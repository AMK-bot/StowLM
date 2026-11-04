"""SFT training (plan 8.1): QLoRA on 4-bit Glimmer in MLX.

QLoRA in one paragraph: the base model's weights stay frozen and quantised (4-bit affine, group 64,
``models/glimmer-30b-4bit``); each adapted linear layer W gets a trainable low-rank update ``scale * B A`` (rank r,
a few million parameters in total). Gradients flow through the frozen 4-bit weights into A and B only, so a 30B model
trains in ~40-70 GB instead of hundreds. Sources: Hu et al. 2021 (LoRA, arXiv:2106.09685), Dettmers et al. 2023
(QLoRA, arXiv:2305.14314). mlx-lm implements the layers (``mlx_lm.tuner.lora``) and the step
(``mlx_lm.tuner.trainer.train``); this module supplies what is StowLM-specific:

- data admission (``admit_dataset``): only a 7.1 ``production_gold`` dataset (stowlm-sft@2.0, every row published
  gold with bound production replay and the v2 split pins) trains a shippable adapter. Anything else (tiny-chain
  stubs, unverified synthetic fixtures) is refused unless ``diagnostic=True`` *and* the adapter folder is under
  ``tiny-chain/`` or ``sft-pilot/``; such adapters carry an explicit stub in their lineage, so they are never
  shippable or official (``stowage.frozen.require_clean`` rejects them);
- the data: ``llm.stowlm.sft_data`` examples, used as token IDs as built (D9: never re-tokenised), with the loss on
  the assistant/action tokens only — ``completion_loss`` (mlx-lm's ``default_loss`` has an off-by-one that trains
  one padding token after every sequence; see its docstring and tests/llm/test_train.py);
- refusal to truncate: an example longer than ``max_seq_length`` would lose its end-of-plan token, so it is an error;
- weight-bound lineage: every published adapter gets ``adapters.safetensors.provenance.json`` (the 6.2 planner's
  consumer contract: schema 1, the adapter file's SHA-256, a Provenance with explicit stubs) recording dataset
  identity (version, kind, file and row hashes), base-model content SHA-256, trainer version and config. Taint is
  contagious: dataset stubs, stubs active while training and every resumed checkpoint's lineage are merged in;
- checkpoints at every ``steps_per_save`` and at the final step, under *global* step numbers, each a complete set
  (adapter, Adam state, ``<step>_state.json`` written last as the commit marker with both files' hashes, the
  config/data/base-model identity and the lineage). Resume takes the latest complete, matching checkpoint and refuses
  a mismatched config, dataset or base model, or a legacy checkpoint without state. The optimizer state matters: the
  tiny chain measured that resuming with fresh Adam moments sends the loss from 0.45 to 2.5 within 5 steps;
- deterministic resume: the batch order and the dropout RNG are pure functions of (seed, step) (``batch_schedule``
  and ``step_seed``), so a run interrupted and resumed at a checkpoint takes exactly the same steps as an
  uninterrupted one (GPU test ``test_split_run_matches_uninterrupted_run``);
- a run record (``run.json`` + ``metrics.jsonl``) for humans; it is informational, never the lineage source.

Measured on the M5 Max (``experiments/glimmer-bench/README.md``, sustained): seq 8k needs gradient checkpointing
(it runs out of memory without), ~180 tok/s on the last 16 layers, ~87 tok/s on all 52.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llm.stowlm.sft_data import (DATASET_VERSION, MVP_GOLD, TAINT_OK_DIRS, SFTExample, example_sha256,
                                 mvp_row_failure, read_dataset)
from eval.snapshot_io import read_snapshot

DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models/glimmer-30b-4bit"
TRAINER_VERSION = "stowlm-train@2.1"  # 2.1: lr schedule sized in optimizer updates (was micro-steps)
SIDECAR = "adapters.safetensors.provenance.json"  # consumer: llm.stowlm.planner.StowLMPlanner._adapter_provenance
CHECKPOINT_SCHEMA = 1
# Config fields that do not change which updates are applied: they may differ between a run and its resume.
# Data and base model are compared by content hash, not by path.
NON_TRAJECTORY_FIELDS = {"data_dir", "adapter_dir", "model", "resume", "steps_per_report", "steps_per_eval",
                         "steps_per_save", "val_batches", "extra", "diagnostic"}


@dataclass
class TrainConfig:
    data_dir: str
    adapter_dir: str
    model: str = str(DEFAULT_MODEL)
    rank: int = 8
    scale: float = 20.0
    dropout: float = 0.0
    num_layers: int = 16  # last N transformer blocks (mlx-lm convention); 52 = all
    keys: list[str] | None = None  # None = every linear in the block (q,k,v,o, attention gate, mlp gate/up/down)
    learning_rate: float = 1e-4  # peak learning rate
    lr_schedule: str = "constant"  # "constant" or "cosine" (linear warmup, then cosine decay to min_lr_frac * peak)
    warmup_frac: float = 0.0  # share of `iters` spent warming up linearly from 0 (applies to both schedules)
    min_lr_frac: float = 0.0
    attn_block: int = 0  # >0: exact query-blocked attention in training (see blocked_attention); 0 = mlx-lm's own
    iters: int = 200
    batch_size: int = 1
    grad_accumulation_steps: int = 1
    # Prompt 3.0 measured lengths (prompt + completion; w2-l4-format-review): full ship ~9,160, dense 800-box pilot
    # 18,006. 18,432 covers both; qualified at ~18k tokens on 16 layers + blocked attention (sft-pilot/README.md).
    max_seq_length: int = 18432
    grad_checkpoint: bool = True
    steps_per_report: int = 10
    steps_per_eval: int = 50
    steps_per_save: int = 50
    val_batches: int = -1  # -1 = the whole validation split
    seed: int = 0
    resume: bool = True
    train_split: str = "train"
    val_split: str = "val"
    # True admits non-gold data (tiny-chain stubs, unverified synthetic fixtures) into tiny-chain/ or sft-pilot/ only;
    # the adapter's lineage then carries a stub, so it can never ship or enter an official evaluation.
    diagnostic: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class TokenDataset:
    """mlx-lm dataset protocol: ``ds[i] -> (token_ids, offset)``; ``process`` is the identity (already tokenised)."""

    def __init__(self, examples: list[SFTExample], max_seq_length: int) -> None:
        too_long = [e.instance_id for e in examples if len(e.input_ids) > max_seq_length]
        if too_long:
            raise ValueError(f"{len(too_long)} examples exceed max_seq_length={max_seq_length} (e.g. {too_long[0]}); "
                             "truncation would cut the end-of-plan token")
        if not examples:
            raise ValueError("empty dataset")
        self._items = [(list(e.input_ids), e.prompt_len) for e in examples]

    def __getitem__(self, idx: int):
        return self._items[idx]

    def __len__(self) -> int:
        return len(self._items)

    def process(self, item):
        return item


def blocked_attention(block: int, window: int, original):
    """Exact attention computed in query blocks of ``block`` rows, each block wrapped in ``mx.checkpoint``.

    Why: in training, mlx-lm's attention backward materialises the full (heads × L × L) score matrices, so memory
    grows with L² (measured: 41 GB at 8k, 66 GB at 12k on 16 layers). Softmax is per query row, so splitting the
    queries into blocks gives exactly the same output; checkpointing each block keeps only one block's scores alive
    in the backward pass (memory ~heads × block × L). Sliding-window layers (mask passed as an L×L boolean array,
    window ``window``) only need keys in [s - window + 1, e), which also cuts their compute. Inference (cache set)
    and short sequences use ``original`` unchanged. Same idea as FlashAttention's tiling (Dao et al. 2022,
    arXiv:2205.14135), done at the block level with recomputation instead of a fused kernel."""
    import mlx.core as mx

    def sdpa(queries, keys, values, cache, scale, mask, sinks=None):
        L = queries.shape[2]
        if cache is not None or sinks is not None or L <= block or keys.shape[2] != L:
            return original(queries, keys, values, cache, scale=scale, mask=mask, sinks=sinks)
        outs = []
        for s in range(0, L, block):
            e = min(L, s + block)
            if isinstance(mask, str):  # "causal": full-attention layer
                k0 = 0
                m = mx.arange(s, e)[:, None] >= mx.arange(0, e)[None, :]
            elif mask is None:  # not causal (never used by Glimmer's decoder; kept for completeness)
                k0, m = 0, None
            else:  # boolean/additive array: the sliding-window layers
                k0 = max(0, s - window + 1)
                m = mask[..., s:e, k0:e]
            hi = L if mask is None else e

            def attend(q, k, v, m=m):
                return mx.fast.scaled_dot_product_attention(q, k, v, scale=scale, mask=m)

            outs.append(mx.checkpoint(attend)(queries[:, :, s:e], keys[:, :, k0:hi], values[:, :, k0:hi]))
        return mx.concatenate(outs, axis=2)

    return sdpa


def learning_rate_schedule(cfg: TrainConfig):
    """Peak lr, optionally with linear warmup over ``warmup_frac * iters`` steps and cosine decay after it.

    Why: Adam's first steps are ~lr-sized in every coordinate whatever the gradient scale, so starting at the peak
    lr can kick a LoRA adapter far from where it started; a short warmup is the standard fix (Goyal et al. 2017,
    arXiv:1706.02677; the cosine schedule is Loshchilov & Hutter 2017, SGDR, arXiv:1608.03983)."""
    import mlx.optimizers as optim

    # The optimizer (and so the schedule) steps once per ``grad_accumulation_steps`` micro-steps: size the schedule in
    # optimizer updates. (stowlm-train@2.0 sized it in micro-steps, so with accumulation 4 the warmup took 4x longer and
    # the cosine ended a quarter of the way down: pilot 2 / 8.1 v1 trained at ~0.9-1.0x peak after warmup.)
    updates = max(1, cfg.iters // max(1, cfg.grad_accumulation_steps))
    warm = int(round(cfg.warmup_frac * updates))
    if cfg.lr_schedule not in ("constant", "cosine"):
        raise ValueError(f"unknown lr_schedule {cfg.lr_schedule!r}")
    if cfg.lr_schedule == "constant" and warm == 0:
        return cfg.learning_rate
    rest = max(1, updates - warm)
    after = (optim.cosine_decay(cfg.learning_rate, rest, cfg.min_lr_frac * cfg.learning_rate)
             if cfg.lr_schedule == "cosine" else (lambda step: cfg.learning_rate))
    if warm == 0:
        return after
    return optim.join_schedules([optim.linear_schedule(0.0, cfg.learning_rate, warm), after], [warm])


def completion_loss(model, batch, lengths):
    """Cross-entropy on completion tokens only. ``lengths[:, 0]`` = prompt_len (first trained token position),
    ``lengths[:, 1]`` = sequence length. Target j predicts token j+1, so token positions prompt_len .. length-1 are
    targets j = prompt_len-1 .. length-2. mlx-lm 0.32.2's ``default_loss`` uses ``steps <= length``, which also
    trains one padding token (id 0) after each sequence's end; this keeps ``steps < length``."""
    import mlx.core as mx
    import mlx.nn as nn

    inputs, targets = batch[:, :-1], batch[:, 1:]
    logits = model(inputs)
    steps = mx.arange(1, targets.shape[1] + 1)  # step s = j + 1 = position of the predicted token
    mask = mx.logical_and(steps >= lengths[:, 0:1], steps < lengths[:, 1:])
    ce = nn.losses.cross_entropy(logits, targets) * mask
    ntoks = mask.sum()
    return ce.astype(mx.float32).sum() / ntoks, ntoks


