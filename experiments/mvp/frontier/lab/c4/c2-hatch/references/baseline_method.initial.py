"""Fresh 0.9.0 + E9 control with the contract's original validation budgets."""
import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def _module():
    name = 'c4_hatch_fresh09_e9_control'
    if name not in sys.modules:
        source = HERE.parents[1]/'c1/leader/method_e9_val.py'
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def propose(instance, *, seed, budget_s, context):
    context['method_metadata']['load_before_control'] = os.getloadavg()
    context['method_metadata']['control'] = 'fresh Frontier 0.9.0 plus E9 only, original validation budgets'
    return _module().propose(instance, seed=seed, budget_s=budget_s, context=context)


def finish(instance, plan, *, seed, finalize_s, label, context):
    return _module().finish(instance, plan, seed=seed, finalize_s=finalize_s,
                            label=label, context=context)
