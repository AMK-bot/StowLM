"""Diagnostic: can a complete legal prefix from the engine be finished and checked now?"""
import json, sys, time
sys.dont_write_bytecode = True
from generate_pilot import runtime
runtime()
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1
from stowage.actions import get_action_space
from stowage.feasibility import make_feasibility_engine
from stowage.model import PlannerResult
from stowage.planner import run_planner

class Greedy:
    name, version, sets_ballast, deterministic = 'probe.greedy', '0', False, True
    def plan(self, instance, *, seed=0, time_limit_s=None):
        space = get_action_space('boxslot'); engine = make_feasibility_engine(instance, space)
        while not engine.is_complete():
            legal = engine.legal_actions()
            if not legal: return PlannerResult(status='dead_end', plan=None, planner=self.name, planner_version='0', seed=seed, metrics={'depth': engine.depth, 'dead': str(engine.dead_end())[:800]})
            engine.push(legal[0])
        return PlannerResult(status='ok', plan=engine.to_plan(), planner=self.name, planner_version='0', seed=seed, metrics={})

with rule_freeze_v1(enabled=True):
    inst = load_instance(sys.argv[1]); t = time.monotonic()
    r, rep = run_planner(Greedy(), inst, seed=0, time_limit_s=120, allow_repair=False)
    print(json.dumps(dict(status=r.status, msg=r.message, shippable=r.shippable, passed=bool(rep and rep.passed),
        rules=[v.rule_id for v in rep.violations] if rep else None, m=r.metrics, s=time.monotonic()-t), default=str))