def save_optimizer_state(optimizer, path: str | Path) -> None:
    import mlx.core as mx
    from mlx.utils import tree_flatten
    mx.save_safetensors(str(path), dict(tree_flatten(optimizer.state)))


def load_optimizer_state(optimizer, model, path: str | Path) -> None:
    """Restore Adam's step count and moments exactly (tests/llm/test_train.py checks the next update is identical)."""
    import mlx.core as mx
    from mlx.utils import tree_unflatten

    optimizer.init(model.trainable_parameters())
    optimizer.state = tree_unflatten(list(mx.load(str(path)).items()))


# ---------------------------------------------------------------------------------------------- identity, lineage


def _sha256_file(path: Path) -> str:
    from eval.harness_integrity import _file_digest  # same streaming hasher the planner/harness verify with

    return _file_digest(Path(path))


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


LR_SCHEDULE_UNITS = "optimizer_updates"  # trainer 2.1; 2.0 checkpoints (micro-step schedule) can't resume under it


def trajectory_config(cfg: TrainConfig) -> dict[str, Any]:
    """The config fields that decide which updates are applied (compared on resume)."""
    return {**{k: v for k, v in asdict(cfg).items() if k not in NON_TRAJECTORY_FIELDS},
            "lr_schedule_units": LR_SCHEDULE_UNITS}


