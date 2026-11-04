"""Admit saved ablation candidates with inherited, rechecked ballast.

This isolates search from a repeated ballast search. It makes no fresh V4
finisher speed claim: the unchanged full checker must accept the complete
candidate with the inherited tank masses.
"""
import json
from pathlib import Path

from stowage.actions.space import Place
from stowage.model import MovePlan, PlannerResult

HERE = Path(__file__).resolve().parent


def propose(instance, *, seed, budget_s, context):
    path = HERE/'results'/'ablation8'/f'{instance.id}.json'
    if not path.exists():
        return []
    record = json.loads(path.read_text())
    context['method_metadata'].update(saved_ablation=record.get('arms'),
                                      union_of_search_arms=True,
                                      inherited_ballast=True)
    return [tuple(Place(a['box'],a['slot']) for a in row['actions'])
            for row in record.get('candidates',[])][:6]


def finish(instance, plan, *, seed, finalize_s, label, context):
    stored = context.get('stored_plan')
    if stored is None:
        return None, dict(message='no inherited ballast certificate')
    stored = MovePlan.model_validate(stored) if isinstance(stored,dict) else stored
    if stored.ballast is None:
        return None, dict(message='stored plan has no ballast')
    candidate = plan.model_copy(update={'ballast':stored.ballast})
    return PlannerResult(status='ok', plan=candidate, planner='fx-vlsn.inherited-ballast',
                         planner_version='experimental', seed=seed), dict(inherited_ballast=True)
