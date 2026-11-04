"""Cold exact-model construction adapter: no incumbent cargo or ballast."""
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from constructor import construct


def _jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def propose(instance, *, seed, budget_s, context):
    result = construct(instance, budget_s=budget_s, seed=seed,
                       problem=context['problem'], tables=context['problem'].engine.T)
    diagnostics = _jsonable(asdict(result))
    diagnostics.update(instance_id=instance.id,
                       input=context['instance_path'],
                       input_sha256=sha256(Path(context['instance_path']).read_bytes()).hexdigest(),
                       constructor_sha256=sha256((HERE/'constructor.py').read_bytes()).hexdigest(),
                       load=os.getloadavg(), budget_s=budget_s, seed=seed,
                       cold_cargo=True, cold_ballast=True)
    folder = HERE/'results/construction-diagnostics'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/f'{instance.id}.json'
    target.write_text(json.dumps(diagnostics, indent=2)+'\n')
    context['method_metadata']['construction'] = diagnostics
    return [result.actions] if result.actions else []
