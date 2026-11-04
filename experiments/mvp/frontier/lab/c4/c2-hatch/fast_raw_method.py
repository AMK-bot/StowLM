"""Admit only the cached first raw CP witness, with independent cold finishing."""
import json
from hashlib import sha256
from pathlib import Path
from stowage.actions.space import Place

HERE = Path(__file__).resolve().parent


def propose(instance, *, seed, budget_s, context):
    import importlib.util
    import sys
    name = 'c4_hatch_raw_e9fix'
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
        raise ValueError('raw witness input/source/seed does not match construction evidence')
    context['method_metadata']['original_construction'] = {
        k: row.get(k) for k in ('constructor_sha256', 'first_model_witness',
                               'wall_time_s', 'budget_s', 'seed', 'load')}
    actions = tuple(Place(**a) for a in row.get('first_model_actions', []))
    return [actions] if actions else []