def lora_parameters(cfg: TrainConfig) -> dict[str, Any]:
    params: dict[str, Any] = {"rank": cfg.rank, "scale": cfg.scale, "dropout": cfg.dropout}
    if cfg.keys is not None:
        params["keys"] = list(cfg.keys)
    return params


def _production_row_failure(e: SFTExample, pins: dict[str, Any]) -> str | None:
    """Re-check one row against 7.1's production-gold admission (``sft_data.write_dataset(production=True)``):
    the trainer does not trust a stats label it did not verify."""
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.format_prompt import active_prompt_version
    from llm.stowlm.mask import MASK_VERSION
    from stowage.actions import ACTION_SPACE_VERSION

    gold, replay = e.meta.get("gold", {}), e.meta.get("replay", {})
    digest = example_sha256(e)
    if gold.get("published") is not True:
        return "lacks published gold admission"
    if gold.get("example_sha256") != digest or replay.get("example_sha256") != digest:
        return "content binding changed after gold admission/replay"
    if pins.get("split_version") != "v2" or any(gold.get(k) != v for k, v in pins.items()):
        return "split release pin mismatch"
    if not (replay.get("ok") is True and replay.get("production") is True
            and replay.get("mask_version") == MASK_VERSION and not replay.get("stubs")):
        return "lacks current production mask replay"
    if any(e.meta.get(k) != v for k, v in
           (("format", FORMAT_VERSION), ("prompt", active_prompt_version()),  # 2.2, or 3.0 under rule freeze v1
            ("action_space", ACTION_SPACE_VERSION), ("dataset", DATASET_VERSION))):
        return "format/prompt/action-space/dataset version mismatch"
    from llm.stowlm.sft_data import validate_production_lineage
    try:
        validate_production_lineage(gold)
    except (ValueError, OSError) as error:
        return str(error)
    return None


@dataclass
class AdmittedData:
    train: list[SFTExample]
    val: list[SFTExample]
    identity: dict[str, Any]  # dataset version, kind, file hashes, row-entries hash, split pins
    stubs: dict[str, str]  # taint the adapter inherits from the data (empty only for verified production gold)


