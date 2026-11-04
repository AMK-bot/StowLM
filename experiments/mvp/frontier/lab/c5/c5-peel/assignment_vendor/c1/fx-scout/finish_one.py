"""Page-model stow on one map, through Sequencer, real replay, V4 finisher and the full checker."""
from pathlib import Path
import json, random, sys
from time import monotonic, perf_counter
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
from alns import Problem, Sequencer
from pages import Model
from planner import _finish
from stowage.io import load_instance
from stowage.solver.securing_geometry import securing_state_violations
from stowage.actions.expander import Expander
from stowage.moves import apply_plan

path, finalize_s = sys.argv[1], float(sys.argv[2])
instance = load_instance(ROOT/path); problem = Problem(instance)
t0 = monotonic()
model = Model(problem)
model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1, deadline=monotonic()+30)
chosen, info = model.dive(deadline=monotonic()+60)
print('dive', info, round(monotonic()-t0, 1), flush=True)
a = model.assignment(chosen)
print('empty hold', model.empty_hold_cells(chosen), 'mix', problem.mixes_hold_stack(a))
o = Sequencer(problem).order(a)
print('order', o and len(o), flush=True)
s = problem.score(o)
print('score', s and s.objective, flush=True)
if s:
    c = apply_plan(instance, s.plan).count
    print({k: getattr(c, k) for k in ('relocations', 'restow_lifts', 'hatch_cover_lifts')}, [(f.hatch_cover_lifts, f.restow_lifts) for f in c.future], flush=True)
    r, row = _finish(instance, s.plan, seed=0, finalize_s=finalize_s, label='pages')
    print({k: row.get(k) for k in ('finalize_status', 'finalize_s', 'passed', 'weighted', 'violations', 'message')}, flush=True)
