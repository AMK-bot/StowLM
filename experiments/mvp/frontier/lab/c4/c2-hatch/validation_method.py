"""Frozen generic exact construction for validation proof counts, warm ballast."""
import json
from pathlib import Path
import sys
from stowage.model import MovePlan

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from method import propose as construct


def propose(instance, *, seed, budget_s, context):
    candidates = construct(instance, seed=seed, budget_s=budget_s, context=context)
    index = json.loads((HERE/'references/upper_index.json').read_text())
    context['_upper_ballast'] = MovePlan.model_validate(index[instance.id]['plan']).ballast
    context['method_metadata']['construction']['cold_ballast'] = False
    context['method_metadata']['validation_use'] = 'proof counts only; generated cargo with inherited upper ballast'
    return candidates


def finish(instance, plan, *, seed, finalize_s, label, context):
    return plan.model_copy(update={'ballast': context['_upper_ballast']}), {
        'mode': 'cold exact cargo, inherited upper ballast, independent full check'}
