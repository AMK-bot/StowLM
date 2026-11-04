"""Pinned, resumable Modal GRPO infrastructure; importing this module starts nothing.

GRPO samples several answers to one prompt, subtracts their mean reward and divides
by their standard deviation, then increases the probability of better answers using
a clipped policy gradient. No separate value network is needed (Shao et al. 2024,
DeepSeekMath §4, https://arxiv.org/abs/2402.03300). D9 requires *both* sampling and
training to normalise over exactly the feasibility mask's allowed tokens. Forced
single-choice tokens have zero gradient and are excluded from the loss.

The smoke driver uses the TRL subclass's masked forward in an explicit, single
update per group GRPO loop (reference KL beta=0), rather than TRL's internal rollout
schema. Two H100s hold bf16 trainer and vLLM separately. The real CUDA path is
UNVERIFIED until the credit-funded smoke; all imports of CUDA/Modal are lazy.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from llm.rl import RL_VERSION
STACK = {"torch": "2.13.0", "transformers": "5.17.0", "vllm": "0.31.0",
         "trl": "1.16.0", "peft": "0.21.2"}
# Explicit image compatibility pins; runtime versions must match this config.
_IMAGE = json.loads(Path(__file__).with_name("training_image.json").read_text())
TRAINING_IMAGE_PINS = {k: v for k, v in _IMAGE.items() if not k.startswith("_")}
TRAINING_IMAGE_SCOPE = _IMAGE.get("_scope", {})  # MVP v4 mask/finisher setting the pins were recorded under
MODEL_PATH = "/weights/Muse-Glimmer-30B@cec94fcf"
# docs/compute.md, 2026-10-27. 1.5 accounts for measured idle/scale-down billing.
RATE_PER_SECOND = 1.5 * (2 * .001097 + 4 * .0000131 + 128 * .00000222)
MASK_KEY = "stowlm_mask"
# One behavior distribution for sampling, replay and checkpoint identity (D9).
ROLLOUT_TEMPERATURE = 1.0


def source_digest(src: Path) -> str:
    """Hash ordered relative paths and source bytes, never pycache or machine paths."""
    digest = hashlib.sha256()
    for path in sorted(p for p in src.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"):
        digest.update(path.relative_to(src).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def current_versions() -> dict[str, str]:
    from llm.stowlm.mask import MASK_VERSION
    from stowage.versions import component_versions
    return {**component_versions(), "mask": MASK_VERSION, "rl": RL_VERSION}


@dataclass(frozen=True)
class PinManifest:
    commit: str
    source_sha256: str
    versions: dict[str, str]
    stack: dict[str, str]
    params_sha256: str | None = None

    @classmethod
    def capture(cls, commit: str, src_dir: Path) -> PinManifest:
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError("a full git commit hash is required")
        return cls(commit, source_digest(src_dir), current_versions(), dict(STACK),
                   source_digest(src_dir.parent / "params") if (src_dir.parent / "params").exists() else None)


def verify_manifest(manifest: PinManifest, src_dir: Path, *, check_stack: bool = False) -> None:
    """Fail before model loading; snapshot includes dirty source via its content hash."""
    if (manifest.versions != current_versions()
            or any(manifest.versions.get(name) != expected for name, expected in TRAINING_IMAGE_PINS.items()
                   if not name.startswith("_"))  # "_"-keys record scope (e.g. MVP mask/finisher setting), not versions
            or manifest.source_sha256 != source_digest(src_dir)):
        raise RuntimeError("training image differs from pinned checker/mask/source manifest")
    params = src_dir.parent / "params"
    if manifest.params_sha256 != (source_digest(params) if params.exists() else None):
        raise RuntimeError("parameter inputs differ from pinned manifest")
    if manifest.stack != STACK:
        raise RuntimeError("CUDA dependency pins differ from manifest")
    if check_stack:
        from importlib.metadata import version
        for name, expected in manifest.stack.items():
            if version(name).split("+")[0] != expected:
                raise RuntimeError(f"unexpected {name} version; expected {expected}")


@dataclass
class SpendCap:
    """Conservative estimated dollar budget, including idle-time multiplier.

    Actual provider invoices are asynchronous; this is an estimated cap, not a
    guarantee about billing. Modal's timeout bounds execution and each step must
    fit its declared upper wall-time before admission. Resume retains prior spend.
    """
    max_dollars: float
    spent_dollars: float = 0.0
    rate_per_second: float = RATE_PER_SECOND

    def __post_init__(self):
        if not all(math.isfinite(v) and v >= 0 for v in
                   (self.max_dollars, self.spent_dollars, self.rate_per_second)) or self.rate_per_second == 0:
            raise ValueError("budget, spend and rate must be finite and nonnegative, with positive rate")
        if self.spent_dollars > self.max_dollars:
            raise ValueError("resume spend exceeds run cap")

    @property
    def timeout_s(self) -> int:
        return math.floor((self.max_dollars - self.spent_dollars) / self.rate_per_second)

    def authorize(self, upper_seconds: float) -> None:
        if not math.isfinite(upper_seconds) or upper_seconds < 0:
            raise ValueError("step upper bound must be finite and nonnegative")
        if upper_seconds * self.rate_per_second > self.max_dollars - self.spent_dollars:
            raise RuntimeError("spend cap: insufficient budget for the next bounded step")

    def charge(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("elapsed time must be finite and nonnegative")
        self.spent_dollars += seconds * self.rate_per_second


def reward_checkpoint_metrics(scores, advantages, *, passed_count: int) -> dict:
    """Persist reward availability beside scores; unknown samples stay JSON null."""
    return {"scores": list(scores), "checker_passed": passed_count,
            "reward_stats": dict(advantages.stats),
            "learning_signal": any(a != 0 for a in advantages),
            "nonzero_advantages": sum(a != 0 for a in advantages)}


class StepCheckpoint:
    """Atomic step directory + latest pointer; commit Volume after complete state.

    The save callback must persist adapter, optimizer and all RNG state before the
    completion marker. Partial directories have no marker and cannot be resumed.
    """
    def __init__(self, root: Path, volume: Any = None):
        self.root, self.volume = Path(root), volume

    def save(self, step: int, state: dict, writer: Callable[[Path], None] | None = None) -> Path:
        if step < 0:
            raise ValueError("negative checkpoint step")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"step-{step:06d}"
        target.mkdir(exist_ok=True)
        if (target / "complete.json").exists():
            raise FileExistsError(f"checkpoint already complete: {target}")
        if writer:
            writer(target)
        temporary = target / "state.tmp"
        temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
        temporary.replace(target / "complete.json")
        latest = self.root / "latest.tmp"
        latest.write_text(target.name)
        latest.replace(self.root / "latest")
        if self.volume is not None:
            self.volume.commit()
        return target

    def load(self) -> tuple[Path, dict] | None:
        if self.volume is not None:
            self.volume.reload()
        def step_number(name):
            suffix = name[5:]
            return int(suffix) if name.startswith("step-") and suffix.isascii() and suffix.isdigit() else None

        pointer = self.root / "latest"
        if pointer.exists() and step_number(pointer.read_text()) is None:
            raise RuntimeError("invalid checkpoint pointer")
        # A preemption can persist the completion marker before the latest
        # pointer. The marker, not that convenience pointer, commits a step.
        candidates = [(step_number(path.name), path) for path in self.root.glob("step-*")
                      if path.is_dir() and not path.is_symlink() and step_number(path.name) is not None
                      and (path / "complete.json").is_file()]
        if not candidates:
            return None
        number, target = max(candidates, key=lambda item: item[0])
        try:
            state = json.loads((target / "complete.json").read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"invalid completed checkpoint: {target.name}") from exc
        if not isinstance(state, dict) or ("step" in state and state["step"] != number):
            raise RuntimeError(f"invalid completed checkpoint state: {target.name}")
        return target, state


def reserve_run_budget(root: Path, manifest: PinManifest, *, cap: float, seconds: int,
                       prior_spend: float, resume: bool, volume: Any = None) -> dict:
    """CPU admission commits full invocation cost BEFORE allocating any GPUs.

    Interrupted invocations retain their entire reservation. The Modal CPU
    admission function is single-container/single-input to serialize this ledger.
    No refunds are inferred from client timers or incomplete GPU checkpoints.
    """
    import uuid
    root = Path(root)
    if volume is not None:
        volume.reload()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "budget.json"
    record = json.loads(path.read_text()) if path.exists() else None
    if record and not resume:
        raise FileExistsError("run id already admitted; use resume or a fresh run id")
    if record and record["manifest"] != asdict(manifest):
        raise RuntimeError("budget ledger manifest mismatch")
    recorded = record["reserved_dollars"] if record else 0.
    if recorded != prior_spend:
        raise RuntimeError("prior-spend must equal the durable budget ledger total")
    budget = SpendCap(cap, recorded)
    budget.authorize(seconds)
    # deadline_epoch: the absolute wall-clock end of this reservation. The GPU function hard-stops there
    # (``hard_stop``), so a resumed run, a restarted incarnation or long reward scoring can't outlive it.
    reservation = {"token": uuid.uuid4().hex, "prior_spend": recorded, "deadline_epoch": time.time() + seconds,
                   "reserved_dollars": recorded + seconds * budget.rate_per_second,
                   "seconds": seconds, "started": False, "manifest": asdict(manifest)}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(reservation, sort_keys=True) + "\n")
    temporary.replace(path)
    if volume is not None:
        volume.commit()
    return reservation


def hard_stop(deadline_epoch: float, *, exit_code: int = 43, clock: Callable[[], float] = time.time,
              sleep: Callable[[float], None] = time.sleep, exit_fn: Callable[[int], None] = os._exit,
              start: bool = True):
    """Kill this process at an absolute wall-clock deadline, whatever it is doing.

    A soft ``deadline`` check runs only between steps, so sequential reward scoring (~30 s per completed plan),
    model loading or checkpoint writing could run past the reservation (review w2-rev-grpo). This watchdog thread
    has no such gaps. Started first in the GPU function, before imports and model work, with the reservation's own
    ``deadline_epoch``, it bounds every incarnation (resume, restart after preemption) by the reserved seconds.
    Returns the watch loop (for tests, ``start=False``).
    """
    import threading
    if not math.isfinite(deadline_epoch):
        raise ValueError("finite deadline required")

    def watch():
        while True:
            if clock() >= deadline_epoch:
                print("hard stop: reservation deadline reached", flush=True)
                exit_fn(exit_code)
                return
            sleep(1.0)
    if start:
        threading.Thread(target=watch, daemon=True).start()
    return watch


def consume_reservation(root: Path, reservation: dict, volume: Any = None) -> None:
    """Reject duplicate/stale GPU invocation tokens, including automatic retries."""
    if volume is not None:
        volume.reload()
    path = Path(root) / "budget.json"
    record = json.loads(path.read_text())
    if record != reservation or record["started"]:
        raise RuntimeError("invalid, stale or already consumed GPU budget reservation")
    record["started"] = True
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True) + "\n")
    temporary.replace(path)
    if volume is not None:
        volume.commit()


def masked_statistics(logits, targets, allowed, *, temperature: float = 1.0):
    """CPU reference for mask-normalised selected log-probabilities and entropy."""
    import numpy as np
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("positive finite temperature required")
    if len(logits) != len(targets) or len(targets) != len(allowed):
        raise ValueError("one allowed set per prediction is required")
    logps, entropies = [], []
    for row, target, ids in zip(logits, targets, allowed, strict=True):
        ids = tuple(ids)
        if not ids or len(set(ids)) != len(ids) or target not in ids:
            raise ValueError("invalid allowed set or forbidden sampled token")
        z = np.asarray(row, dtype=float)[list(ids)] / temperature
        z -= z.max()
        lp = z - np.log(np.exp(z).sum())
        logps.append(float(lp[ids.index(target)]))
        entropies.append(float(-(np.exp(lp) * lp).sum()))
    return np.asarray(logps), np.asarray(entropies)


_TOKENIZERS: dict[str, Any] = {}


def decoder_from_spec(spec: dict):
    """One decoder per request. The tokenizer is the planner's own ``tokenizers`` file loader (same codec as
    ``StowLMPlanner``), cached per path so admitting a request doesn't re-read it. ``feasibility_extra`` is passed to
    ``FeasibilityConfig.extra`` unchanged (e.g. the labelled C2-off ``{"global_screen": 0}`` the 6.2 pilot uses while
    the 3.7 linear policy is stale); the default engine is used when it is absent."""
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig, MaskedDecoder
    from stowage.model import Instance
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.actions import get_action_space
    # The instance travels as a JSON string: vLLM validates every integer in SamplingParams.extra_args against the
    # int64 range, and instances carry larger ones (falsifier 4, 29 Oct: "extra_args integers must be between ...").
    raw = spec["instance"]
    instance = Instance.model_validate_json(raw) if isinstance(raw, str) else Instance.model_validate(raw)
    path = spec.get("tokenizer_path", MODEL_PATH)
    if path not in _TOKENIZERS:
        _TOKENIZERS[path] = load_tokenizer(path)
    tokenizer = _TOKENIZERS[path]
    extra = spec.get("feasibility_extra")
    engine = (make_feasibility_engine(instance, get_action_space()) if extra is None else
              make_feasibility_engine(instance, get_action_space(), config=FeasibilityConfig(extra=dict(extra))))
    if spec.get("securing"):
        # D95: the necessary deck-securing envelope over the unchanged production mask, exactly as the MVP
        # evaluation composes it (llm.securing.securing_decoder_factory -> wrap_engine -> MaskedDecoder).
        from llm.securing.mask_filter import wrap_engine
        engine = wrap_engine(engine)
    return MaskedDecoder(engine, ActionCodec(tokenizer, [c.id for c in instance.containers]),
                         MaskConfig(**spec.get("config", {})))


def make_mask_processor(decoder_factory: Callable = decoder_from_spec):
    """vLLM 0.31 Model Runner V2 factory (``vllm/v1/worker/gpu/sample/logits_processor/interface.py``).

    vLLM 0.31 defaults to Model Runner V2, whose loader rejects the older V1 ``vllm.v1.sample.logits_processor``
    ABC (1.9 smoke attempt 1, 29 Oct). V2 contract: ``add_request(slot, params) -> bool`` (False = never touch this
    request; slots are recycled, so state is always reinitialised), ``apply(logits, ctx)`` once per batch with rows
    mapped to slots by ``ctx.expanded_idx_mapping`` (rows reorder every step). The decoder is host Python, so each
    step copies the row -> slot map, the fed tokens and positions to the host (one sync). A row's fed token is the
    previously sampled token once ``pos >= prompt_len``; the first output position feeds the last prompt token and
    advances nothing. ``last_pos`` makes advancing idempotent. Completion/dead-end EOS is a transport sentinel only,
    never an action: it is stripped before replay/reward, and dead ends receive the fixed failure penalty.
    ``stats`` (class level) records host time inside ``apply`` and calls so callers can report the mask host share;
    ``decoders`` maps each request's ``spec["rid"]`` (if given) to its decoder for status read-back.
    """
    import torch
    from vllm.v1.worker.gpu.sample.logits_processor import LogitsProcessor

    class StowMaskLP(LogitsProcessor):
        stats = {"apply_calls": 0, "apply_s": 0.0, "rows": 0, "advances": 0}
        decoders: dict = {}

        @classmethod
        def validate_params(cls, params):
            spec = (params.extra_args or {}).get(MASK_KEY)
            if spec is not None and (not isinstance(spec, dict) or "instance" not in spec
                                     or "eos_token_id" not in spec):
                raise ValueError("every masked rollout requires instance and EOS mask metadata")

        def __init__(self, vllm_config, req_states):
            self.rs, self.slots = req_states, {}

        def add_request(self, req_idx, params):
            spec = (params.extra_args or {}).get(MASK_KEY)
            if spec is None:
                self.slots.pop(req_idx, None)
                return False
            self.validate_params(params)
            decoder = decoder_factory(spec)
            if "rid" in spec:
                StowMaskLP.decoders[spec["rid"]] = decoder
            self.slots[req_idx] = {"decoder": decoder, "eos": spec["eos_token_id"], "last_pos": -1}
            return True

        def apply(self, logits, ctx):
            started = time.perf_counter()
            slots = ctx.expanded_idx_mapping.tolist()
            if not any(slot in self.slots for slot in slots):
                return logits
            fed, positions = ctx.input_ids.tolist(), ctx.pos.tolist()
            prompt_len = self.rs.prompt_len.np
            rows, cols, masked = [], [], []
            for row, (slot, token, pos) in enumerate(zip(slots, fed, positions)):
                state = self.slots.get(slot)
                if state is None:
                    continue
                dec = state["decoder"]
                if pos >= int(prompt_len[slot]) and pos > state["last_pos"]:
                    if token < 0:
                        raise RuntimeError("async scheduling placeholders unsupported")
                    if dec.allowed_token_ids():  # after completion only the transport EOS is fed
                        dec.advance(token)
                        StowMaskLP.stats["advances"] += 1
                state["last_pos"] = max(state["last_pos"], pos)
                allowed = dec.allowed_token_ids() or (state["eos"],)  # terminal sentinel, not a policy token
                rows.extend([row] * len(allowed))
                cols.extend(allowed)
                masked.append(row)
            keep = torch.zeros(logits.shape, dtype=torch.bool, device=logits.device)
            keep[torch.tensor(rows, device=logits.device), torch.tensor(cols, device=logits.device)] = True
            on = torch.zeros(logits.shape[0], dtype=torch.bool, device=logits.device)
            on[torch.tensor(masked, device=logits.device)] = True
            logits.masked_fill_(on[:, None] & ~keep, float("-inf"))
            StowMaskLP.stats["apply_calls"] += 1
            StowMaskLP.stats["rows"] += len(masked)
            StowMaskLP.stats["apply_s"] += time.perf_counter() - started
            return logits
    return StowMaskLP


_MASK_CLS = None


def __getattr__(name):
    # vLLM imports this class by qualified name, without eager CUDA imports on the
    # local machine or during repository import sweeps. Cached, so the worker reads
    # the same class's ``stats``/``decoders`` that the engine instantiated.
    global _MASK_CLS
    if name == "StowMaskLP":
        if _MASK_CLS is None:
            _MASK_CLS = make_mask_processor()
        return _MASK_CLS
    raise AttributeError(name)


def _generate_batch(engine, request: dict) -> dict:
    """Worker side of a batched rollout request: one ``generate`` over every (prompt, sample) item.

    Each item is its own request (own ``rid`` and seed), so the processor keeps one decoder per sample and prefix
    caching shares each prompt's prefill. ``spec=None`` is an unmasked control (``min_tokens`` = ``max_tokens``,
    EOS ignored) for forward-only throughput at the same batch size.
    """
    from vllm import SamplingParams
    cls = __getattr__("StowMaskLP")
    before = dict(cls.stats)
    params, prompts = [], []
    for item in request["batch"]:
        spec = item.get("spec")
        common = dict(temperature=ROLLOUT_TEMPERATURE, max_tokens=item["max_tokens"], seed=item["seed"], logprobs=1)
        if spec is None:
            params.append(SamplingParams(min_tokens=item["max_tokens"], ignore_eos=True, **common))
        else:
            params.append(SamplingParams(stop_token_ids=[spec["eos_token_id"]], extra_args={MASK_KEY: spec},
                                         **common))
        prompts.append({"prompt_token_ids": item["prompt_ids"]})
    started = time.perf_counter()
    results = engine.generate(prompts, params, use_tqdm=False)
    wall = time.perf_counter() - started
    out = {"tokens": [], "logps": [], "status": [], "actions": [], "wall_s": wall,
           "mask": {k: cls.stats[k] - before[k] for k in before}}
    for item, result in zip(request["batch"], results, strict=True):
        x = result.outputs[0]
        out["tokens"].append(list(x.token_ids))
        out["logps"].append([float(lp[t].logprob) for t, lp in zip(x.token_ids, x.logprobs, strict=True)])
        decoder = cls.decoders.pop((item.get("spec") or {}).get("rid"), None)
        # The plan terminator <|eot|> is also the stop token, so vLLM stops on it before the processor sees it
        # fed back (falsifier run, 29 Oct). Advance it here so the read-back status matches a replay.
        if decoder is not None and x.token_ids and x.token_ids[-1] in decoder.allowed_token_ids():
            decoder.advance(x.token_ids[-1])
        out["status"].append(None if decoder is None else decoder.status)
        out["actions"].append(None if decoder is None else len(decoder.actions))
    return out


def make_masked_grpo_trainer(allowed_for_row: Callable):
    """TRL 1.16 subclass: gather allowed head rows instead of full-vocab logits.

    ``allowed_for_row(ids, attention_mask, completion_length)`` must replay D9
    using prompt metadata and remove padding. Returned sets include forced tokens.
    Glimmer's multiplier and softcap precede temperature, just as in vLLM.
    """
    import torch
    from trl import GRPOTrainer

    class MaskedGRPOTrainer(GRPOTrainer):
        def _get_per_token_logps_and_entropies(
            self, model, input_ids, attention_mask, logits_to_keep, batch_size=None,
            compute_entropy=False, compute_aux_loss=False, **mm_kwargs,
        ):
            if compute_aux_loss or any(v is not None for v in mm_kwargs.values()):
                raise NotImplementedError("text-only Glimmer GRPO; no auxiliary loss")
            if logits_to_keep <= 0:
                raise ValueError("completion must contain at least one policy token")
            all_lp, all_ent = [], []
            # One sequence at a time: bounded activation memory, no B×L×V tensor.
            for i in range(input_ids.shape[0]):
                ids, am = input_ids[i:i+1], attention_mask[i:i+1]
                sets = allowed_for_row(ids[0].tolist(), am[0].tolist(), logits_to_keep)
                if len(sets) != logits_to_keep:
                    raise ValueError("mask replay length differs from completion")
                base = model.get_base_model() if hasattr(model, "get_base_model") else model
                hidden = base.model(input_ids=ids, attention_mask=am, use_cache=False).last_hidden_state
                hidden = hidden[0, -logits_to_keep-1:-1]
                head = base.get_output_embeddings().weight
                config = getattr(base.config, "text_config", base.config)
                multiplier = getattr(config, "output_multiplier", 1.0)
                softcap = getattr(config, "final_logit_softcapping", None)
                lp, ent = [], []
                targets = ids[0, -logits_to_keep:].tolist()
                for h, target, allowed in zip(hidden, targets, sets, strict=True):
                    allowed = tuple(allowed)
                    if not allowed or target not in allowed or len(set(allowed)) != len(allowed):
                        raise ValueError("forbidden policy token or invalid mask replay")
                    index = torch.tensor(allowed, device=head.device, dtype=torch.long)
                    z = torch.nn.functional.linear(h, head.index_select(0, index)).float() * multiplier
                    if softcap:
                        z = softcap * torch.tanh(z / softcap)
                    z = z / self.temperature
                    logp = z - torch.logsumexp(z, dim=-1)
                    lp.append(logp[allowed.index(target)])
                    if compute_entropy:
                        ent.append(-(logp.exp() * logp).sum())
                all_lp.append(torch.stack(lp))
                if compute_entropy:
                    all_ent.append(torch.stack(ent))
            return torch.stack(all_lp), (torch.stack(all_ent) if compute_entropy else None), None
    return MaskedGRPOTrainer


# MVP v4 rollout/scoring configuration (D79 C2 off, D95 securing layer, D88 V4 finisher): the same mask and
# finisher the MVP evaluation uses, so GRPO optimises the behaviour that is evaluated (logic-grpo).
MVP_SPEC = {"feasibility_extra": {"global_screen": 0}, "securing": True}


def mvp_engine(instance):
    """The replay/scoring engine matching ``decoder_from_spec`` with ``MVP_SPEC``."""
    from llm.securing.mask_filter import wrap_engine
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    engine = make_feasibility_engine(instance, get_action_space(),
                                     config=FeasibilityConfig(extra=dict(MVP_SPEC["feasibility_extra"])))
    return wrap_engine(engine) if MVP_SPEC["securing"] else engine


def mvp_reward_config():
    from llm.rl.reward import RewardConfig
    return RewardConfig(ballast_v4_finalize=True)


def score_rollout(instance, engine, codec, tokens, *, seed: int, config=None):
    """Replay policy tokens, stripping transport EOS; distinguish dead end/timeout.

    A token cap is an unfinished plan, never a successful checker candidate. This
    CPU function shares the exact same replay path as the training log-prob mask.
    """
    from llm.rl.reward import evaluate_reward
    from llm.stowlm.mask import MaskedDecoder, allowed_sets
    from stowage.model import PlannerResult
    decoder = MaskedDecoder(engine.copy(), codec)
    actual = []
    for token in tokens:
        if not decoder.allowed_token_ids():
            break
        decoder.advance(token)
        actual.append(token)
    decoder.allowed_token_ids()
    status = "ok" if decoder.status == "complete" else (
        "dead_end" if decoder.status == "dead_end" else "timeout")
    # The engine builds a plan only from a complete action sequence (to_plan raises otherwise; found on the
    # falsifier's dead-end rollouts, 29 Oct). Dead ends/timeouts are scored by status alone (fixed penalties).
    result = PlannerResult(status=status, plan=decoder.engine.to_plan() if status == "ok" else None, seed=seed,
                           planner="stowlm.grpo", planner_version=RL_VERSION)
    outcome = evaluate_reward(instance, result, dead_end=decoder.status == "dead_end",
                              **({"config": config} if config is not None else {}))
    return actual, allowed_sets(engine, codec, actual), outcome


def score_rollout_job(job: tuple) -> dict:
    """Process-pool unit of reward scoring: ``(instance_json_text, tokens, seed, tokenizer_path)`` -> outcome summary.

    Top level (picklable under spawn) so a GRPO step scores its group in parallel: one completed tiny plan costs
    ~15-38 s of CPU in the V4 finisher + full checker (falsifier 4, 29 Oct). Same MVP engine and reward config as
    the sampler's mask (``mvp_engine``, ``mvp_reward_config``).
    """
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from stowage.model import Instance
    text, tokens, seed, tokenizer_path = job
    started = time.perf_counter()
    instance = Instance.model_validate_json(text)
    if tokenizer_path not in _TOKENIZERS:
        _TOKENIZERS[tokenizer_path] = load_tokenizer(tokenizer_path)
    codec = ActionCodec(_TOKENIZERS[tokenizer_path], [c.id for c in instance.containers])
    actual, sets, outcome = score_rollout(instance, mvp_engine(instance), codec, tokens, seed=seed,
                                          config=mvp_reward_config())
    return {"actual": list(actual), "sets": [list(x) for x in sets], "value": outcome.value,
            "passed": bool(outcome.passed), "reason": outcome.reason, "moves": outcome.weighted_nonproductive,
            "diagnostic": (outcome.diagnostic or "")[:300] if hasattr(outcome, "diagnostic") else "",
            "score_s": round(time.perf_counter() - started, 2)}


def initialize_policy_adapter(base, sft_adapter: str | Path | None = None):
    """Load a converted, local SFT adapter; fresh LoRA remains the base smoke default."""
    if sft_adapter is not None:
        from peft import PeftModel
        return PeftModel.from_pretrained(base, str(sft_adapter), is_trainable=True,
                                         local_files_only=True)
    from peft import LoraConfig, get_peft_model
    regex = r"model\.language_model\.layers\.\d+\.(self_attn\.(q_proj|k_proj|v_proj|o_proj|gate_proj)|mlp\.(gate_proj|up_proj|down_proj))"
    return get_peft_model(base, LoraConfig(r=8, lora_alpha=16, target_modules=regex,
                                         lora_dropout=0, bias="none", task_type="CAUSAL_LM"))


def apply_exported_weights(engine, weight_file: str | Path) -> dict:
    """Worker side of production synchronization, also used by CPU regression fakes."""
    import torch
    weights = torch.load(weight_file, map_location="cpu", weights_only=True)
    engine.apply_model(lambda model: model.load_weights(list(weights.items())))
    # Cached prompt KV was computed with the previous weights; reusing it would make the sampler's behavior
    # distribution differ from the trainer's (stale prefix), so drop it after every synchronization.
    if hasattr(engine, "reset_prefix_cache"):
        engine.reset_prefix_cache()
    return {"synced": True, "tensors": len(weights)}


def merged_weights_from_checkpoint(base_dir: str | Path, adapter_dir: str | Path) -> dict:
    """``W + (alpha/r) * B @ A`` for every module a PEFT LoRA adapter targets, read from the base safetensors on CPU.

    The same out-of-place fp32 merge as ``merged_lora_weights`` (rounded once to the base dtype), but with no
    trainer model: the rollout worker can start from an SFT adapter (falsifier 4; 10.1's first sync) on one GPU.
    Keys are the checkpoint's own names (``model.language_model.layers.N.<sub>.weight``), as ``load_weights``
    expects. Only plain LoRA (no rsLoRA/DoRA) is supported.
    """
    import torch
    from safetensors import safe_open
    from safetensors.torch import load_file
    base_dir, adapter_dir = Path(base_dir), Path(adapter_dir)
    cfg = json.loads((adapter_dir / "adapter_config.json").read_text())
    if cfg.get("use_rslora") or cfg.get("use_dora"):
        raise NotImplementedError("rsLoRA/DoRA adapters are not supported")
    scaling = float(cfg["lora_alpha"]) / float(cfg["r"])
    lora = load_file(str(adapter_dir / "adapter_model.safetensors"))
    prefix = "base_model.model."
    modules = sorted({k[len(prefix):].rsplit(".lora_", 1)[0] for k in lora if k.startswith(prefix)})
    index = json.loads((base_dir / "model.safetensors.index.json").read_text())["weight_map"]
    out, handles = {}, {}
    try:
        for module in modules:
            name = f"{module}.weight"
            if "vision" in name or name not in index:
                raise KeyError(f"adapter targets {name}, absent from the base checkpoint (or a vision module)")
            shard = index[name]
            if shard not in handles:
                handles[shard] = safe_open(str(base_dir / shard), framework="pt")
            weight = handles[shard].get_tensor(name)
            a = lora[f"{prefix}{module}.lora_A.weight"].float()
            b = lora[f"{prefix}{module}.lora_B.weight"].float()
            out[name] = (weight.float() + (b @ a) * scaling).to(weight.dtype)
    finally:
        handles.clear()
    return out


def merged_lora_weights(model) -> dict:
    """Merged ``W + scaling * B @ A`` for every LoRA-wrapped linear, computed OUT OF PLACE and cast once.

    PEFT's ``merge_adapter``/``unmerge_adapter`` add and then subtract the delta in the base dtype. In bf16 both steps
    round, so the frozen base doesn't come back bit-identical (review w2-rev-grpo: 1 -> 0.99609375 after one sync).
    The trainer's base would then drift away from the pretrained weights that adapter-only checkpoints assume. Here
    the base is only read: ``W`` and the delta go to fp32, are summed, and the sum is rounded to the base dtype once
    for the sampler. Only LoRA-wrapped modules change, so only they are exported. Exporting every language weight
    (~56 GB bf16 for Glimmer 30B) per step wouldn't fit a 128 GiB container's RAM; the toy adapter's 16 layers are
    ~15.6 GB. Vision modules are never exported.
    """
    import torch
    out = {}
    with torch.no_grad():
        for name, module in model.named_modules():
            if "vision" in name or not hasattr(module, "base_layer") or not hasattr(module, "lora_A"):
                continue
            adapters = [a for a in getattr(module, "active_adapters", ()) if a in module.lora_A]
            if not adapters:
                continue
            if getattr(module, "merged", False):
                raise RuntimeError(f"{name}: adapter is merged in place; the frozen base is no longer pristine")
            if any(getattr(module, "use_dora", {}).get(a, False) for a in adapters):
                raise NotImplementedError("DoRA adapters are not supported by the out-of-place export")
            weight = module.base_layer.weight
            merged = weight.detach().float().clone()
            for a in adapters:
                delta = module.lora_B[a].weight.detach().float() @ module.lora_A[a].weight.detach().float()
                if getattr(module, "fan_in_fan_out", False):
                    delta = delta.T
                merged += delta * float(module.scaling[a])
            out[name.replace("base_model.model.", "", 1) + ".weight"] = merged.to(weight.dtype).cpu()
    return out


def sync_policy_weights(model, connection, deadline: float, *,
                        weight_file: str | Path = "/tmp/stowlm-sync.pt") -> None:
    """Export merged LoRA weights (out of place) → worker apply_model(load_weights) → prefix-cache reset → ack.

    Runs even at step zero. The acknowledgment is required before rollout; the transient export is always removed.
    The trainer's parameters are never modified here.
    """
    import torch
    weight_file = Path(weight_file)
    try:
        weights = merged_lora_weights(model)
        if not weights:
            raise RuntimeError("no LoRA-wrapped weights to synchronize")
        torch.save(weights, weight_file)
        connection.send({"weights": str(weight_file)})
        if _receive(connection, deadline - time.monotonic()).get("synced") is not True:
            raise RuntimeError("rollout worker did not acknowledge policy synchronization")
    finally:
        weight_file.unlink(missing_ok=True)


def grpo_rollout_loss(current, old, advantage: float, decision, *, group_size: int, clip: float = .2,
                      record: dict | None = None):
    """Clipped GRPO surrogate for one rollout, normalised over decision tokens only (D9).

    Forced positions (one legal token, log-prob 0) carry no gradient and are excluded from the mean, so instances
    with many forced tokens aren't diluted. ``record`` (optional) receives the decision/forced counts actually used,
    so callers can assert which normalisation ran.
    """
    import torch
    mask = torch.tensor([list(decision)], device=current.device, dtype=torch.bool)
    n_decision = int(mask.sum())
    if n_decision == 0:
        raise ValueError("rollout has no decision tokens")
    ratio = (current - old.detach()).exp()
    adv = torch.tensor(advantage, device=ratio.device, dtype=ratio.dtype)
    loss = -torch.minimum(ratio * adv, ratio.clamp(1 - clip, 1 + clip) * adv)[mask].sum() / n_decision / group_size
    if record is not None:
        record.update(calls=record.get("calls", 0) + 1, decision=record.get("decision", 0) + n_decision,
                      forced=record.get("forced", 0) + int(mask.numel()) - n_decision)
    return loss


# One sampler/trainer agreement gate for production and the falsifiers (D26 "Agreement gate", leader 29 Oct). A
# base-only control (no adapter, no sync) reaches 0.316 nat per-position max between bf16 HF (sdpa) and vLLM
# (FlashAttention-3, compiled), so a 0.1-nat max is below the kernels' own floor. Gate, over every compared position:
# mean <= 0.01, p99 <= 0.1, max <= 0.5 nat. The base-only floor is measured in the same run (``measure_agreement_floor``)
# and must itself pass the gate; the synced path must stay within it. "Within" is read as mean and p99 at most
# AGREEMENT_FLOOR_SLACK x the floor's (max is gated absolutely: at ~3k positions it is one draw, too noisy to compare).
# Recorded F2 (29 Oct): floor mean 0.0068 / p99 0.097 / max 0.316; synced 0.0064-0.0072 / 0.072-0.076 / 0.224-0.367.
AGREEMENT_GATE = {"mean_abs_nats": .01, "p99_abs_nats": .1, "max_abs_nats": .5}
AGREEMENT_FLOOR_SLACK = 2.0


def agreement_stats(pairs) -> dict:
    """Error statistics over every position of ``(trainer_logps, sampler_logps)`` rollout pairs.

    Nothing is dropped: a shape mismatch, an empty rollout or any nonfinite value on either side raises.
    """
    import numpy as np
    diffs = []
    for trainer, sampler in pairs:
        trainer, sampler = np.asarray(trainer, dtype=float), np.asarray(sampler, dtype=float)
        if (trainer.shape != sampler.shape or trainer.size == 0
                or not np.isfinite(trainer).all() or not np.isfinite(sampler).all()):
            raise RuntimeError("vLLM/trainer masked log-prob mismatch: invalid values or shape")
        diffs.append(np.abs(trainer - sampler).ravel())
    if not diffs:
        raise RuntimeError("vLLM/trainer agreement: no positions to compare")
    d = np.concatenate(diffs)
    return {"positions": int(d.size), "mean_abs_nats": float(d.mean()),
            "p99_abs_nats": float(np.quantile(d, .99)), "max_abs_nats": float(d.max())}


def agreement_failures(stats: dict, floor: dict | None = None) -> list[str]:
    """Reasons ``stats`` fails the agreement gate (empty list = pass). With ``floor``, the base-only floor must pass
    the gate too, and the synced mean/p99 must stay within AGREEMENT_FLOOR_SLACK x the floor's (with a resolution
    of 1e-3 / 1e-2 nat so a near-zero floor can't demand the impossible)."""
    fails = [f"{k} {stats[k]:.4g} > {v}" for k, v in AGREEMENT_GATE.items() if stats[k] > v]
    if floor is not None:
        fails += [f"base floor {k} {floor[k]:.4g} > {v}" for k, v in AGREEMENT_GATE.items() if floor[k] > v]
        for key, resolution in (("mean_abs_nats", 1e-3), ("p99_abs_nats", 1e-2)):
            bound = AGREEMENT_FLOOR_SLACK * max(floor[key], resolution)
            if stats[key] > bound:
                fails.append(f"{key} {stats[key]:.4g} > {AGREEMENT_FLOOR_SLACK:g} x base floor {floor[key]:.4g}")
    return fails


def assert_masked_agreement(trainer_logps, sampler_logps, *,
                            tolerance: float = AGREEMENT_GATE["max_abs_nats"]) -> float:
    """Per-rollout hard check (stale/missing/nonfinite sampler values, or any position above the gate's max);
    returns the rollout's maximum error. The mean/p99/floor parts of the gate are checked per step."""
    import numpy as np
    stats = agreement_stats([(trainer_logps, sampler_logps)])
    if stats["max_abs_nats"] > tolerance:
        raise RuntimeError(f"vLLM/trainer masked log-prob mismatch: {stats['max_abs_nats']}")
    return float(np.float64(stats["max_abs_nats"]))


def measure_agreement_floor(model, trainer, connection, *, prompt, spec, engine, codec, group_size: int,
                            max_tokens: int, deadline: float, device: str = "cuda:0") -> dict:
    """Base-only control, measured in the same run: the worker still holds the base weights (call this before the
    first sync), the trainer runs with its adapter disabled, so the remaining error is the kernels' own floor.
    Raises if the floor itself fails the gate."""
    import torch
    from llm.stowlm.mask import MaskedDecoder, allowed_sets
    connection.send({"prompt_ids": list(prompt), "spec": spec, "group_size": group_size,
                     "max_tokens": max_tokens, "seed": -1})
    sampled = _receive(connection, deadline - time.monotonic())
    pairs = []
    for tokens, sampler in zip(sampled["tokens"], sampled["logps"], strict=True):
        decoder, actual = MaskedDecoder(engine.copy(), codec), []
        for token in tokens:  # strip the transport EOS, as score_rollout does
            if not decoder.allowed_token_ids():
                break
            decoder.advance(token)
            actual.append(token)
        if not actual:
            continue
        sets = allowed_sets(engine, codec, actual)
        trainer.allowed_override = sets
        ids = torch.tensor([list(prompt) + actual], device=device)
        with torch.no_grad(), model.disable_adapter():
            lp, _, _ = trainer._get_per_token_logps_and_entropies(model, ids, torch.ones_like(ids), len(actual))
        pairs.append((lp[0].float().cpu().numpy(), sampler[:len(actual)]))
    floor = agreement_stats(pairs)
    fails = agreement_failures(floor)
    if fails:
        raise RuntimeError(f"base-only agreement floor fails the gate: {fails}")
    return floor


def _rollout_process(connection, cuda_device: str = "1"):
    """Spawned process owns one GPU (GPU1 beside a GPU0 trainer; GPU0 on a rollout-only 1-GPU container)."""
    os.environ["CUDA_VISIBLE_DEVICES"] = cuda_device
    os.environ["VLLM_ENABLE_V1_MULTIPROCESSING"] = "0"
    try:
        from vllm import LLM, SamplingParams
        engine = LLM(model=MODEL_PATH, dtype="bfloat16", trust_remote_code=False,
                     hf_overrides={"architectures": ["MuseGlimmerForCausalLM"]},
                     logits_processors=["llm.rl.modal_grpo:StowMaskLP"],  # Model Runner V2 class
                     language_model_only=True, logprobs_mode="processed_logprobs", async_scheduling=False, max_model_len=4096, gpu_memory_utilization=.85)
        connection.send({"ready": True})
        while True:
            request = connection.recv()
            if request is None:
                break
            if "weights" in request:
                started = time.perf_counter()
                connection.send({**apply_exported_weights(engine, request["weights"]),
                                 "load_s": time.perf_counter() - started})
                continue
            if "batch" in request:
                connection.send(_generate_batch(engine, request))
                continue
            params = SamplingParams(n=request["group_size"], temperature=ROLLOUT_TEMPERATURE,
                max_tokens=request["max_tokens"], seed=request["seed"],
                stop_token_ids=[request["spec"]["eos_token_id"]],
                extra_args={MASK_KEY: request["spec"]}, logprobs=1)
            result = engine.generate([{"prompt_token_ids": request["prompt_ids"]}], params)[0]
            connection.send({"tokens": [list(x.token_ids) for x in result.outputs],
                "logps": [[float(lp[t].logprob) for t, lp in zip(x.token_ids, x.logprobs, strict=True)]
                          for x in result.outputs]})
    except BaseException as exc:
        connection.send({"error": repr(exc)})
    finally:
        connection.close()


def _receive(connection, timeout: float):
    if not connection.poll(max(0, timeout)):
        raise TimeoutError("bounded rollout worker timed out")
    result = connection.recv()
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def run_gpu_smoke(instance_json: dict, manifest: PinManifest, *, checkpoint_root: str,
                  volume: Any, cap: float, steps: int = 1, group_size: int = 2,
                  max_tokens: int = 256, step_timeout_s: int = 600,
                  resume: bool = False, reservation: dict | None = None,
                  sft_adapter: str | Path | None = None) -> dict:
    """Explicit clipped GRPO loop, bf16 base + converted SFT or fresh PEFT adapter.

    GPU execution is deliberately available only through the documented Modal
    command. CPU rewards run in this process with GPU tensors released between
    forwards; the reward module itself has no GPU dependency and can be remoted.
    """
    if reservation is None:
        raise RuntimeError("GPU invocation needs a durable CPU admission reservation")
    consume_reservation(Path(checkpoint_root), reservation, volume)
    import multiprocessing
    import random
    import numpy as np
    import torch
    from transformers import AutoTokenizer, MuseGlimmerForConditionalGeneration
    from llm.rl.reward import (RewardConfig, advance_timeout_policy, dropped_sample_diagnostics,
                              normalize_group_rewards, require_timeout_admission)
    from llm.rl.lineage import rollout_data_identity
    from llm.stowlm.format import ActionCodec
    from llm.stowlm.format_prompt import encode_prompt
    from llm.stowlm.mask import allowed_sets
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.model import Instance
    from stowage.frozen import require_clean

    verify_manifest(manifest, Path("/root/stowlm/src"), check_stack=True)
    if steps < 1 or group_size < 2 or not 1 <= max_tokens <= 2048 or step_timeout_s < 1:
        raise ValueError("invalid smoke dimensions")
    instance = Instance.model_validate(instance_json)
    require_clean(instance, what="GRPO training prompt")
    budget = SpendCap(cap, reservation["prior_spend"])
    checkpoints = StepCheckpoint(Path(checkpoint_root), volume)
    previous = checkpoints.load()
    if previous and not resume:
        raise FileExistsError("run exists; request resume or choose another run id")
    begin, restored = 0, None
    if previous:
        path, state = previous
        if state["manifest"] != asdict(manifest) or state["instance"] != instance_json:
            raise RuntimeError("resume manifest/prompt mismatch")
        begin, restored = state["step"], path
        if state.get("timeout_policy"):
            require_timeout_admission(state["timeout_policy"], lambda: None)
    timeout_policy = previous[1].get("timeout_policy") if previous else None
    training_config = {"group_size": group_size, "max_tokens": max_tokens, "temperature": ROLLOUT_TEMPERATURE,
                       "learning_rate": 1e-5, "clip": .2, "beta": 0., "reward": RL_VERSION, "step_timeout_s": step_timeout_s,
                       "rollout_data_identity": rollout_data_identity(rule_catalogue=manifest.versions.get("rule_catalogue")),
                       # Same mask and finisher budget as gold and evaluation (logic-grpo): the default engine with
                       # C2 on (no feasibility_extra), and the reward's pinned base/retry finisher budgets.
                       "mask_engine": dict(MVP_SPEC),
                       "reward_config": asdict(mvp_reward_config())}
    if sft_adapter is not None:
        adapter_path = Path(sft_adapter)
        if not (adapter_path / "adapter_config.json").is_file() or not (adapter_path / "adapter_model.safetensors").is_file():
            raise ValueError("SFT adapter must be a local converted PEFT directory")
        training_config["sft_adapter_sha256"] = source_digest(adapter_path)
    if previous and state.get("training_config") != training_config:
        raise RuntimeError("resume training configuration differs from checkpoint")
    budget.authorize(step_timeout_s)
    started = time.monotonic()
    run_deadline = started + min(reservation["seconds"], reservation["deadline_epoch"] - time.time())
    initial_deadline = min(run_deadline, started + step_timeout_s)
    random.seed(0); np.random.seed(0); torch.manual_seed(0)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    codec = ActionCodec(tokenizer, [c.id for c in instance.containers])
    prompt = encode_prompt(instance, tokenizer)
    if len(prompt) + max_tokens > 4096:
        raise ValueError("smoke prompt exceeds 4096 tokens; use a tiny fixture")
    engine = mvp_engine(instance)
    if engine.dead_end() is not None or engine.is_complete():
        raise RuntimeError("smoke requires a feasible, nonempty loading problem")
    eos = tokenizer.eos_token_id
    if eos is None:
        raise ValueError("tokenizer must define a terminal transport token")
    # Same mask as the MVP evaluation; the instance as a JSON string (vLLM's extra_args int64 validation).
    spec = {"instance": json.dumps(instance_json), "eos_token_id": eos, **MVP_SPEC}
    # Child is spawned before parent model allocation and owns only GPU1.
    parent, child = multiprocessing.get_context("spawn").Pipe()
    process = multiprocessing.get_context("spawn").Process(target=_rollout_process, args=(child,))
    process.start()
    child.close()
    try:
        _receive(parent, initial_deadline - time.monotonic())
        torch.cuda.set_device(0)
        base = MuseGlimmerForConditionalGeneration.from_pretrained(
            MODEL_PATH, dtype=torch.bfloat16, device_map={"": 0}, local_files_only=True,
            attn_implementation="sdpa")
        model = initialize_policy_adapter(base, sft_adapter)
        model.train()
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
        optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=1e-5)
        if restored:
            from peft import set_peft_model_state_dict
            snapshot = torch.load(restored / "training.pt", map_location="cpu", weights_only=False)
            set_peft_model_state_dict(model, snapshot["adapter"])
            optimizer.load_state_dict(snapshot["optimizer"])
            random.setstate(snapshot["python_rng"]); np.random.set_state(snapshot["numpy_rng"])
            torch.set_rng_state(snapshot["torch_rng"]); torch.cuda.set_rng_state_all(snapshot["cuda_rng"])
        def replay(ids, attention, length):
            override = getattr(trainer, "allowed_override", None)
            if override is not None:  # floor measurement supplies its replayed sets
                trainer.allowed_override = None
                return override
            return allowed_sets(engine, codec, ids[-length:])
        trainer_cls = make_masked_grpo_trainer(replay)
        # We use only the subclass's forward, deliberately avoiding Trainer's
        # unrelated data-loader/server internals in this explicit smoke loop.
        trainer = object.__new__(trainer_cls)
        trainer.temperature = training_config["temperature"]
        def writer(target):
            from peft import get_peft_model_state_dict
            model.save_pretrained(target / "adapter")
            torch.save({"adapter": get_peft_model_state_dict(model), "optimizer": optimizer.state_dict(),
                        "python_rng": random.getstate(), "numpy_rng": np.random.get_state(),
                        "torch_rng": torch.get_rng_state(), "cuda_rng": torch.cuda.get_rng_state_all()},
                       target / "training.pt")
        history = []
        # Base-only floor before the first sync of this invocation (the worker still holds the base weights).
        floor = measure_agreement_floor(model, trainer, parent, prompt=prompt, spec=spec, engine=engine, codec=codec,
                                        group_size=group_size, max_tokens=max_tokens, deadline=initial_deadline)
        for step in range(begin, steps):
            deadline = initial_deadline if step == begin else min(run_deadline, time.monotonic() + step_timeout_s)
            sync_policy_weights(model, parent, deadline)
            parent.send({"prompt_ids": list(prompt), "spec": spec, "group_size": group_size,
                         "max_tokens": max_tokens, "seed": step})
            sampled = _receive(parent, deadline - time.monotonic())
            tokens_group = sampled["tokens"]
            rollouts, scores, passed_count, dropped_samples = [], [], 0, []
            for tokens in tokens_group:
                actual, sets, outcome = score_rollout(instance, engine, codec, tokens, seed=step,
                                                      config=mvp_reward_config())
                scores.append(outcome.value)
                if outcome.value is None:
                    dropped_samples.append(dropped_sample_diagnostics(instance, outcome))
                passed_count += int(outcome.passed)
                rollouts.append((actual, sets))
            advantages = normalize_group_rewards(scores)
            timeout_policy = advance_timeout_policy([advantages.stats], previous=timeout_policy)
            state = {"step": step + 1, "spent_dollars": reservation["reserved_dollars"],
                     "manifest": asdict(manifest), "instance": instance_json, "training_config": training_config,
                     **reward_checkpoint_metrics(scores, advantages, passed_count=passed_count),
                     "timeout_policy": timeout_policy, "dropped_samples": dropped_samples,
                     "optimizer_updated": False, "loss": None,
                     "max_masked_logp_error": None, "gradient_norm": None}
            require_timeout_admission(
                timeout_policy, lambda: checkpoints.save(step + 1, state, writer))
            optimizer.zero_grad()
            losses, pairs = [], []
            max_logp_error = 0.0
            for rollout_index, ((tokens, sets), advantage) in enumerate(zip(rollouts, advantages, strict=True)):
                decision = [len(s) > 1 for s in sets]
                if scores[rollout_index] is None or not tokens or not any(decision):
                    continue
                ids = torch.tensor([list(prompt) + tokens], device="cuda:0")
                attention = torch.ones_like(ids)
                with torch.no_grad():
                    old, _, _ = trainer._get_per_token_logps_and_entropies(model, ids, attention, len(tokens))
                sampler_lp = sampled["logps"][rollout_index][:len(tokens)]
                trainer_lp = old[0].detach().float().cpu().numpy()
                error = assert_masked_agreement(trainer_lp, sampler_lp)
                pairs.append((trainer_lp, sampler_lp))
                max_logp_error = max(max_logp_error, error)
                current, _, _ = trainer._get_per_token_logps_and_entropies(model, ids, attention, len(tokens))
                loss = grpo_rollout_loss(current, old, advantage, decision, group_size=group_size)
                loss.backward()
                losses.append(float(loss.detach()))
                if time.monotonic() > deadline:
                    raise TimeoutError("step exceeded admitted wall time before optimizer update")
            if not losses and any(score is not None for score in scores):
                raise RuntimeError("smoke generated no non-forced policy tokens; no masked-forward verification")
            agreement = agreement_stats(pairs) if pairs else None
            if agreement is not None:
                fails = agreement_failures(agreement, floor)
                if fails:  # before the optimizer update: an off-policy ratio must never train the adapter
                    raise RuntimeError(f"vLLM/trainer agreement gate failed: {fails}")
            grad_norm = None
            if losses:
                grad_norm = float(torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.))
                if not math.isfinite(grad_norm):
                    raise RuntimeError("nonfinite GRPO gradient")
                optimizer.step()
            verify_manifest(manifest, Path("/root/stowlm/src"))
            state.update(agreement=agreement, agreement_floor=floor,
                         loss=sum(losses) if losses else None, max_masked_logp_error=max_logp_error if losses else None,
                         gradient_norm=grad_norm, optimizer_updated=bool(losses))
            checkpoints.save(step + 1, state, writer)
            history.append(state)
            started = time.monotonic()
        return {"steps": history, "estimated_dollars": reservation["reserved_dollars"],
                "algorithm": "explicit GRPO surrogate, clip=0.2, beta=0; TRL masked forward"}
    finally:
        if process.is_alive():
            try:
                parent.send(None)
            except (BrokenPipeError, OSError):
                pass
            process.join(timeout=5)
        if process.is_alive():
            process.terminate()  # only the child PID created above, never a pattern kill
            process.join(timeout=5)
        parent.close()


