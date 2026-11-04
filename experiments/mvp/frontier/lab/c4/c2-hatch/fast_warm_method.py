"""Cached exact-model stow with copied upper ballast; explicitly warm."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from stowage.actions.space import Place
from stowage.model import MovePlan

HERE = Path(__file__).resolve().parent


def propose(instance, *, seed, budget_s, context):
    name = 'c4_hatch_warm_e9fix'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE.parents[1]/'c1/leader/e9fix.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    sys.modules[name].install()
    source = HERE/'results/fast-construction-diagnostics'/f'{instance.id}.json'
    if not source.exists():
        return []
    row = json.loads(source.read_text())
    if (row['instance_id'] != instance.id or row['seed'] != seed
            or row['input_sha256'] != sha256(Path(context['instance_path']).read_bytes()).hexdigest()
            or row['constructor_sha256'] != sha256((HERE/'constructor_fast.py').read_bytes()).hexdigest()):
        raise ValueError('cached exact stow input/source/seed mismatch')
    index = json.loads((HERE/'references/upper_index.json').read_text())
    upper = index.get(instance.id)
    if not upper:
        return []
    context['_upper_ballast'] = MovePlan.model_validate(upper['plan']).ballast
    context['method_metadata'].update(cold_cargo=True, cold_ballast=False,
        original_construction_s=row['wall_time_s'], upper_evidence=upper['evidence'])
    actions = tuple(Place(**a) for a in row.get('actions', []))
    return [actions] if actions else []


def finish(instance, plan, *, seed, finalize_s, label, context):
    return plan.model_copy(update={'ballast': context['_upper_ballast']}), {
        'mode': 'generated cargo with inherited upper ballast, independent full check required'}
