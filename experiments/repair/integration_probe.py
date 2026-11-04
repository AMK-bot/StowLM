"""One bounded native repair attempt on an unmodified generated tiny instance."""
from __future__ import annotations
import json
from time import monotonic

if __name__ == '__main__':
    from stowage.actions import get_action_space
    from stowage.generate.random_yard import generate_random_instance
    from stowage.model import PlannerResult
    from stowage.solver.repair import repair
    started = monotonic()
    instance = generate_random_instance('tiny', seed=12, blocking_ratio=0., max_attempts=3, c2_screen=True)
    print(json.dumps({'generation_s':monotonic()-started,'loads':len(instance.load_list)}), flush=True)
    original = PlannerResult(status='dead_end', plan=get_action_space().expand(instance, []),
                             planner='synthetic-partial',planner_version='test')
    result = repair(instance, original, None, seed=12, time_limit_s=30.)
    print(json.dumps({'status':result.status,'message':result.message,'metrics':result.metrics,
                      'shippable':result.shippable,'total_s':monotonic()-started},default=str), flush=True)
