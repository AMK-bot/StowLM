"""Pilot: does the forbids() hook steer the scalable solver on tiny, and how long does a tiny replan take?"""
import json, sys, time
from stowage.io import load_instance
from llm.instructions.apply import apply, describe
from llm.instructions.replan import replan
from llm.instructions.schema import CloseBay, MaxTiers

cases = [("experiments/mvp/corpus/test/tiny/feeder_500/simulated_yard/seed-008000003.json", CloseBay(type="close_bay", bay=1)),
         ("experiments/mvp/corpus/test/tiny/feeder_500/random_yard/seed-008000000.json",
          MaxTiers(type="max_tiers", bay=1, zone="hold", max_tiers=1))]
for path, c in cases[int(sys.argv[1]):int(sys.argv[2])]:
    inst = load_instance(path)
    ci = apply(inst, c)
    t = time.monotonic()
    r = replan(ci, time_limit_s=float(sys.argv[3]))
    print(json.dumps(dict(instance=inst.id, constraint=c.model_dump(), effect=describe(ci), precheck=r.precheck,
        status=r.result and r.result.status, message=r.result and r.result.message, passed=r.report and r.report.passed,
        shippable=r.result and r.result.shippable, honoured=r.honoured, violations=r.constraint_violations,
        wall_s=round(time.monotonic() - t, 1),
        moves=r.plan and [(m.kind, getattr(m, "container_id", None), getattr(m, "dst", None)) for m in r.plan.moves])), flush=True)
