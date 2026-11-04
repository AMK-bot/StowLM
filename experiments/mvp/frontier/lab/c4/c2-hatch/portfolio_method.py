"""Final admission of checked C4-generated plans and carried upper fallbacks; not cold construction."""
import importlib.util
import json
from pathlib import Path
import sys
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import MovePlan

HERE = Path(__file__).resolve().parent


def propose(instance, *, seed, budget_s, context):
    name = 'c4_hatch_e9fix'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE.parents[1]/'c1/leader/e9fix.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    sys.modules[name].install()
    index = json.loads((HERE/'references/portfolio_index.json').read_text())
    row = index.get(instance.id)
    if not row:
        return []
    plan = MovePlan.model_validate(row['plan'])
    actions = tuple(a for a in get_action_space('boxslot').from_plan(instance, plan)
                    if isinstance(a, Place))
    context['_upper_plan'] = plan
    context['_upper_actions'] = actions
    context['method_metadata']['upper_evidence'] = row['evidence']
    return [actions]


def finish(instance, plan, *, seed, finalize_s, label, context):
    actions = tuple(a for a in get_action_space('boxslot').from_plan(instance, plan)
                    if isinstance(a, Place))
    if actions != context.get('_upper_actions'):
        raise ValueError('upper-plan actions changed before admission')
    return context['_upper_plan'], {'mode': 'copied upper, independent full check required'}
