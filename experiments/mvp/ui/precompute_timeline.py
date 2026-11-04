"""Precompute the stage page's physics timeline (checker stability + strength per keyframe) into the UI cache.

    PYTHONPATH=.:src uv run python experiments/mvp/ui/precompute_timeline.py --instance I.json --plan P.json
    PYTHONPATH=.:src uv run python experiments/mvp/ui/precompute_timeline.py --run-folder R [--entry E] [--workers 2]

Inputs go through ``app.inputs.load_inputs`` (same identity gates as the demo server). Output:
``experiments/mvp/ui/cache/<sha256(instance json + plan json)[:16]>.physics.json`` with both input digests and the
physics epoch inside (format version, checker epoch over src/params/hulls and the numerical environment, and the app
modules that shape frames); the server uses it only when all of these match, so ANY src/params change means running
this again. The run is resumable within one epoch: every finished keyframe is checkpointed, and a rerun skips frames
already in the file; frames from an older epoch are recomputed, never resumed.
Run with ``nice`` and single-threaded BLAS (OPENBLAS/OMP/VECLIB_NUM_THREADS=1); keep --workers <= 4.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(var, "1")

from app.inputs import load_inputs  # noqa: E402
from app import stage_physics  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--instance")
    parser.add_argument("--plan")
    parser.add_argument("--run-folder")
    parser.add_argument("--entry")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cache-dir", default=str(stage_physics.CACHE_DIR))
    parser.add_argument("--max-frames", type=int, help="Compute only the first N pending keyframes (timing probe)")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be between 1 and 4 (shared machine)")
    inputs = load_inputs(instance_path=args.instance, plan_path=args.plan, run_folder=args.run_folder, entry=args.entry)
    if inputs.plan is None:
        parser.error("No plan to replay")
    instance, plan = inputs.instance, inputs.plan
    cache_dir = Path(args.cache_dir)
    if stage_physics.load_cached(instance, plan, cache_dir) is not None:
        print("cache already complete:", stage_physics.cache_path(stage_physics.input_digests(instance, plan), cache_dir))
        return 0
    print(f"physics epoch {stage_physics.physics_epoch()[:16]}", flush=True)
    frames, policy = stage_physics.select_keyframes(instance, plan)
    done = stage_physics.load_partial(instance, plan, cache_dir)
    pending = [k for k in frames if k not in done]
    if args.max_frames is not None:
        pending = pending[:args.max_frames]
    print(f"{instance.id}: {len(plan.moves)} lifts, {len(frames)} keyframes ({policy}); "
          f"{len(done)} checkpointed, computing {len(pending)} with {args.workers} worker(s)", flush=True)
    started = time.monotonic()

    def checkpoint(k, frame):
        done[k] = frame
        stage_physics.write_cache(instance, plan, None, partial=done, cache_dir=cache_dir)
        print(f"  frame {k}: {time.monotonic() - started:.1f}s  gm={frame.get('gm_m')}  err={frame.get('error')}",
              flush=True)

    stage_physics.compute_physics(instance, plan, frames=pending, workers=args.workers, done={},
                                  on_frame=checkpoint)
    if all(k in done for k in frames):
        physics = stage_physics.compute_physics(instance, plan, frames=frames, done=done)
        physics["keyframes"] = policy
        path = stage_physics.write_cache(instance, plan, physics, cache_dir=cache_dir)
        if stage_physics.load_cached(instance, plan, cache_dir) is None:
            print(f"ERROR: {path} does not load back as a current complete cache", file=sys.stderr)
            return 1
        print(f"complete in {time.monotonic() - started:.1f}s -> {path}")
    else:
        print(f"partial ({len(done)}/{len(frames)}) checkpointed; rerun to resume")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
