# Plan 6.2 pilot: untuned Glimmer 4-bit under the 5.1 mask (StowLMPlanner best-of-k) vs solver.scalable, on the same
# val instances, through the 5.2 harness (run_evaluation -> run_planner: plan -> finalize -> check -> repair).
# --no-stub selects the official C2-on path after current-epoch requalification. The legacy default remains a
# labelled C2-off diagnostic (tiny_run=True); its results never qualify the production ablation.
# Usage: uv run python experiments/base-mask/run.py --planner base|scalable --run-dir DIR [--n-samples 32] [--temperature 1]
#        [--limits tiny=12 small=12] [--instances experiments/base-mask/instances-v3-val]
import argparse
import json
from dataclasses import replace

import stowage.interfaces  # noqa: F401
from eval.harness import run_evaluation
from stowage.feasibility import FeasibilityConfig
from stowage.frozen import stub_override
from eval.splits import DEFAULT_MANIFEST

STUB_REASON = ("6.2 pilot: 3.7 linear policy epoch stale (cache_warm exit 2), so the engine's C2 global screen is off "
               "(extra global_screen=0); L1/L2 local checks and C1 stay on; the full checker judges every plan")


def c2_off_engine(instance, space, *, config=None):
    from stowage.feasibility._engine import IncrementalEngine

    c = config or FeasibilityConfig()
    return IncrementalEngine(instance, space, replace(c, extra={**c.extra, "global_screen": 0}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--planner", choices=["base", "scalable"], required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--instances", default="experiments/base-mask/instances-v2-val-current")
    ap.add_argument("--split-manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--model-path", default=None, help="local Glimmer weights; content digest is recorded")
    ap.add_argument("--limits", nargs="+", default=["tiny=12", "small=12"])
    ap.add_argument("--n-samples", type=int, default=32)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--time-limit", type=float, default=None)
    ap.add_argument("--no-stub", action="store_true",
                    help="official path: real engine (C2 on), no stub, tiny_run=False (needs cache_warm exit 0)")
    args = ap.parse_args()
    limits = {k: int(v) for k, v in (x.split("=") for x in args.limits)}
    if args.planner == "base":
        from llm.stowlm.planner import StowLMPlanner

        planner = StowLMPlanner(n_samples=args.n_samples, temperature=args.temperature,
                                **({"model_path": args.model_path} if args.model_path else {}))
    else:
        from stowage.solver.scalable import ScalableSolver

        planner = ScalableSolver()
    import contextlib

    ctx = (contextlib.nullcontext() if args.no_stub
           else stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON))
    with ctx:
        summary = run_evaluation(planner, run_dir=args.run_dir, instances_dir=args.instances, split="val",
                                 rungs=tuple(limits), limits=limits, workers=1, tiny_run=not args.no_stub, seed=0,
                                 time_limit_s=args.time_limit, split_manifest=args.split_manifest)
    print(json.dumps({k: v for k, v in summary.items() if k != "by_rung"}, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main()
