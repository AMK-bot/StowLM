"""Fifteen-second one-core sizing pilot; not an approved departure plan."""
import json
import os
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
from stowage.io import load_instance
from stowage.feasibility import FeasibilityConfig
from stowage.solver.scalable import ScalableConfig, ScalableSolver
from stowage.rules.catalogue import rule_freeze_v1_enabled

assert not rule_freeze_v1_enabled()
instance = load_instance(ROOT / "experiments/mvp/corpus/showcase/feeder_1700-full.json")
start = time.monotonic()
result = ScalableSolver(ScalableConfig(feasibility_config=FeasibilityConfig(extra={"global_screen": 0}))).plan(
    instance, seed=0, time_limit_s=15)
out = ROOT / "experiments/mvp/runs/showcase-solver/pilot"
out.mkdir(parents=True, exist_ok=True)
(out / "result.json").write_text(result.model_dump_json(indent=2)+"\n")
meta = dict(wall_s=time.monotonic()-start, peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            status=result.status, moves=len(result.plan.moves) if result.plan else 0, metrics=result.metrics)
(out / "timing.json").write_text(json.dumps(meta, indent=2)+"\n")
print(json.dumps(meta), flush=True)
