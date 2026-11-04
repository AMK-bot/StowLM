"""StowLM as a ``stowage.planner.Planner`` (name ``"stowlm"``; ``"stowlm.base"`` for untuned Glimmer + mask, 6.2).

plan(instance):
    prompt  = encode_prompt(instance)                      # 4.5 framing, token IDs (D9)
    decoder = MaskedDecoder(make_feasibility_engine(...))  # 5.1 mask over the 4.4 engine's legal actions
    tokens  = generate_masked(model, prompt, [decoder])    # every sampled token is in the allowed set
    plan    = engine.to_plan()                             # ActionSpace.expand -> MovePlan (ballast None)

The planner never judges its own plan: ``planner.finalize`` adds ballast and ``checker.check`` decides (CLAUDE.md:
"the LLM plans; the checker decides"). A mask dead end returns ``status="dead_end"`` with the partial plan for
repair (5.7). ``sets_ballast = False``. With temperature 0 (greedy) the planner is deterministic given the model,
adapter, instance and engine versions.

The model is loaded once per planner object (``mlx_lm.load`` with ``adapter_path``); ~17 GB peak for the 4-bit
base. ``n_samples=1`` plans greedily (or one sample) at batch 1. ``n_samples=k`` draws k plans of the same prompt in
one ``generate_masked`` call (prompt prefilled once, KV cache tiled; batch 32–64 is MLX's efficient range,
experiments/glimmer-bench) and keeps the **best-of-k**: the checker-feasible candidate with the lowest D11 move
objective, then D11's level-4 tie-breaks from the full checker (``heavy_on_deck_count``, ``killed_slots``; 0 when
the rule freeze hasn't activated them), then higher masked log-probability, then sample index. The move objective is
known before checking, so candidates are finalised (ballast) and full-checked *in move order*; once one passes,
only the rest of its equal-move group is still checked (level 4 needs their reports) and selection stops: every
better-ranked candidate was checked and failed, no worse one can win, so this is the same choice as checking all k
at a fraction of the cost (measured: ballast + check ~15–19 s per tiny candidate, 83–97 % of a best-of-16
instance, experiments/eval-c1/README.md). ``exhaustive_selection=True`` checks every candidate (diagnostics). The pipeline still performs its authoritative final check before shipping. Best-of-k is the usual
"sample many, keep the best by a verifier" baseline (e.g. Cobbe et al. 2021, arXiv:2110.14168); here the verifier
is the full checker; syntactic completion and mask legality do not establish physical feasibility.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from llm.stowlm.format import load_tokenizer
from llm.stowlm.format_prompt import encode_prompt
from llm.stowlm.sft_data import codec_for
from stowage.actions import get_action_space
from stowage.frozen import current_provenance
from stowage.model import Instance, PlannerResult, Provenance

DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models/glimmer-30b-4bit"


LEVEL4 = ("heavy_on_deck_count", "killed_slots")  # D11 level-4 tie-breaks, in priority order


class StowLMPlanner:
    sets_ballast = False

    def __init__(self, *, model_path: str | Path = DEFAULT_MODEL, adapter_path: str | Path | None = None,
                 temperature: float = 0.0, max_tokens: int = 20_000, mask_config: Any = None,
                 engine_config: Any = None, n_samples: int = 1, exhaustive_selection: bool = False,
                 decoder_factory: Any = None, backtrack: int = 0, greedy_first: bool = False,
                 candidate_finalize_s: float | None = None, last_resort: bool = True) -> None:
        """``decoder_factory(instance, engine, codec, mask_config, n) -> list`` of n MaskedDecoder-compatible
        objects (``allowed_token_ids/advance/prefetch/status/dead_end/tokens/actions/engine.to_plan``), each at the
        instance's root state; e.g. a process-pool mask backend (experiments/eval-c1/pool.py). It must produce the
        same allowed sets as ``MaskedDecoder``; None = in-thread decoders.

        Pass-rate options (v4, all off by default = the unchanged batched decode):
        ``backtrack`` > 0: decode samples one at a time with bounded backtracking (``llm.stowlm.backtrack``; at most
        this many rewinds per sample). ``greedy_first``: sample 0 is greedy (T = 0), the others use ``temperature``
        (best-of-k = greedy + k-1 samples). ``candidate_finalize_s``: finisher-aware selection; each candidate's
        ballast finisher gets at most this budget (a timeout fails that candidate and selection moves on); if none
        passes, the fewest-move candidate that ran out of budget gets the remaining time as a last resort."""
        self.model_path = str(model_path)
        self.adapter_path = None if adapter_path is None else str(adapter_path)
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.mask_config = mask_config
        self.engine_config = engine_config  # FeasibilityConfig for make_feasibility_engine (None = the 4.4 default)
        self.n_samples = int(n_samples)
        self.exhaustive_selection = bool(exhaustive_selection)
        self.decoder_factory = decoder_factory
        self.backtrack = int(backtrack)
        self.greedy_first = bool(greedy_first)
        self.candidate_finalize_s = None if candidate_finalize_s is None else float(candidate_finalize_s)
        # False = equal-budget evaluation (D128.5): no candidate's finisher gets more than candidate_finalize_s
        self.last_resort = bool(last_resort)
        if self.n_samples < 1:
            raise ValueError("n_samples must be >= 1")
        self.name = "stowlm" if adapter_path is not None else "stowlm.base"
        self.version = (f"{Path(self.model_path).name}+{Path(self.adapter_path).name if self.adapter_path else 'none'}"
                        + (f"+best-of-{self.n_samples}@T{self.temperature:g}" if self.n_samples > 1 else ""))
        self.deterministic = self.temperature <= 0 and self.n_samples == 1
        self._model = None
        self._loaded_content_identity: dict[str, str] | None = None
        self._loaded_adapter_provenance: Provenance | None = None

    def _adapter_provenance(self) -> Provenance:
        """Read the trainer's weight-bound lineage, never infer clean from a legacy run.json.

        Contract for 8.1: atomically publish adapters.safetensors.provenance.json
        with schema_version=1, weights_sha256 (the adapter file's SHA-256), and
        provenance (a Provenance JSON object with explicit stubs). This binds
        local training evidence to bytes; it is not third-party authentication.
        """
        if self.adapter_path is None:
            return Provenance()
        from eval.harness_integrity import _file_digest

        directory = Path(self.adapter_path)
        sidecar = directory / "adapters.safetensors.provenance.json"
        try:
            raw = sidecar.read_bytes()
            metadata = json.loads(raw)
            if (not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int
                    or metadata["schema_version"] != 1):
                raise ValueError("unsupported provenance schema_version")
            lineage = metadata.get("provenance")
            if not isinstance(lineage, dict) or not isinstance(lineage.get("stubs"), dict):
                raise ValueError("provenance must declare stubs explicitly")
            provenance = Provenance.model_validate(lineage)
            digest = _file_digest(directory / "adapters.safetensors")
            if metadata.get("weights_sha256") != digest:
                raise ValueError("provenance weights digest does not match adapter")
            if sidecar.read_bytes() != raw:
                raise ValueError("provenance changed while validating adapter")
            return provenance.merge(parent_ids=[f"adapter-weights-sha256:{digest}"])
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"untrusted adapter provenance: {exc}") from exc

    def _adapter_base_model_sha256(self) -> str | None:
        """Base-model content SHA-256 the adapter was trained on (8.1 sidecar ``training`` block), if recorded.

        A LoRA update is only meaningful on the weights it was trained against; a mismatch fails closed."""
        if self.adapter_path is None:
            return None
        try:
            training = json.loads((Path(self.adapter_path) / "adapters.safetensors.provenance.json").read_bytes()).get(
                "training") or {}
        except (OSError, ValueError, AttributeError):
            return None  # _adapter_provenance reports unreadable metadata
        value = training.get("base_model_content_sha256") if isinstance(training, dict) else None
        return value if isinstance(value, str) else None

    def _check_adapter_base(self, model_sha256: str | None) -> None:
        expected = self._adapter_base_model_sha256()
        if expected is not None and model_sha256 is not None and expected != model_sha256:
            raise ValueError("adapter was trained on a different base model (content SHA-256 mismatch)")

    @property
    def provenance(self) -> Provenance:
        """Expose adapter taint before an official harness can plan or reuse rows.

        Unknown lineage is explicitly tainted, including on error results. The
        loader separately rejects it, so direct callers retain the result/error
        protocol without ever generating with untrusted adapter metadata.
        """
        try:
            provenance = self._adapter_provenance()
        except ValueError as exc:
            provenance = Provenance(stubs={"stowlm.adapter_provenance": str(exc)})
        # Replacing a sidecar cannot erase taint retained by already-loaded
        # tensors, including when replacement metadata is itself invalid.
        if self._loaded_adapter_provenance is not None:
            provenance = provenance.merge(self._loaded_adapter_provenance)
        return provenance

    @property
    def evaluation_config(self) -> dict[str, Any]:
        """JSON identity of this planner for the 5.2 harness (the loaded model object itself is opaque)."""
        from dataclasses import asdict, is_dataclass

        def plain(x):
            return asdict(x) if is_dataclass(x) else x

        from eval.harness_integrity import pin_weight_content

        self._adapter_provenance()  # missing metadata cannot acquire a clean evaluation identity
        configuration = pin_weight_content({"model_path": self.model_path, "adapter_path": self.adapter_path,
                "temperature": self.temperature,
                "n_samples": self.n_samples, "max_tokens": self.max_tokens,
                "selection": ("full-checker-feasible min (weighted_nonproductive, level-4, -logprob, index)"
                              if self.exhaustive_selection else
                              "rank-order early stop after the best feasible move group (= exhaustive choice)"),
                **({"decoder_backend": getattr(self.decoder_factory, "name", type(self.decoder_factory).__name__)}
                   if self.decoder_factory is not None else {}),
                "mask_config": plain(self.mask_config), "engine_config": plain(self.engine_config),
                **({"backtrack": self.backtrack} if self.backtrack else {}),
                **({"greedy_first": True} if self.greedy_first else {}),
                **({"candidate_finalize_s": self.candidate_finalize_s} if self.candidate_finalize_s else {}),
                **({"last_resort": False} if not self.last_resort else {})})
        identity = {k: v for k, v in configuration.items() if k.endswith("_content_sha256")}
        self._check_adapter_base(identity.get("model_content_sha256"))
        if self._loaded_content_identity is not None and identity != self._loaded_content_identity:
            raise ValueError("model/adapter content changed after load; reload planner")
        return configuration

    def _load(self):
        adapter_provenance = self._adapter_provenance()  # validate before importing MLX
        if (self._loaded_adapter_provenance is not None
                and adapter_provenance != self._loaded_adapter_provenance):
            raise ValueError("adapter provenance changed after load; reload planner")
        if self._model is None:
            from eval.harness_integrity import pin_weight_content
            from mlx_lm import load

            from llm.stowlm.mlx_memory import apply_cache_limit

            self.mlx_cache_limit_gb = apply_cache_limit()  # bounded MLX buffer cache (llm.stowlm.mlx_memory)

            # The harness already streamed this snapshot before entering plan.
            # Reuse its bound identity within that run, avoiding a third full
            # 17 GB read. Direct callers take their own content snapshot here.
            identity = getattr(self, "_harness_weight_identity", None)
            if identity is None:
                identity = {k: v for k, v in pin_weight_content({
                    "model_path": self.model_path, "adapter_path": self.adapter_path}).items()
                    if k.endswith("_content_sha256")}
            self._check_adapter_base(identity.get("model_content_sha256"))
            self._model, _ = load(self.model_path, adapter_path=self.adapter_path)
            self._loaded_content_identity = dict(identity)
            self._loaded_adapter_provenance = adapter_provenance.model_copy(deep=True)
            self._harness_weight_identity = dict(identity)
        return self._model

    def plan(self, instance: Instance, *, seed: int = 0, time_limit_s: float | None = None) -> PlannerResult:
        started = time.perf_counter()
        from llm.stowlm.mask import MASK_VERSION, MaskedDecoder
        from llm.stowlm.mask_mlx import generate_masked
        from stowage.feasibility import make_feasibility_engine

        provenance = current_provenance(created_by=f"{self.name}@{self.version}", seed=seed).merge(
            instance.provenance, self.provenance, parent_ids=[instance.id])
        base = dict(planner=self.name, planner_version=self.version, seed=seed)
        deadline = None if time_limit_s is None else started + max(0.0, time_limit_s)
        metrics = {"n_samples": self.n_samples, "n_dead_end": 0, "mask_dead_ends": 0}
        decoders, space = [], None
        feasible, failures, t_selection = [], {}, None
        budget_out = []  # finisher-aware selection: candidates whose short finisher budget ran out

        def level4(report) -> tuple[float, ...]:
            """D11 level 4 (docs/decisions.md D11, D61): reported by the rules part once rule freeze v1 is active;
            absent for every candidate before then, so 0 keeps the order unchanged."""
            rules = report.parts.get("rules")
            values = rules.metrics if rules is not None else {}
            return tuple(float(values.get(name, 0.0)) for name in LEVEL4)

        def feasible_key(x):  # (moves, level 4, -logprob, sample index)
            return x[:4]

        class DeadlineExceeded(Exception):
            pass

        def check_deadline(*_):
            # Cooperative cancellation: blocking model/setup/checker calls finish before this check can run.
            # generate_masked invokes on_step after a decode step; prefill has no cancellation hook.
            if deadline is not None and time.perf_counter() >= deadline:
                raise DeadlineExceeded

        try:
            from stowage.moves import count_moves
            from stowage.planner import finalize
            from stowage.checker import check, report_matches_inputs

            check_deadline()
            model = self._load()
            check_deadline()
            tokenizer = load_tokenizer()
            space = get_action_space()
            check_deadline()
            t_engine = time.perf_counter()
            engine = make_feasibility_engine(instance, space, config=self.engine_config)
            engine_s = time.perf_counter() - t_engine
            check_deadline()
            codec = codec_for(instance, tokenizer)
            if self.decoder_factory is not None:
                decoders = list(self.decoder_factory(instance, engine, codec, self.mask_config, self.n_samples))
                if len(decoders) != self.n_samples:
                    raise ValueError("decoder_factory returned the wrong number of decoders")
            else:
                decoders = [MaskedDecoder(engine if i == 0 else engine.copy(), codec, self.mask_config)
                            for i in range(self.n_samples)]
            prompt = encode_prompt(instance, tokenizer)
            check_deadline()
            if self.backtrack or self.greedy_first:
                from llm.stowlm.backtrack import ForbiddingDecoder, generate_backtracking

                results, per_sample = [], []
                t_gen = time.perf_counter()
                for i, inner in enumerate(decoders):
                    r, st = generate_backtracking(
                        model, prompt, ForbiddingDecoder(inner),
                        temperature=0.0 if (self.greedy_first and i == 0) else self.temperature,
                        seed=seed * 1000 + i, max_tokens=self.max_tokens, max_backtracks=self.backtrack,
                        check_deadline=check_deadline if deadline is not None else None)
                    results.append(r)
                    per_sample.append(st)
                wall = time.perf_counter() - t_gen
                steps = sum(st["steps"] for st in per_sample)
                summary = {"wall_s": round(wall, 3), "steps": steps, "tokens": steps, "host_mask_s": 0.0,
                           "tok_s": round(steps / wall, 2) if wall else 0.0,
                           "prefill_s": round(sum(st["prefill_s"] for st in per_sample), 3),
                           "decode_mode": "sequential-backtracking", "backtracks": [st["backtracks"] for st in per_sample],
                           "rewound_actions": [st["rewound_actions"] for st in per_sample],
                           "cache_refills": sum(st["cache_refills"] for st in per_sample),
                           "cache_trims": sum(st["cache_trims"] for st in per_sample),
                           # unmasked argmax outside the allowed set, over every decode step (incl. rewound ones);
                           # "boundary" = first token of an action
                           "override_rate_all_steps": round(sum(st["argmax_illegal"] for st in per_sample) / steps, 4)
                           if steps else None,
                           "override_rate_action_start": round(sum(st["boundary_argmax_illegal"] for st in per_sample)
                                                               / max(1, sum(st["boundary_steps"] for st in per_sample)), 4),
                           "sample_temperatures": [0.0 if (self.greedy_first and i == 0) else self.temperature
                                                   for i in range(len(decoders))]}
            else:
                results, stats = generate_masked(model, prompt, decoders, temperature=self.temperature,
                                                 max_tokens=self.max_tokens, seed=seed,
                                                 **({"on_step": check_deadline} if deadline is not None else {}))
                summary = stats.summary()
            check_deadline()
            statuses = [r.status for r in results]
            try:
                from llm.stowlm.mlx_memory import memory_gb

                metrics.update(memory_gb(), mlx_cache_limit_gb=getattr(self, "mlx_cache_limit_gb", None))
            except Exception:  # fake models in CPU tests
                pass
            scored = []  # (objective, -logprob, index, raw plan), including rejected plans for repair
            for i, (r, d) in enumerate(zip(results, decoders)):
                if r.status == "complete":
                    candidate = d.engine.to_plan()
                    scored.append((count_moves(instance, candidate).weighted_nonproductive, -r.logprob, i, candidate))
            metrics.update({"tokens_in": len(prompt), "tokens_out": sum(len(r.tokens) for r in results),
                       "mask_version": MASK_VERSION, "temperature": self.temperature,
                       "n_complete": statuses.count("complete"), "n_dead_end": statuses.count("dead_end"),
                       "mask_dead_ends": statuses.count("dead_end"),
                       "n_max_tokens": statuses.count("max_tokens"), "engine_build_s": round(engine_s, 3),
                       "host_share": round(summary["host_mask_s"] / summary["wall_s"], 3) if summary["wall_s"] else None,
                       "sample_objectives": sorted(x[0] for x in scored),
                       "dead_end_depths": sorted(len(d.actions) for r, d in zip(results, decoders)
                                                 if r.status == "dead_end"),
                       **{f"gen_{k}": v for k, v in summary.items()}})
            plan, message = None, None
            required_parts = {"moves", "rules", "stability", "strength", "visibility", "lashing", "constraints"}

            def is_full_pass(report, plan_) -> bool:
                """A complete, bound full-checker pass: every required part ran without error or hard violation."""
                return bool(report.passed and report.count is not None and not report.shut_out
                            and required_parts.issubset(report.parts)
                            and not any(v.severity == "hard" for v in report.violations)
                            and all(p.ran and not p.error and not any(v.severity == "hard" for v in p.violations)
                                    for p in report.parts.values())
                            and report_matches_inputs(report, instance, plan_))

            if scored:
                t_selection = time.perf_counter()
                n_checked = 0
                # Rank order by default; exhaustive mode keeps sample order (its pick is order-independent).
                order = scored if self.exhaustive_selection else sorted(scored, key=lambda x: x[:3])
                for objective, neg_lp, index, candidate in order:
                    if feasible and not self.exhaustive_selection and objective > feasible[0][0]:
                        break  # move order: no candidate with more moves can beat a feasible one
                    check_deadline()
                    n_checked += 1
                    try:
                        provenance = provenance.merge(candidate.provenance)
                        trial = PlannerResult(status="ok", plan=candidate, provenance=provenance, **base)
                        remaining = 30.0 if deadline is None else max(0.0, deadline - time.perf_counter())
                        budget = remaining if self.candidate_finalize_s is None else min(remaining,
                                                                                          self.candidate_finalize_s)
                        t_fin = time.perf_counter()
                        trial = finalize(instance, trial, time_limit_s=budget)
                        provenance = provenance.merge_nested(trial)
                        check_deadline()
                        if trial.status != "ok" or trial.plan is None:
                            failures[str(index)] = trial.message or trial.status
                            if (self.candidate_finalize_s is not None
                                    and time.perf_counter() - t_fin >= 0.9 * budget):
                                budget_out.append((objective, neg_lp, index, candidate))
                            continue
                        report = check(instance, trial.plan)
                        provenance = provenance.merge_nested(report)
                        check_deadline()
                        # Decoder completion only closes the action grammar. Rank after the ballast finisher
                        # and the complete checker, preserving taint for run_planner's authoritative gate.
                        full_pass = is_full_pass(report, trial.plan)
                        if full_pass:
                            feasible.append((objective, level4(report), neg_lp, index, trial.plan))
                            provenance = provenance.merge(trial.provenance, trial.plan.provenance, report.provenance)
                        else:
                            failures[str(index)] = "full checker rejected candidate or report binding"
                    except DeadlineExceeded:
                        raise
                    except Exception as exc:
                        # One failed candidate cannot hide an independently feasible sibling.
                        failures[str(index)] = f"{type(exc).__name__}: {exc}"
                if not feasible and budget_out and self.last_resort:  # last resort: the fewest-move budget-capped candidate, remaining time
                    objective, neg_lp, index, candidate = min(budget_out, key=lambda x: x[:3])
                    check_deadline()
                    t_last = time.perf_counter()
                    try:
                        trial = PlannerResult(status="ok", plan=candidate, provenance=provenance, **base)
                        remaining = 30.0 if deadline is None else max(0.0, deadline - time.perf_counter())
                        trial = finalize(instance, trial, time_limit_s=remaining)
                        provenance = provenance.merge_nested(trial)
                        check_deadline()
                        if trial.status == "ok" and trial.plan is not None:
                            report = check(instance, trial.plan)
                            provenance = provenance.merge_nested(report)
                            if is_full_pass(report, trial.plan):
                                feasible.append((objective, level4(report), neg_lp, index, trial.plan))
                                provenance = provenance.merge(trial.provenance, trial.plan.provenance,
                                                              report.provenance)
                                failures.pop(str(index), None)
                            else:
                                failures[str(index)] = "full checker rejected candidate (last-resort finisher)"
                        else:
                            failures[str(index)] = f"last-resort finisher: {trial.message or trial.status}"
                    except DeadlineExceeded:
                        raise
                    except Exception as exc:
                        failures[str(index)] = f"last resort: {type(exc).__name__}: {exc}"
                    metrics["last_resort"] = {"sample": index, "s": round(time.perf_counter() - t_last, 3),
                                              "passed": any(x[3] == index for x in feasible)}
                metrics["finisher_budget_timeouts"] = [x[2] for x in budget_out]
                metrics.update(n_checker_feasible=len(feasible),  # among the checked candidates
                               checker_feasible_objectives=sorted(x[0] for x in feasible),
                               candidate_failures=failures, n_candidates_checked=n_checked,
                               chosen_level4=dict(zip(LEVEL4, min(feasible, key=feasible_key)[1])) if feasible else None,
                               selection_mode="exhaustive" if self.exhaustive_selection else "rank_order_first_pass",
                               candidate_selection_s=round(time.perf_counter() - t_selection, 3))
                if feasible:
                    objective, _, neg_lp, best, plan = min(feasible, key=feasible_key)
                else:
                    objective, neg_lp, best, plan = min(scored, key=lambda x: x[:3])
                if not feasible:
                    message = "No sampled completion passed finalization and the full checker; retained for repair."
                metrics.update(chosen_sample=best, chosen_objective=objective, masked_logprob=-neg_lp,
                               n_actions=len(decoders[best].actions), tokens_out_chosen=len(results[best].tokens))
                status = "ok"
                # D76 pass@1: the first sample by index, judged on its own (ballast + full checker), from the samples
                # already drawn; no selection. Reuses the selection's verdict when sample 0 was checked there.
                first = next((x for x in scored if x[2] == 0), None)
                p1 = {"sample": 0, "complete": first is not None, "checker_passed": False, "objective": None,
                      "checked_in_selection": False}
                if first is not None:
                    hit = next((x for x in feasible if x[3] == 0), None)
                    if hit is not None:
                        p1.update(checker_passed=True, objective=hit[0], checked_in_selection=True)
                    elif "0" in failures:
                        p1.update(checked_in_selection=True, reason=failures["0"])
                    else:
                        t_p1 = time.perf_counter()
                        try:
                            check_deadline()
                            trial = PlannerResult(status="ok", plan=first[3], provenance=provenance, **base)
                            remaining = 30.0 if deadline is None else max(0.0, deadline - time.perf_counter())
                            trial = finalize(instance, trial, time_limit_s=remaining)
                            provenance = provenance.merge_nested(trial)
                            if trial.status == "ok" and trial.plan is not None:
                                report = check(instance, trial.plan)
                                provenance = provenance.merge_nested(report)
                                p1.update(checker_passed=is_full_pass(report, trial.plan),
                                          objective=first[0] if is_full_pass(report, trial.plan) else None)
                            else:
                                p1.update(reason=trial.message or trial.status)
                        except DeadlineExceeded:  # never turn a delivered selection into a timeout
                            p1.update(checker_passed=None, reason="not checked: per-instance time limit")
                        except Exception as exc:
                            p1.update(reason=f"{type(exc).__name__}: {exc}")
                        p1["extra_check_s"] = round(time.perf_counter() - t_p1, 3)
                metrics["pass_at_1"] = p1
            else:
                # no complete sample: return the deepest partial plan (most committed actions) for repair (5.7)
                best = max(range(len(decoders)), key=lambda i: (len(decoders[i].actions), -i))
                d, r = decoders[best], results[best]
                status = "dead_end" if r.status == "dead_end" else "timeout"
                metrics["pass_at_1"] = {"sample": 0, "complete": False, "checker_passed": False, "objective": None,
                                        "checked_in_selection": False, "reason": f"sample 0 {results[0].status}"}
                message = d.dead_end.reason if d.dead_end is not None else r.status
                metrics.update(chosen_sample=best, n_actions=len(d.actions), masked_logprob=r.logprob)
                try:
                    plan = space.expand(instance, d.actions) if d.actions else None
                except ValueError:
                    plan = None
            check_deadline()
            if plan is not None:
                plan = plan.model_copy(update={"id": f"{instance.id}.{self.name}.{seed}",
                                               "provenance": provenance.merge(plan.provenance)})
                provenance = provenance.merge(plan.provenance)
            return PlannerResult(status=status, plan=plan, wall_time_s=time.perf_counter() - started,
                                 metrics=metrics, provenance=provenance, message=message, **base)
        except DeadlineExceeded:
            plan = None
            statuses = [d.status for d in decoders]
            metrics.update(deadline_exceeded=True, n_complete=statuses.count("complete"),
                           n_dead_end=statuses.count("dead_end"), mask_dead_ends=statuses.count("dead_end"),
                           tokens_out=sum(len(d.tokens) for d in decoders))
            if t_selection is not None:
                metrics.update(n_checker_feasible=len(feasible),
                               checker_feasible_objectives=sorted(x[0] for x in feasible),
                               candidate_failures=failures,
                               candidate_selection_s=round(time.perf_counter() - t_selection, 3))
            if feasible:
                objective, _, neg_lp, best, plan = min(feasible, key=feasible_key)
                metrics.update(chosen_sample=best, chosen_objective=objective, masked_logprob=-neg_lp,
                               n_actions=len(decoders[best].actions))
                plan = plan.model_copy(update={"id": f"{instance.id}.{self.name}.{seed}",
                                               "provenance": provenance.merge(plan.provenance)})
                provenance = provenance.merge(plan.provenance)
            elif decoders:
                best = max(range(len(decoders)), key=lambda i: (len(decoders[i].actions), -i))
                d = decoders[best]
                metrics.update(chosen_sample=best, n_actions=len(d.actions))
                try:
                    plan = d.engine.to_plan() if d.status == "complete" else (
                        space.expand(instance, d.actions) if d.actions else None)
                    if plan is not None:
                        plan = plan.model_copy(update={"id": f"{instance.id}.{self.name}.{seed}",
                                                       "provenance": provenance.merge(plan.provenance)})
                        provenance = provenance.merge(plan.provenance)
                except Exception:
                    plan = None
            return PlannerResult(status="timeout", plan=plan, wall_time_s=time.perf_counter() - started,
                                 metrics=metrics, provenance=provenance,
                                 message="Elapsed planning deadline reached at a cooperative cancellation point.", **base)
        except Exception as exc:  # the protocol: never raise for a hard instance
            if decoders:
                statuses = [d.status for d in decoders]
                metrics.update(n_complete=statuses.count("complete"), n_dead_end=statuses.count("dead_end"),
                               mask_dead_ends=statuses.count("dead_end"),
                               tokens_out=sum(len(d.tokens) for d in decoders))
            return PlannerResult(status="error", wall_time_s=time.perf_counter() - started, provenance=provenance,
                                 metrics=metrics, message=f"{type(exc).__name__}: {exc}", **base)
