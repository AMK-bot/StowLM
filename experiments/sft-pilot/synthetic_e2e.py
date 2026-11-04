"""8.1 end-to-end training loop on the real 4-bit Glimmer with a clean SYNTHETIC FIXTURE dataset (fix-t8-sft).

Not a quality result and never shippable: the token data are seeded random IDs labelled ``synthetic_fixture``, the
dataset is ``unverified`` (not 7.1 production gold), so ``train`` admits it only with ``diagnostic=True`` under
``sft-pilot/`` and stamps the adapter's lineage with a stub. What it shows:

1. admission -> QLoRA steps -> numbered checkpoints (adapter + Adam + state marker) -> published adapter with its
   weight-bound sidecar, on the real model (D31-style config, last 16 layers, cosine + warmup);
2. an interrupted run (stopped after step 3, last checkpoint 2) resumed to the end gives the same adapter as an
   uninterrupted run (batch order and RNG are functions of (seed, step));
3. the 6.2 planner reads the sidecar: the adapter is tainted and refused by ``require_clean``.

    uv run python experiments/sft-pilot/synthetic_e2e.py [--iters 6]

Writes ``experiments/sft-pilot/synthetic-fixture/{data,full,split}/`` and ``synthetic_e2e.json``.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from dataclasses import replace
from pathlib import Path

from llm.stowlm import train as T
from llm.stowlm.sft_data import SFTExample, write_dataset

HERE = Path(__file__).resolve().parent
ROOT = HERE / "synthetic-fixture"


class Interrupt(Exception):
    pass


def fixture(n_train: int, n_val: int, seed: int = 0) -> list[SFTExample]:
    rng = random.Random(seed)
    rows = []
    for i in range(n_train + n_val):
        split = "train" if i < n_train else "val"
        n_prompt, n_completion = rng.randint(400, 700), rng.randint(100, 250)
        rows.append(SFTExample(instance_id=f"synthetic-fixture-{i:03d}", split=split,
                               input_ids=[rng.randint(1000, 100_000) for _ in range(n_prompt + n_completion)],
                               prompt_len=n_prompt, n_actions=1, n_boxes=1, plan_id="synthetic-fixture",
                               meta={"synthetic_fixture": True, "gold": {"published": False}}))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=6)
    a = ap.parse_args()
    if ROOT.exists():
        shutil.rmtree(ROOT)
    data = ROOT / "data"
    write_dataset(fixture(8, 2), data)
    cfg = T.TrainConfig(data_dir=str(data), adapter_dir=str(ROOT / "full"), num_layers=16, rank=8, scale=20.0,
                        learning_rate=5e-5, lr_schedule="cosine", warmup_frac=0.05, min_lr_frac=0.1,
                        attn_block=1024, max_seq_length=1024, batch_size=1, iters=a.iters, steps_per_report=1,
                        steps_per_eval=a.iters, steps_per_save=2, seed=0, diagnostic=True)
    t0 = time.perf_counter()
    full = T.train(cfg)
    t_full = time.perf_counter() - t0

    split = replace(cfg, adapter_dir=str(ROOT / "split"))

    def stop(step: int) -> None:
        if step == 3:
            raise Interrupt

    T._after_step = stop
    try:
        T.train(split)
    except Interrupt:
        pass
    T._after_step = lambda step: None
    resumed = T.train(split)

    import mlx.core as mx

    wf = mx.load(str(Path(cfg.adapter_dir) / "adapters.safetensors"))
    ws = mx.load(str(Path(split.adapter_dir) / "adapters.safetensors"))
    max_diff = max(float(mx.abs(wf[k] - ws[k]).max()) for k in wf)

    from llm.stowlm.planner import StowLMPlanner
    from stowage.frozen import StubTaintError, require_clean

    planner = StowLMPlanner(model_path=cfg.model, adapter_path=split.adapter_dir)
    try:
        require_clean(planner.provenance, what="official evaluation planner")
        refused = False
    except StubTaintError:
        refused = True
    sidecar = json.loads((Path(split.adapter_dir) / T.SIDECAR).read_text())
    summary = {
        "label": "SYNTHETIC FIXTURE — diagnostic loop check, not a quality result, never shippable",
        "config": {k: v for k, v in full["config"].items() if k not in ("extra",)},
        "full": {k: full[k] for k in ("train_loss_first", "train_loss_last", "val_loss_first", "val_loss_last",
                                       "wall_s", "peak_memory_gb", "tokens_per_second_mean", "stubs")},
        "full_wall_incl_load_s": round(t_full, 1),
        "resume": {"resumed_from": Path(resumed["resumed_from"]).name, "iterations_run": resumed["iterations_run"],
                   "max_abs_weight_diff_vs_uninterrupted": max_diff},
        "sidecar": {"weights_sha256": sidecar["weights_sha256"], "stubs": sidecar["provenance"]["stubs"],
                    "dataset_kind": sidecar["training"]["data_identity"]["dataset_kind"],
                    "base_model_content_sha256": sidecar["training"]["base_model_content_sha256"]},
        "planner_require_clean_refuses": refused,
    }
    (HERE / "synthetic_e2e.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
