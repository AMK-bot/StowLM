"""Write a real stage bundle (stage-v1) to a JSON file for frontend development.

    PYTHONPATH=.:src uv run python experiments/mvp/ui/write_fixture.py \
        --instance experiments/mvp/smoke/run-20261029/tiny-feeder_500-0/instance.json \
        --plan experiments/mvp/smoke/run-20261029/tiny-feeder_500-0/plan.json \
        [--compare-plan BASELINE.json | --compare-run-folder R2 --compare-entry E2] [--run-folder R --entry E] --out experiments/mvp/ui/fixtures/stage-tiny.json

Physics comes from the precompute cache only (run precompute_timeline.py first); no live jobs are started.
A baseline appears if ``--compare-plan``/``--compare-run-folder`` registers a two-step baseline plan for the same
instance, or with ``--live-baseline`` (runs ``TwoStepPlanner`` once, measured, on tiny/small instances).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.flow import DemoService  # noqa: E402
from app.inputs import load_inputs  # noqa: E402
from app.server import json_bytes  # noqa: E402
from app.stage_service import StageService  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance")
    parser.add_argument("--plan")
    parser.add_argument("--run-folder")
    parser.add_argument("--entry")
    parser.add_argument("--compare-plan", action="append", default=[])
    parser.add_argument("--compare-run-folder")
    parser.add_argument("--compare-entry")
    parser.add_argument("--live-baseline", action="store_true",
                        help="Run the two-step baseline (D79 C2 off, tiny/small only) when none is registered")
    parser.add_argument("--lite-replay", action="store_true",
                        help="Lite replay (app.stage_fast) for very large plans: no per-frame states, checker once")
    parser.add_argument("--companions", action="store_true",
                        help="Also register the base Glimmer + mask run and the Muse Spark plan for the same instance "
                             "(app.stage_extras.companion_inputs), as the no-argument server does")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    inputs = load_inputs(instance_path=args.instance, plan_path=args.plan, run_folder=args.run_folder,
                         entry=args.entry)
    others = [load_inputs(instance_path=args.instance, plan_path=p, selected_instance=inputs.instance)
              for p in args.compare_plan]
    if args.compare_run_folder:
        others.append(load_inputs(run_folder=args.compare_run_folder, entry=args.compare_entry,
                                  selected_instance=inputs.instance))
    if args.companions:
        from app.stage_extras import companion_inputs
        extra, notes = companion_inputs(inputs.instance, exclude_planners=(inputs.metadata.get("planner"),))
        others.extend(extra)
        for note in notes:
            print("note:", note)
    initial = None
    if args.lite_replay:
        from app.flow import SOURCE_KEYS
        from app.stage_fast import build_lite_replay
        from stowage.model import PlannerResult
        raw = inputs.metadata.get("planner_result")
        started = time.monotonic()
        initial = build_lite_replay(inputs.instance, inputs.plan, run_checker=True,
                                    planner_result=PlannerResult.model_validate(raw) if raw else None)
        initial["metadata"].update({k: v for k, v in inputs.metadata.items() if k in SOURCE_KEYS})
        print(f"lite replay incl. full checker: {time.monotonic() - started:.1f}s", flush=True)
    demo = DemoService(inputs.instance, inputs.plan, inputs.metadata, others, initial_replay=initial, discover=False)
    stage = StageService(demo, live_physics=False, live_baseline=args.live_baseline)
    stage.bundle(demo.active_plan_id)  # queues a registered-baseline replay if one was supplied
    stage.wait_idle()
    started = time.monotonic()
    bundle = stage.bundle(demo.active_plan_id)
    built = time.monotonic() - started
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(json_bytes(bundle))
    print(f"wrote {args.out}: {len(bundle['steps'])} lifts, bundle {built:.2f}s, physics={bundle['physics_status']}, "
          f"baseline={'yes' if bundle['comparison']['baseline'] else bundle['comparison']['reasons']['baseline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