def admit_dataset(cfg: TrainConfig) -> AdmittedData:
    """Read the splits and decide whether this run may train on them (CPU only, before any MLX import).

    Production gold (verified row by row, untainted, current v2 pins) -> clean data lineage. Everything else needs
    ``cfg.diagnostic`` and a tiny-chain/sft-pilot adapter folder, and is labelled with a stub in the lineage."""
    from stowage.frozen import current_provenance

    data, out = Path(cfg.data_dir), Path(cfg.adapter_dir).resolve()
    files = {name: data / name for name in (f"{cfg.train_split}.jsonl", f"{cfg.val_split}.jsonl", "stats.json")}
    snapshots = {name: read_snapshot(path) if path.exists() else None for name, path in files.items()}
    file_sha = {name: snapshot.sha256 if snapshot is not None else None for name, snapshot in snapshots.items()}
    def split_rows(name):
        snapshot = snapshots[name]
        if snapshot is None:
            raise FileNotFoundError(files[name])
        return [SFTExample(**json.loads(line)) for line in snapshot.data.decode('utf-8').splitlines() if line.strip()]
    train_ex = split_rows(f'{cfg.train_split}.jsonl')
    val_ex = split_rows(f'{cfg.val_split}.jsonl')
    try:
        stats = snapshots['stats.json'].json() if snapshots['stats.json'] is not None else {}
    except (OSError, ValueError):
        stats = {}
    rows = [*train_ex, *val_ex]
    stubs: dict[str, str] = {}
    for e in rows:  # contagious: row stubs and their replay's stubs
        stubs.update(e.stubs)
        stubs.update(e.meta.get("replay", {}).get("stubs", {}) or {})
    stubs.update(current_provenance().stubs)  # an override active while training taints the adapter too
    pins = {k: stats[k] for k in ("split_version", "split_entries_sha256") if k in stats}
    claimed = stats.get("dataset_kind", "missing stats.json")
    problems: list[str] = []
    if stats.get("dataset") != DATASET_VERSION:
        problems.append(f"dataset version {stats.get('dataset')!r} is not {DATASET_VERSION}")
    if claimed not in ("production_gold", MVP_GOLD):
        problems.append(f"dataset_kind={claimed!r}")
    if stubs:
        problems.append(f"stub-tainted ({sorted(stubs)})")
    # The prompt/format the rows were serialised with (w2-eval-honesty handoff): consumers must encode prompts the
    # same way, so the adapter's lineage records them. Rows must agree with each other and with stats.json.
    row_prompts = sorted({str(e.meta.get("prompt")) for e in rows})
    row_formats = sorted({str(e.meta.get("format")) for e in rows})
    for what, found, declared in (("prompt", row_prompts, stats.get("prompt")),
                                  ("format", row_formats, stats.get("format"))):
        if len(found) != 1 or (declared is not None and found != [str(declared)]):
            problems.append(f"mixed or undeclared {what} versions {found} (stats.json: {declared!r})")
    if not problems and claimed == MVP_GOLD:  # plan v4 / D79: per-row checker + D79 replay binding, no v3 pins
        for e in rows:
            if (why := mvp_row_failure(e)) is not None:
                problems.append(f"example {e.instance_id}: {why}")
                break
    elif not problems:
        from eval.splits import load_inventory

        inventory = load_inventory()
        current = {"split_version": inventory.version, "split_entries_sha256": inventory.entries_sha256}
        if pins != current:
            problems.append(f"split pins {pins} are not the current release {current}")
        for e in rows:
            if (why := _production_row_failure(e, current)) is not None:
                problems.append(f"example {e.instance_id}: {why}")
                break
    kind = claimed if not problems else "diagnostic"
    if problems:
        if not cfg.diagnostic:
            raise RuntimeError(f"refusing to train a shippable adapter: dataset is not verified production gold (nor MVP gold) "
                               f"({'; '.join(problems)}). Non-gold data trains only with diagnostic=True under "
                               "tiny-chain/ or sft-pilot/")
        if not TAINT_OK_DIRS & set(out.parts):
            raise RuntimeError(f"diagnostic dataset ({'; '.join(problems)}); adapters from it may only live under "
                               "tiny-chain/ or sft-pilot/")
        stubs["stowlm.train.dataset"] = (f"non-gold SFT data ({'; '.join(problems)}): diagnostic adapter, "
                                         "never shippable")
    entries = sorted((e.split, e.instance_id, example_sha256(e)) for e in rows)
    identity = {"dataset_version": stats.get("dataset"), "dataset_kind": kind, "claimed_kind": claimed,
                "prompt_version": row_prompts[0] if len(row_prompts) == 1 else row_prompts,
                "action_space_version": (lambda v: v[0] if len(v) == 1 else v)(
                    sorted({str(e.meta.get("action_space")) for e in rows})),
                "format_version": row_formats[0] if len(row_formats) == 1 else row_formats,
                "files_sha256": file_sha, "entries_sha256": _canonical_sha256(entries),
                "n_train": len(train_ex), "n_val": len(val_ex),
                "training_epochs": sorted({e.meta.get('gold', {}).get('epoch_id') for e in rows
                    if e.meta.get('gold', {}).get('epoch_id')}),
                "training_catalogue_sha256": sorted({(e.meta.get('gold', {}).get('required_rules') or {}).get('catalogue_sha256')
                    for e in rows if (e.meta.get('gold', {}).get('required_rules') or {}).get('catalogue_sha256')}), **pins}
    for snapshot in snapshots.values():
        if snapshot is not None:
            snapshot.require_fresh()
    return AdmittedData(train_ex, val_ex, identity, stubs)


def training_provenance(cfg: TrainConfig, data: AdmittedData, base_model_sha256: str, inherited: list[Any]):
    """Lineage for every checkpoint/adapter of this run: current component versions and active stubs, the data's
    taint, and every resumed checkpoint's lineage (taint never washes out on resume)."""
    from stowage.frozen import current_provenance
    from stowage.model import Provenance

    prov = current_provenance(created_by=TRAINER_VERSION, seed=cfg.seed)
    return prov.merge(Provenance(stubs=data.stubs), *inherited,
                      parent_ids=[f"sft-data-sha256:{_canonical_sha256(data.identity['files_sha256'])}",
                                  f"sft-entries-sha256:{data.identity['entries_sha256']}",
                                  f"base-model-sha256:{base_model_sha256}"])


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".tmp-{os.getpid()}-{path.name}")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_adapter_sidecar(adapter_dir: str | Path, provenance, training: dict[str, Any]) -> dict[str, Any]:
    """Bind ``provenance`` to the current ``adapters.safetensors`` bytes (6.2 consumer contract, schema 1).

    The extra ``training`` block (dataset identity, base-model hash, config, trainer version) is informational for
    the consumer except ``base_model_content_sha256``, which the planner checks against the model it loads."""
    out = Path(adapter_dir)
    metadata = {"schema_version": 1, "weights_sha256": _sha256_file(out / "adapters.safetensors"),
                "provenance": provenance.model_dump(mode="json"), "training": training}
    _atomic_write_text(out / SIDECAR, json.dumps(metadata, indent=1, allow_nan=False))
    return metadata