def build_modal_app(repo_root: Path, manifest: PinManifest, *, cap: float,
                    prior_spend: float = 0, timeout_s: int = 600, steps: int = 1,
                    project_receipt: dict | None = None, sft_adapter: Path | None = None):
    """Construct definitions only; neither building images nor running remote work.

    Physics cache must already be populated in stowlm-physics-cache. Mount at the
    same path as local cache defaults; cache warm-up is a separate CPU admission,
    never silently triggered by a GPU smoke. Source and all rule/parameter inputs
    are shipped from the pinned tree; its hash is verified in the container.
    """
    import modal
    budget = SpendCap(cap, prior_spend)
    if steps < 1 or timeout_s < 1:
        raise ValueError("positive steps and timeout required")
    budget.authorize(timeout_s)
    root = Path(repo_root)
    verify_manifest(manifest, root / "src")
    image = (modal.Image.from_registry("nvidia/cuda:13.0.1-devel-ubuntu24.04", add_python="3.12")
             .pip_install(*(f"{k}=={v}" for k, v in STACK.items()), "numpy", "scipy", "pydantic",
                          "ortools", "highspy", "datasets", "accelerate==1.15.0")
             .env({"PYTHONPATH": "/root/stowlm/src", "STOWLM_SOURCE_COMMIT": manifest.commit})
             .workdir("/root/stowlm")
             .add_local_dir(str(root / "src"), "/root/stowlm/src", copy=True)
             .add_local_dir(str(root / "params"), "/root/stowlm/params", copy=True))
    if sft_adapter is not None:
        if not Path(sft_adapter).is_dir():
            raise ValueError("SFT adapter must be an exported PEFT directory")
        image = image.add_local_dir(str(sft_adapter), "/root/stowlm/sft-adapter", copy=True)
    weights = modal.Volume.from_name("stowlm-glimmer-weights", create_if_missing=False)
    physics = modal.Volume.from_name("stowlm-physics-cache", create_if_missing=False)
    checkpoints = modal.Volume.from_name("stowlm-grpo-checkpoints", create_if_missing=True)
    app = modal.App("stowlm-grpo")

    @app.function(image=image, cpu=1, memory=1024, timeout=60, retries=0, max_containers=1,
                  volumes={"/checkpoints": checkpoints, "/root/stowlm/experiments/cache": physics})
    def admit(run_id: str, resume: bool = False, instance_json: dict | None = None):
        if not project_receipt or project_receipt.get("run_id") != run_id:
            raise RuntimeError("shared project budget reservation required before Modal admission")
        from llm.rl.preflight import preflight_physics_cache
        verify_manifest(manifest, Path("/root/stowlm/src"), check_stack=True)
        if instance_json is None:
            raise ValueError("instance_json is required for physics cache preflight")
        preflight_physics_cache(instance_json, manifest, Path("/root/stowlm/experiments/cache"))
        if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in run_id):
            raise ValueError("run_id must be a lowercase slug")
        store = StepCheckpoint(Path(f"/checkpoints/{run_id}"), checkpoints)
        latest = store.load()
        completed = latest[1]["step"] if latest and resume else 0
        remaining = steps - completed
        if remaining <= 0:
            raise RuntimeError("requested steps already completed")
        return reserve_run_budget(store.root, manifest, cap=cap, seconds=remaining * timeout_s,
                                  prior_spend=prior_spend, resume=resume, volume=checkpoints)

    @app.function(image=image, gpu="H100:2", cpu=8, memory=131072,
                  volumes={"/weights": weights, "/root/stowlm/experiments/cache": physics,
                           "/checkpoints": checkpoints},
                  timeout=min(timeout_s * steps, budget.timeout_s), retries=0, scaledown_window=2, max_containers=1)
    def smoke(instance_json: dict, run_id: str, steps: int = 1, max_tokens: int = 256,
              group_size: int = 2, resume: bool = False, reservation: dict | None = None):
        if not project_receipt or project_receipt.get("run_id") != run_id:
            raise RuntimeError("shared project budget reservation required before GPU admission")
        if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in run_id):
            raise ValueError("run_id must be a lowercase slug")
        # Before any import or model work: the durable reservation (remaining steps only, on resume) bounds this
        # invocation with a hard stop. The Modal timeout on this function is only an outer backstop.
        if not reservation or "deadline_epoch" not in reservation:
            raise RuntimeError("GPU invocation needs a durable CPU admission reservation with a deadline")
        hard_stop(reservation["deadline_epoch"])
        return run_gpu_smoke(instance_json, manifest, checkpoint_root=f"/checkpoints/{run_id}",
            volume=checkpoints, cap=cap, steps=steps, max_tokens=max_tokens,
            group_size=group_size, step_timeout_s=timeout_s, resume=resume, reservation=reservation,
            sft_adapter="/root/stowlm/sft-adapter" if sft_adapter is not None else None)
    return app, admit, smoke


