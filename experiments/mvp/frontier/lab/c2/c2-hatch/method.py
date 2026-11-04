"""Replay measured repair artifacts and retain fully checked Cycle 1 incumbents.

This is explicitly a warm-start certificate experiment. score.py independently
checks every returned finished plan; prior passing flags only select incumbents.
"""
from pathlib import Path
import importlib.util
import json
import sys

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import MovePlan

HERE = Path(__file__).resolve().parent
C1 = HERE.parents[1] / 'c1'


def _install():
    spec = importlib.util.spec_from_file_location('c2_hatch_e9fix', C1 / 'leader/e9fix.py')
    module = sys.modules.get(spec.name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    module.install()


def _incumbents(instance, context):
    rows = []
    stored = context.get('stored_plan')
    if stored:
        rows.append((float(context['stored_lifts']), stored, 'stored 0.9.0'))
    for directory in ('fx-fvs/results/combined-quick',
                      'fx-vlsn/results/candidate_check'):
        path = C1 / directory / f'{instance.id}.json'
        if path.exists():
            row = json.loads(path.read_text())
            if row.get('passed') and row.get('plan'):
                rows.append((row['lifts'], row['plan'], str(path.relative_to(C1))))
    if instance.id == 'sim.small.40100034':
        path = C1 / 'fx-chains/results/small-checker-diagnostic/finisher-result.json'
        data = json.loads(path.read_text())
        rows.append((14., data['plan'], 'c1 manual special-box diagnostic'))
    return sorted(rows, key=lambda row: row[0])


def propose(instance, *, seed, budget_s, context):
    _install()
    space = get_action_space('boxslot')
    candidates, plans = [], []
    incumbents = _incumbents(instance, context)
    for lifts, raw, origin in incumbents:
        plan = MovePlan.model_validate(raw)
        actions = tuple(a for a in space.from_plan(instance, plan) if isinstance(a, Place))
        if actions not in candidates:
            candidates.append(actions)
            plans.append((actions, plan, origin))
    context['_hatch_plans'] = plans
    context.setdefault('method_metadata', {})['incumbents'] = [
        {'lifts': x[0], 'origin': x[2]} for x in incumbents]
    # A frozen offline artifact makes search and full-check costs explicit.
    artifact = HERE / 'results/repair_candidates' / f'{instance.id}.json'
    if artifact.exists():
        data = json.loads(artifact.read_text())
        for item in data.get('candidates', []):
            actions = tuple(Place(a['box'], a['slot']) for a in item['actions'])
            if actions not in candidates:
                candidates.insert(0, actions)
        context['method_metadata']['repair_artifact'] = str(artifact.relative_to(HERE))
        context['method_metadata']['offline_search'] = data.get('metadata', {})
    context['method_metadata']['candidate_count'] = len(candidates)
    return candidates


def finish(instance, plan, *, seed, finalize_s, label, context):
    actions = tuple(a for a in get_action_space('boxslot').from_plan(instance, plan)
                    if isinstance(a, Place))
    plans = context.get('_hatch_plans', [])
    for before, complete, origin in plans:
        if before == actions:
            return complete, {'mode': 'retained finished plan', 'origin': origin,
                              'gate': 'independent full checker in score.py'}
    if plans:
        return plan.model_copy(update={'ballast': plans[0][1].ballast}), {
            'mode': 'changed cargo with incumbent ballast',
            'gate': 'independent full checker in score.py'}
    return None, {'message': 'no finished ballast incumbent; construction not attempted'}
