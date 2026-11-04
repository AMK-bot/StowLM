"""Debug helper: one planner.plan call on one instance (no pipeline), printing status/message/metrics."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stowage.interfaces  # noqa: F401
from stowage.frozen import stub_override
from stowage.io import load_instance
from pool import STUB_REASON, c2_off_engine
from pooled_planner import PooledStowLMPlanner

if __name__ == "__main__":
    p = PooledStowLMPlanner(n_samples=int(sys.argv[2]), temperature=1.0, mask_workers=int(sys.argv[3]), c2=False)
    with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
        r = p.plan(load_instance(sys.argv[1]), seed=0)
    p.close()
    print(r.status, r.message)
    print(json.dumps({k: v for k, v in r.metrics.items()}, default=str)[:3000])
