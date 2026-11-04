"""CPU-only isolated securing query at a saved plan's penultimate prefix."""
import cProfile
import json
from pathlib import Path
import pstats
from time import perf_counter

from llm.lookahead.mask_filter import LookaheadEngine
from llm.maskspeed.replay import ROOT, saved_traces, instance_for
from llm.securing.mask_filter import SecuringEngine
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine


trace = next(t for t in saved_traces() if t['expected_status'] == 'complete')
instance = instance_for(trace)
space = get_action_space()
engine = make_feasibility_engine(instance, space,
    config=FeasibilityConfig(extra={'global_screen': 0}))
for encoded in trace['actions'][:-1]:
    engine.push(space.decode(encoded))
wrapped = SecuringEngine(LookaheadEngine(engine))
profiler = cProfile.Profile()
started = perf_counter()
profiler.enable()
legal = wrapped.legal_actions()
profiler.disable()
elapsed = perf_counter() - started
folder = ROOT / 'experiments/mvp/maskspeed'
profiler.dump_stats(str(folder / 'securing-tail.prof'))
result = dict(trace=trace['id'], map=trace['map'], depth=wrapped.depth,
              legal_actions=len(legal), wall_s=elapsed, screen_metrics=wrapped.screen.metrics)
(folder / 'securing-tail.json').write_text(json.dumps(result, indent=2)+'\n')
with (folder / 'securing-tail-profile.txt').open('w') as output:
    pstats.Stats(profiler, stream=output).sort_stats('cumtime').print_stats(50)
print(json.dumps(result))