def main():
    from decimal import Decimal
    from llm.rl.spend import ModalSpendLedger, require_credit_ready
    parser = argparse.ArgumentParser(description="Credit-funded GRPO smoke; never run without leader approval")
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cap", type=float, required=True)
    parser.add_argument("--overhead-usd", type=Decimal,
                        help="positive reserved lifecycle/build/storage/egress headroom within --cap")
    parser.add_argument("--sft-adapter", type=Path, help="converted PEFT SFT adapter directory")
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--group-size", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prior-spend", type=float, default=0,
                        help="required reserved_dollars total from run budget.json when resuming")
    args = parser.parse_args()
    require_credit_ready()  # Before ledger writes, image upload/build, or any Modal lifecycle.
    if args.overhead_usd is None or args.overhead_usd <= 0:
        parser.error("--overhead-usd must explicitly reserve lifecycle/build/storage/egress costs")
    if args.resume and args.prior_spend <= 0:
        parser.error("resume requires --prior-spend from budget.json reserved_dollars")
    root = Path(__file__).resolve().parents[3]
    instance_json = json.loads(args.instance.read_text())
    if args.sft_adapter is not None and not args.sft_adapter.is_dir():
        parser.error("--sft-adapter must be an exported PEFT directory")
    compute_cap = args.cap - float(args.overhead_usd)
    bounded = SpendCap(compute_cap, args.prior_spend)
    if args.steps < 1 or args.timeout_s < 1:
        parser.error("positive steps and timeout required")
    # Completed steps are known only after the durable Volume reload in admit.
    # Require one next step here; admit checks the exact remaining step count.
    bounded.authorize(args.timeout_s)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    manifest = PinManifest.capture(commit, root / "src")
    ledger = ModalSpendLedger()
    project_receipt = ledger.reserve(args.run_id, Decimal(str(args.cap)),
        compute_estimate=Decimal(str(args.steps * args.timeout_s * RATE_PER_SECOND)),
        overhead_usd=args.overhead_usd)
    print(f"Shared budget reservation {project_receipt['reservation_id']}: ${args.cap}; "
          "retained pending final itemized provider billing", flush=True)
    try:
        app, admit, smoke = build_modal_app(root, manifest, cap=compute_cap, prior_spend=args.prior_spend,
            timeout_s=args.timeout_s, steps=args.steps, project_receipt=project_receipt,
            sft_adapter=args.sft_adapter)
        with app.run():
            reservation = admit.remote(args.run_id, resume=args.resume, instance_json=instance_json)
            result = smoke.remote(instance_json, args.run_id, steps=args.steps,
                                  max_tokens=args.max_tokens, group_size=args.group_size, resume=args.resume,
                                  reservation=reservation)
    finally:
        # Success, failure and cancellation all leave the full durable hold.
        # Only offline final provider reconciliation may release unspent headroom.
        ledger.sync_compute()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
