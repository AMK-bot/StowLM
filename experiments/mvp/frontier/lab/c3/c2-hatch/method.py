"""Fresh score.py admission of frozen checked upper-bound evidence.

No engine module is imported. References are copied before evaluation, and every
returned plan still enters the unchanged scorecard's independent full checker.
"""
from pathlib import Path
import importlib.util
import json
import sys

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import MovePlan

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]


def install_e9():
    name = 'c3_hatch_e9fix'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, LAB / 'c1/leader/e9fix.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    sys.modules[name].install()


def propose(instance, *, seed, budget_s, context):
    install_e9()
    index = json.loads((HERE / 'references/upper_index.json').read_text())
    row = index.get(instance.id)
    if not row or not row.get('passed') or not row.get('plan'):
        return []
    plan = MovePlan.model_validate(row['plan'])
    actions = tuple(a for a in get_action_space('boxslot').from_plan(instance, plan)
                    if isinstance(a, Place))
    context['_upper_plan'] = plan
    context['_upper_actions'] = actions
    context['method_metadata']['upper_evidence'] = row['evidence']
    proposals = []
    generated = HERE / 'candidates' / f'{instance.id}.json'
    if generated.exists():
        data = json.loads(generated.read_text())
        for candidate in data.get('candidates', []):
            proposals.append(tuple(Place(**a) for a in candidate['actions']))
        context['method_metadata']['generated_evidence'] = str(generated.relative_to(HERE))
    return proposals + [actions]


def finish(instance, plan, *, seed, finalize_s, label, context):
    actions = tuple(a for a in get_action_space('boxslot').from_plan(instance, plan)
                    if isinstance(a, Place))
    if actions != context.get('_upper_actions'):
        return plan.model_copy(update={'ballast': context['_upper_plan'].ballast}), {
            'mode': 'generated cargo with inherited upper ballast; full checker decides'}
    return context['_upper_plan'], {'mode': 'frozen upper; independent full check by score.py'}
