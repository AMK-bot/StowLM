"""Fresh 0.9.0 + E9 control with the contract's original validation budgets."""
import importlib.util
import os
from pathlib import Path
import sys
from time import monotonic, sleep

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
    # Yield this executive's sole CPU slot between arms, never inside a timed
    # search/finisher. Record idle time separately from measured work.
    started = monotonic()
    pause = HERE/'PAIRED-PAUSE'
    acknowledgment = HERE/'results/paired-idle.json'
    if pause.exists():
        import json
        acknowledgment.write_text(json.dumps({'pid': os.getpid(),
            'instance_id': instance.id, 'state': 'idle_before_control'})+'\n')
        while pause.exists():
            sleep(1.)
        acknowledgment.unlink(missing_ok=True)
    context['method_metadata']['control_idle_s'] = monotonic()-started
    context['method_metadata']['load_before_control'] = os.getloadavg()
    context['method_metadata']['control'] = 'fresh Frontier 0.9.0 plus E9 only, original validation budgets'
    return _module().propose(instance, seed=seed, budget_s=budget_s, context=context)


def finish(instance, plan, *, seed, finalize_s, label, context):
    return _module().finish(instance, plan, seed=seed, finalize_s=finalize_s,
                            label=label, context=context)
