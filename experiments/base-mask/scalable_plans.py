# Re-plan the 24 pilot instances with solver.scalable (plan only, same C2-off stub as run.py) to get the planner's own
# status and D11 objective before finalize/repair (the harness record keeps the post-repair plan).
import json
from pathlib import Path
from time import perf_counter

import stowage.interfaces  # noqa: F401
from eval.harness import select_instances
from run import STUB_REASON, c2_off_engine
from stowage.frozen import stub_override
from stowage.io import load_instance
from stowage.moves import count_moves
from stowage.solver.scalable import ScalableSolver

ROOT = Path(__file__).resolve().parents[2]
rows = select_instances(ROOT / "experiments/base-mask/instances-v3-val", split="val", rungs=("tiny", "small"),
                        limits={"tiny": 12, "small": 12})
out = {}
with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
    for r in rows:
        inst = load_instance(r["path"])
        t = perf_counter()
        res = ScalableSolver().plan(inst, seed=0)
        obj = None
        if res.status == "ok" and res.plan is not None:
            c = count_moves(inst, res.plan)
            obj = c.weighted_nonproductive if c.loads == len(inst.load_list) else None
        out[r["spec_key"]] = dict(status=res.status, objective=obj, plan_s=round(perf_counter() - t, 2),
                                  message=(res.message or "")[:150])
        print(inst.id, out[r["spec_key"]], flush=True)
(ROOT / "experiments/base-mask/scalable_plans.json").write_text(json.dumps(out, indent=1))
