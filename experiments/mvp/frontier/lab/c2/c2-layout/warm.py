"""Diagnostic warm starts from Cycle 1's checked reference ship condition."""
from pathlib import Path
import importlib.util
import json
import random
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.path.insert(0, str(LAB/'c1/fx-scout'))
sys.path.insert(0, str(LAB/'c1/leader'))
import e9fix
e9fix.install()
from pages import Model
from lash_oracle import Fast
from alns import Sequencer
from stowage.checker import check
from stowage.moves import apply_plan
from stowage.model.plan import MovePlan
sys.path.insert(0, str(HERE))
from repair import repair_oog

spec = importlib.util.spec_from_file_location('c1_layout_reference', LAB/'c1/fx-scout/method.py')
scout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scout)
finish = scout.finish


def propose(instance, *, seed, budget_s, context):
    p = context['problem']
    path = LAB/'c1/fx-scout/results/quick-3'/f'{instance.id}.json'
    if not path.exists():
        return []
    old = json.loads(path.read_text())
    if not old.get('passed'):
        return []
    ship = apply_plan(instance, MovePlan.model_validate(old['plan'])).final_state.ship
    oracle = Fast(instance, ship)
    settings = dict(deck_tiers=3 if len(instance.load_list)>120 else 4,
                    lash_limit=.94)
    if len(instance.load_list)<=120:
        settings.update(lcg_target=54.30520322025337, hold_reward=3.)
    base = Model(p, oracle=oracle, **settings)
    layout = scout.block_layout(base)
    meta = context.setdefault('method_metadata', {})
    meta['warm_reference'] = str(path)
    meta['trials'] = []
    candidates = [tuple(scout.Sequencer(p).order(old['assignment']))]
    deadline = monotonic()+budget_s
    for mode in (True, False):
        if monotonic()>=deadline:
            break
        model = Model(p, oracle=oracle, layout=layout, layout_deck=mode, **settings)
        start = monotonic()
        root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                              phase=1, deadline=min(deadline, start+15))
        row = dict(mode=mode, uncovered=root['uncovered'])
        meta['trials'].append(row)
        if root['uncovered']>3:
            continue
        model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                       phase=3, max_rounds=30, deadline=min(deadline,start+30))
        chosen, info = model.dive(deadline=min(deadline,start+60), rng=random.Random(seed+int(mode)))
        row.update(info)
        if chosen is None:
            continue
        chosen, heel = model.balance(chosen, random.Random(seed))
        assignment = repair_oog(p, model, model.clear_oog(model.assignment(chosen)))
        actions = Sequencer(p).order(assignment)
        scored = p.score(actions) if actions else None
        row.update(heel=heel, lifts=scored.objective if scored else None)
        if scored:
            report = check(instance, scored.plan)
            row.update(passed=report.passed, rules=sorted({v.rule_id for v in report.violations}))
            candidates.append(scored.actions)
    return candidates