# ----------------------------------------------------------------------------------------- checkpoints and resume


def _numbered(adapter_dir: Path, step: int, kind: str) -> Path:
    return adapter_dir / f"{step:07d}_{kind}"


def _latest_checkpoint(adapter_dir: Path) -> tuple[Path, int] | None:
    """Latest numbered adapter file by step (legacy helper; resume uses ``latest_complete_checkpoint``)."""
    found = [(int(m[1]), p) for p in adapter_dir.glob("*_adapters.safetensors")
             if (m := re.fullmatch(r"(\d+)_adapters\.safetensors", p.name))]
    if not found:
        return None
    it, path = max(found)
    return path, it


def read_checkpoint_state(adapter_dir: Path, step: int) -> dict[str, Any] | None:
    """The committed state of checkpoint ``step``, or None if it is incomplete or its files don't match it."""
    try:
        state = json.loads(_numbered(adapter_dir, step, "state.json").read_text())
        if state.get("schema_version") != CHECKPOINT_SCHEMA or state.get("iteration") != step:
            return None
        if (_sha256_file(_numbered(adapter_dir, step, "adapters.safetensors")) != state["adapter_sha256"]
                or _sha256_file(_numbered(adapter_dir, step, "optimizer.safetensors")) != state["optimizer_sha256"]):
            return None
        return state
    except (OSError, ValueError, KeyError, TypeError):
        return None


def latest_complete_checkpoint(adapter_dir: Path) -> dict[str, Any] | None:
    """Latest checkpoint whose state marker and both files are present and hash-consistent.

    A numbered adapter with no ``_state.json`` at all is a legacy (pre-2.0) or foreign checkpoint: refuse rather
    than silently restart over it. A step whose marker is missing but whose earlier steps are complete was
    interrupted mid-save: fall back to the earlier step."""
    steps = sorted({int(m[1]) for p in adapter_dir.glob("*_adapters.safetensors")
                    if (m := re.fullmatch(r"(\d+)_adapters\.safetensors", p.name))}, reverse=True)
    if steps and not any(adapter_dir.glob("*_state.json")):
        raise RuntimeError(f"{adapter_dir} holds legacy checkpoints without trainer state ({steps[0]:07d}_...); "
                           "their data order and lineage are unknown: train into a fresh adapter_dir")
    for step in steps:
        if (state := read_checkpoint_state(adapter_dir, step)) is not None:
            return state
    return None


def check_resume_compatible(state: dict[str, Any], cfg: TrainConfig, data_identity: dict[str, Any],
                            base_model_sha256: str) -> None:
    """Resuming continues the *same* trajectory: same data rows, base model and trajectory config. ``iters`` may grow
    only when the learning-rate schedule doesn't depend on it (constant, no warmup)."""
    old, new = dict(state["trajectory_config"]), trajectory_config(cfg)
    schedule_depends_on_iters = cfg.lr_schedule != "constant" or cfg.warmup_frac > 0
    if not schedule_depends_on_iters:
        old.pop("iters", None)
        new.pop("iters", None)
    diff = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
    if diff:
        raise RuntimeError(f"checkpoint {state['iteration']} was trained with a different config ({diff}); "
                           "use a fresh adapter_dir or resume=False")
    if state["data_identity"]["entries_sha256"] != data_identity["entries_sha256"]:
        raise RuntimeError("checkpoint was trained on different dataset rows; use a fresh adapter_dir")
    if state["base_model_content_sha256"] != base_model_sha256:
        raise RuntimeError("checkpoint was trained on a different base model; use a fresh adapter_dir")
    if state["iteration"] > cfg.iters:
        raise RuntimeError(f"checkpoint is at step {state['iteration']} > iters={cfg.iters}")


def batch_schedule(lengths: list[int], batch_size: int, seed: int, start: int = 0):
    """Yield the example indices of global batches ``start, start+1, ...`` forever.

    Batches are fixed groups of ``batch_size`` length-sorted neighbours (as mlx-lm builds them: similar lengths,
    less padding); epoch ``e`` visits them in the order ``default_rng([seed, e]).permutation``. Batch ``b`` is a pure
    function of (seed, b), so resuming at step ``start`` continues the uninterrupted sequence exactly, with no global
    NumPy state (mlx-lm's own iterator reseeds the global RNG and restarts at batch 0)."""
    import numpy as np

    order = sorted(range(len(lengths)), key=lambda i: (lengths[i], i))
    groups = [order[i:i + batch_size] for i in range(0, len(order) - batch_size + 1, batch_size)]
    if not groups:
        raise ValueError(f"dataset must have at least batch_size={batch_size} examples")
    b = start
    while True:
        epoch, pos = divmod(b, len(groups))
        perm = np.random.default_rng([seed, epoch]).permutation(len(groups))
        for p in perm[pos:]:
            yield groups[int(p)]
        b = (epoch + 1) * len(groups)


