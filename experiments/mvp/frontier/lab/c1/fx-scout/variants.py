"""Try deck caps and mass derates on one map: page model, then real replay, sight, lashing, full check with default tanks."""
from pathlib import Path
from collections import Counter
import json, random, sys
from time import monotonic
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
from alns import Problem, Sequencer
from pages import Model
from sight import sight_clear
from stowage.checker import check
from stowage.io import load_instance
from lash_oracle import Fast
from stowage.moves import apply_plan
instance = load_instance(ROOT/sys.argv[1]); problem = Problem(instance)
reference = json.loads((Path(__file__).parent/'stow_4_1.0.json').read_text())
ship = apply_plan(instance, problem.score(Sequencer(problem).order(reference)).plan).final_state.ship
oracle = Fast(instance, ship)
for tiers, factor in [tuple(json.loads(x)) for x in sys.argv[2:]]:
    t0 = monotonic()
    model = Model(problem, deck_tiers=tiers, deck_mass_factor=factor, oracle=oracle); print('types', len(model.types))
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1, deadline=monotonic()+100)
    print('== tiers', tiers, 'factor', factor, 'LP uncovered', round(root['uncovered'], 2), 'proved', root['proved'], flush=True)
    if root['uncovered'] > 1e-6:
        T = model.T
        print(' short', [(T.box_ids[b], round(v, 2), int(T.length[b]), round(float(T.mass[b])/1000, 1), round(float(T.height[b]), 2), bool(T.is_dg[b]), bool(T.closes_stack[b]), bool(T.fallback[b]), int(T.static[b].sum())) for b, v in model.short.items()], 'time', round(monotonic()-t0), 'cols', len(model.columns), 'refused', model.refused, flush=True)
        print(' factors', sorted({round(v, 2) for v in model.factor.values()})[:8], flush=True)
        continue
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=3, deadline=monotonic()+120)
    print(' root phase 3', round(root['objective'], 2), root['rounds'], len(model.columns), round(monotonic()-t0, 1), flush=True)
    chosen, info = model.dive(deadline=monotonic()+150, log=lambda m: print(' ', m, flush=True) if 'tail' in m else None)
    print(' dive', info, round(monotonic()-t0, 1), 'banned', len(model.banned), 'refused', model.refused, flush=True)
    if not chosen:
        continue
    heel0 = model.moments(chosen)
    chosen, heel = model.balance(chosen, random.Random(0))
    a = model.assignment(chosen)
    o = Sequencer(problem).order(a)
    s = problem.score(o) if o else None
    print(' heel', round(heel0), '->', round(heel), 'empty hold', model.empty_hold_cells(chosen), 'mix', problem.mixes_hold_stack(a), 'lifts', s and s.objective, flush=True)
    if s is None:
        continue
    print(' sight/lashing', sight_clear(instance, s.plan, {}), flush=True)
    r = check(instance, s.plan)
    print(' check', Counter(v.rule_id for v in r.violations).most_common(9), flush=True)
    worst = {}
    for v in r.violations:
        if v.measured is not None and v.rule_id not in worst:
            worst[v.rule_id] = (round(v.measured, 3), round(v.limit, 3) if v.limit else None, v.message[:60])
    print(' ', worst, flush=True)
    Path(__file__).with_name(f'stowL_{str(tiers).replace(" ","")}_{factor}.json').write_text(json.dumps(a))
