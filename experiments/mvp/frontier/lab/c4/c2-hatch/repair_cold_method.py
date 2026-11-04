"""Cold ballast finishing of the fixed generic repaired relaxation witnesses."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from repair_method import propose as realize


def propose(instance, *, seed, budget_s, context):
    context['_repair_include_unflagged'] = True
    return realize(instance, seed=seed, budget_s=budget_s, context=context)


def finish(instance, plan, *, seed, finalize_s, label, context):
    from planner import _finish
    return _finish(instance, context['_repair_plan'], seed=seed,
                   finalize_s=finalize_s, label=label)