def processed_tokens(lengths: list[int], batch_size: int, seed: int, start: int, n: int) -> list[int]:
    """Tokens fed to the model (prompt + completion, unpadded) at global steps start+1 .. start+n, from the same
    deterministic ``batch_schedule`` training uses. mlx-lm's ``tokens_per_second`` counts only the loss (completion)
    tokens, which understates throughput ~20x on prompt-heavy StowLM examples; D31's budget is in processed tokens."""
    import itertools

    return [sum(lengths[i] for i in group)
            for group in itertools.islice(batch_schedule(lengths, batch_size, seed, start), n)]


def step_seed(seed: int, step: int) -> int:
    """MLX RNG seed for global step ``step`` (dropout masks). MLX can't restore an arbitrary RNG key, so the key is
    re-derived from (seed, step) before every step — the counter-based idea of JAX's ``fold_in`` (Salmon et al. 2011,
    "Parallel random numbers: as easy as 1, 2, 3")."""
    return int.from_bytes(hashlib.sha256(f"stowlm-train:{seed}:{step}".encode()).digest()[:8], "big") >> 1


def _make_iterate_batches(seed: int, start: int):
    """mlx-lm ``iterate_batches`` replacement: training (``loop=True``) follows ``batch_schedule`` from ``start``;
    evaluation (``loop=False``) walks the length-grouped batches in one fixed seeded order (same subset every eval)."""
    import mlx.core as mx
    import numpy as np

    def iterate_batches(dataset, batch_size, max_seq_length, loop=False, comm_group=None, **_):
        if comm_group is not None and comm_group.size() != 1:
            raise RuntimeError("StowLM SFT is single-device")
        lengths = [len(dataset[i][0]) for i in range(len(dataset))]
        if loop:
            groups = batch_schedule(lengths, batch_size, seed, start)
        else:  # one fixed seeded order, so a val_batches subset is the same unbiased sample at every evaluation
            order = sorted(range(len(dataset)), key=lambda i: (lengths[i], i))
            fixed = [order[i:i + batch_size] for i in range(0, len(order) - batch_size + 1, batch_size)]
            groups = (fixed[int(p)] for p in np.random.default_rng([seed, 2**31 - 1]).permutation(len(fixed)))
        for idx in groups:
            items = [dataset[j] for j in idx]
            width = 1 + 32 * ((max(len(t) for t, _ in items) + 31) // 32)  # mlx-lm's padding rule
            width = min(width, max_seq_length)
            arr = np.zeros((len(items), width), np.int32)
            for r, (toks, _) in enumerate(items):
                if len(toks) > max_seq_length:
                    raise ValueError("example longer than max_seq_length reached the batcher")
                arr[r, :len(toks)] = toks
            yield mx.array(arr), mx.array([[off, len(t)] for t, off in items])

    return iterate_batches


def _after_step(step: int) -> None:
    """Hook called after each global step's checkpoint work (tests replace it to simulate an interruption)."""


def train(cfg: TrainConfig) -> dict[str, Any]:
    """Run (or resume) one QLoRA SFT run; returns the run record also written to ``<adapter_dir>/run.json``."""
    out = Path(cfg.adapter_dir)
    if cfg.steps_per_save % cfg.grad_accumulation_steps or cfg.iters % cfg.grad_accumulation_steps:
        raise ValueError("steps_per_save and iters must be multiples of grad_accumulation_steps "
                         "(a checkpoint mid-accumulation would drop the accumulated gradient)")
    data = admit_dataset(cfg)  # CPU, before MLX: refuses non-gold data unless diagnostic
    train_set = TokenDataset(data.train, cfg.max_seq_length)
    val_set = TokenDataset(data.val, cfg.max_seq_length)
    if len(val_set) < cfg.batch_size:  # mlx-lm's evaluate yields no batch -> NaN val loss -> run.json crash at the end
        raise ValueError(f"val split has {len(val_set)} examples < batch_size {cfg.batch_size}")
    from eval.harness_integrity import _path_digest

    base_sha = _path_digest(cfg.model)  # whole model directory: weights, config, tokenizer (~6-12 s for 15.7 GB)
    out.mkdir(parents=True, exist_ok=True)
    state = latest_complete_checkpoint(out) if cfg.resume else None
    if state is not None:
        check_resume_compatible(state, cfg, data.identity, base_sha)
    elif not cfg.resume and any(out.glob("*_adapters.safetensors")):
        raise RuntimeError(f"{out} already holds checkpoints; resume=False would mix runs: use a fresh adapter_dir")

    from stowage.model import Provenance

    inherited = [] if state is None else [Provenance.model_validate(state["provenance"])]
    provenance = training_provenance(cfg, data, base_sha, inherited)
    if state is not None:
        provenance = provenance.merge(parent_ids=[f"resumed-from-checkpoint-adapter-sha256:{state['adapter_sha256']}"])
    segments = ([] if state is None else list(state.get("segments", []))) + [
        {"start": 0 if state is None else state["iteration"], "date": time.strftime("%Y-%m-%d %H:%M:%S")}]

    # MLX only from here on: everything above (data, identities, resume checks) is CPU and Metal-free
    import mlx.core as mx
    from llm.stowlm.mlx_memory import apply_cache_limit
    apply_cache_limit(default_gb=8.0)  # bounded MLX buffer cache; training reuses buffers, so a larger default
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten
    from mlx_lm.models import muse_glimmer
    from mlx_lm.tuner.callbacks import TrainingCallback
    from mlx_lm.tuner.datasets import CacheDataset
    from mlx_lm.tuner.trainer import TrainingArgs, evaluate
    from mlx_lm.tuner.trainer import train as mlx_train
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from mlx_lm.utils import load_model

    start_it = 0 if state is None else int(state["iteration"])
    mx.random.seed(cfg.seed)  # LoRA init; overwritten by the checkpoint's weights on resume
    model, _ = load_model(Path(cfg.model))
    model.freeze()
    linear_to_lora_layers(model, cfg.num_layers, lora_parameters(cfg))
    optimizer = optim.Adam(learning_rate=learning_rate_schedule(cfg))
    if state is not None:
        model.load_weights(str(_numbered(out, start_it, "adapters.safetensors")), strict=False)
        load_optimizer_state(optimizer, model, _numbered(out, start_it, "optimizer.safetensors"))
    n_trainable = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
    training_info = {"trainer": TRAINER_VERSION, "prompt_version": data.identity["prompt_version"],
                     "format_version": data.identity["format_version"],
                     "action_space_version": data.identity["action_space_version"], "config": asdict(cfg), "trajectory_config": trajectory_config(cfg),
                     "data_identity": data.identity, "base_model_content_sha256": base_sha}
    # adapter_config.json in mlx-lm's format, so mlx_lm.load(adapter_path=...) and convert_adapter.py can read it
    _atomic_write_text(out / "adapter_config.json", json.dumps({
        "fine_tune_type": "lora", "num_layers": cfg.num_layers, "lora_parameters": lora_parameters(cfg),
        "model": cfg.model, "stowlm_train_config": asdict(cfg), "stowlm_trainer": TRAINER_VERSION}, indent=1))

    def save_checkpoint(step: int) -> dict[str, Any]:
        """Numbered adapter + Adam state, then the state marker (commit point), then publish as the current
        adapter with its weight-bound sidecar. A crash at any point leaves the previous complete checkpoint valid."""
        weights = dict(tree_flatten(model.trainable_parameters()))
        ad, op = _numbered(out, step, "adapters.safetensors"), _numbered(out, step, "optimizer.safetensors")
        for target, save in ((ad, lambda p: mx.save_safetensors(str(p), weights)),
                             (op, lambda p: save_optimizer_state(optimizer, p))):
            tmp = target.with_name(f".tmp-{target.name}")
            save(tmp)
            os.replace(tmp, target)
        record = {"schema_version": CHECKPOINT_SCHEMA, "iteration": step, "adapter_sha256": _sha256_file(ad),
                  "optimizer_sha256": _sha256_file(op), "provenance": provenance.model_dump(mode="json"),
                  "segments": segments, "data_cursor": {"next_batch": step, "rule": "batch_schedule(seed, step)"},
                  "rng": {"next_step_seed": step_seed(cfg.seed, step), "rule": "step_seed(seed, step)"},
                  **training_info}
        _atomic_write_text(_numbered(out, step, "state.json"), json.dumps(record, indent=1, allow_nan=False))
        publish(step, ad)
        return record

    def publish(step: int, numbered_adapter: Path) -> None:
        tmp = out / ".tmp-adapters.safetensors"
        tmp.write_bytes(numbered_adapter.read_bytes())
        os.replace(tmp, out / "adapters.safetensors")
        write_adapter_sidecar(out, provenance, {**training_info, "iteration": step, "iters": cfg.iters,
                                                "segments": segments})

    metrics_file = out / "metrics.jsonl"

    def write_metric(info: dict) -> dict:
        info = {k: (float(v) if hasattr(v, "item") else v) for k, v in info.items()}
        with open(metrics_file, "a") as f:
            f.write(json.dumps(info) + "\n")
        return info

    def run_val(step: int) -> None:
        t = time.perf_counter()
        loss = evaluate(model=model, dataset=CacheDataset(val_set), batch_size=cfg.batch_size,
                        num_batches=cfg.val_batches, max_seq_length=cfg.max_seq_length, loss=completion_loss,
                        iterate_batches=_make_iterate_batches(cfg.seed, 0))
        rec.val.append(write_metric({"kind": "val", "iteration": step, "val_loss": loss,
                                     "val_time": time.perf_counter() - t}))
        model.train()

    class Recorder(TrainingCallback):
        """mlx-lm reports every step (steps_per_report=1); this aggregates to cfg.steps_per_report in global steps,
        saves checkpoints and runs validation at global multiples, and re-derives the RNG key for the next step."""

        def __init__(self) -> None:
            self.train, self.val, self.window = [], [], []

        def on_train_loss_report(self, info: dict) -> None:
            step = start_it + int(info["iteration"])
            self.window.append(info)
            if step % cfg.steps_per_report == 0 or step == cfg.iters:
                w = self.window
                secs = sum(1.0 / float(x["iterations_per_second"]) for x in w)
                toks = sum(float(x["tokens_per_second"]) / float(x["iterations_per_second"]) for x in w)
                fed_toks = sum(fed[s - start_it - 1] for s in range(step - len(w) + 1, step + 1))
                self.train.append(write_metric({
                    "kind": "train", "iteration": step, "train_loss": sum(float(x["train_loss"]) for x in w) / len(w),
                    "learning_rate": float(w[-1]["learning_rate"]), "iterations_per_second": len(w) / secs,
                    "tokens_per_second": toks / secs, "loss_tokens_per_second": toks / secs,
                    "processed_tokens_per_second": fed_toks / secs, "processed_tokens": fed_toks,
                    "trained_tokens": float(w[-1]["trained_tokens"]),
                    "peak_memory": float(w[-1]["peak_memory"])}))
                self.window = []
            if step % cfg.steps_per_save == 0 or step == cfg.iters:
                save_checkpoint(step)
            if step % cfg.steps_per_eval == 0 or step == cfg.iters:
                run_val(step)
            mx.random.seed(step_seed(cfg.seed, step))  # key for step + 1
            _after_step(step)

    rec = Recorder()
    remaining = cfg.iters - start_it
    fed = processed_tokens([len(train_set[i][0]) for i in range(len(train_set))], cfg.batch_size, cfg.seed,
                           start_it, remaining)
    # Process-wide patches, scoped to this call and restored in `finally` (a later call or the planner in the same
    # process must see mlx-lm's own functions): blocked attention, and mlx-lm's grad_checkpoint, which replaces the
    # transformer block class's __call__.
    original_sdpa = muse_glimmer.scaled_dot_product_attention
    block_cls = type(model.layers[0])
    original_call = block_cls.__call__
    scratch_final = out / ".mlx-final.safetensors"
    t0 = time.perf_counter()
    mx.reset_peak_memory()
    try:
        if cfg.attn_block:
            muse_glimmer.scaled_dot_product_attention = blocked_attention(
                cfg.attn_block, model.args.sliding_window, original_sdpa)
        if remaining:
            run_val(start_it)
            mx.random.seed(step_seed(cfg.seed, start_it))  # key for step start_it + 1
            args = TrainingArgs(batch_size=cfg.batch_size, iters=remaining, val_batches=cfg.val_batches,
                                steps_per_report=1, steps_per_eval=remaining + 1, steps_per_save=remaining + 1,
                                adapter_file=str(scratch_final), max_seq_length=cfg.max_seq_length,
                                grad_checkpoint=cfg.grad_checkpoint,
                                grad_accumulation_steps=cfg.grad_accumulation_steps)
            mlx_train(model=model, optimizer=optimizer, args=args, train_dataset=CacheDataset(train_set),
                      val_dataset=None, loss=completion_loss, training_callback=rec,
                      iterate_batches=_make_iterate_batches(cfg.seed, start_it))
        else:  # already at iters: (re)publish the final checkpoint with this call's lineage
            publish(start_it, _numbered(out, start_it, "adapters.safetensors"))
    finally:
        muse_glimmer.scaled_dot_product_attention = original_sdpa
        block_cls.__call__ = original_call
        scratch_final.unlink(missing_ok=True)
    wall = time.perf_counter() - t0
    tps = [r["tokens_per_second"] for r in rec.train if "tokens_per_second" in r]
    train_s = sum(r["processed_tokens"] / r["processed_tokens_per_second"] for r in rec.train
                  if r.get("processed_tokens_per_second"))
    record = {
        "config": asdict(cfg), "trainer": TRAINER_VERSION, "trainable_parameters": int(n_trainable),
        "stubs": sorted(provenance.stubs), "provenance": provenance.model_dump(mode="json"),
        "data_identity": data.identity, "base_model_content_sha256": base_sha,
        "n_train": len(train_set), "n_val": len(val_set),
        "resumed_from": None if state is None else str(_numbered(out, start_it, "adapters.safetensors")),
        "optimizer_state_on_resume": None if state is None else "restored",
        "segments": segments, "iterations_run": remaining, "wall_s": round(wall, 1),
        "peak_memory_gb": round(mx.get_peak_memory() / 1e9, 2),
        "train_loss_first": rec.train[0]["train_loss"] if rec.train else None,
        "train_loss_last": rec.train[-1]["train_loss"] if rec.train else None,
        "val_loss_first": rec.val[0]["val_loss"] if rec.val else None,
        "val_loss_last": rec.val[-1]["val_loss"] if rec.val else None,
        "tokens_per_second_mean": round(sum(tps) / len(tps), 1) if tps else None,  # loss tokens (legacy key)
        "loss_tokens_per_second_mean": round(sum(tps) / len(tps), 1) if tps else None,
        # D31 budgets are in processed tokens: every prompt + completion token fed, over training time (no val)
        "processed_tokens": int(sum(r["processed_tokens"] for r in rec.train)),
        "processed_tokens_per_second": (round(sum(r["processed_tokens"] for r in rec.train) / train_s, 1)
                                        if train_s else None),
        "date": time.strftime("%Y-%m-%d"),
        "note": "informational; the lineage of record is adapters.safetensors.provenance.json",
    }
    _atomic_write_text(out / "run.json", json.dumps(record, indent=1, allow_nan=False))
    return record


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description="StowLM QLoRA SFT (plan 8.1)")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--adapter-dir", required=True)
    for f in TrainConfig.__dataclass_fields__.values():
        if f.name in ("data_dir", "adapter_dir", "extra", "keys"):
            continue
        typ = {"int": int, "float": float, "str": str, "bool": lambda s: s.lower() in ("1", "true", "yes")}[
            f.type if isinstance(f.type, str) else f.type.__name__]
        ap.add_argument(f"--{f.name.replace('_', '-')}", type=typ, default=f.default)
    ap.add_argument("--keys", nargs="*", default=None)
    a = vars(ap.parse_args(argv))
    print(json.dumps(train(TrainConfig(**a)), indent=1))


if __name__ == "__main__":
    main()
