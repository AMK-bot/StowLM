"""Paired C1 comparisons on one identical selection (logic review: never compare ROW.md means across rows).

Wraps ``eval.harness.run_paired_evaluation``: both planners run through the same harness on the same frozen
selection, seed and time limit, into ``<run>/first`` and ``<run>/second``. ``paired.json`` and ``ROW.md`` then hold
the per-instance differences with a paired bootstrap interval (Efron & Tibshirani 1993, ch. 13). The headline uses
the **own-plan** population (D68: solver-repaired plans excluded); the shipped population is reported beside it.
Each side resumes independently.

    # StowLM vs the two-step industry baseline (E10), then StowLM vs 6.2 base + mask (what SFT buys):
    uv run python experiments/eval-c1/paired.py --first stowlm --adapter experiments/sft/stowlm-v1 \
        --second twostep --instances <test epoch root> --limits tiny=75 small=75 medium=75 full=20 \
        --c2 --no-stub --run-dir experiments/eval-c1/runs/paired-stowlm-vs-twostep
    uv run python experiments/eval-c1/paired.py --first stowlm --adapter experiments/sft/stowlm-v1 \
        --second base --instances <test epoch root> --limits tiny=75 small=75 medium=75 full=20 \
        --c2 --no-stub --run-dir experiments/eval-c1/runs/paired-stowlm-vs-base
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_c1  # noqa: E402


def make(kind: str, a, side: str):
    if kind in ("stowlm", "base"):
        from pooled_planner import PooledStowLMPlanner

        kw = dict(n_samples=a.n_samples, temperature=a.temperature, max_tokens=a.max_tokens,
                  mask_workers=a.mask_workers, c2=a.c2, trace_dir=str(Path(a.run_dir) / side))
        if a.model_path:
            kw["model_path"] = a.model_path
        if kind == "stowlm":
            if not a.adapter:
                raise SystemExit("--first/--second stowlm needs --adapter")
            kw["adapter_path"] = a.adapter
        return PooledStowLMPlanner(**kw)
    if kind == "twostep":
        from stowage.solver.twostep import TwoStepPlanner

        return TwoStepPlanner()
    if kind == "scalable":
        from stowage.solver.scalable import ScalableSolver

        return ScalableSolver()
    raise SystemExit(f"unknown planner {kind}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Paired C1 comparison on one identical frozen selection")
    ap.add_argument("--first", required=True, choices=["stowlm", "base", "twostep", "scalable"])
    ap.add_argument("--second", required=True, choices=["stowlm", "base", "twostep", "scalable"])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--instances", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--split-manifest", default=None)
    ap.add_argument("--limits", nargs="+", required=True)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--n-samples", type=int, default=8)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--max-tokens", type=int, default=20_000)
    ap.add_argument("--time-limit", type=float, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mask-workers", type=int, default=run_c1.OFFICIAL_MASK_WORKERS)
    ap.add_argument("--metric", default="weighted_nonproductive")
    ap.add_argument("--c2", action="store_true")
    ap.add_argument("--no-stub", action="store_true")
    a = ap.parse_args()
    if a.no_stub and not a.c2:
        raise SystemExit("--no-stub (official) requires --c2")
    if a.mask_workers > run_c1.HARNESS_WORKER_CAP:
        raise SystemExit(f"--mask-workers {a.mask_workers} exceeds the harness cap of {run_c1.HARNESS_WORKER_CAP}")
    if a.no_stub and a.time_limit is None:
        a.time_limit = run_c1.OFFICIAL_TIME_LIMIT_S
    import stowage.interfaces  # noqa: F401
    from eval.harness import run_paired_evaluation
    from eval.splits import DEFAULT_MANIFEST
    from stowage.frozen import stub_override

    from pool import STUB_REASON, c2_off_engine

    limits = {k: int(v) for k, v in (x.split("=") for x in a.limits)}
    first, second = make(a.first, a, "first"), make(a.second, a, "second")
    ctx = (contextlib.nullcontext() if a.c2
           else stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON))
    try:
        with ctx:
            comparison = run_paired_evaluation(
                first, second, run_dir=a.run_dir, metric=a.metric, population="own", instances_dir=a.instances,
                split=a.split, rungs=tuple(limits), limits=limits, workers=1, tiny_run=not a.no_stub, seed=a.seed,
                time_limit_s=a.time_limit, split_manifest=a.split_manifest or DEFAULT_MANIFEST)
    finally:
        for p in (first, second):
            getattr(p, "close", lambda: None)()
    print(json.dumps(comparison, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main()
