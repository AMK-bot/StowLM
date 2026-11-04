"""Use frozen public provenance without opening any valsplit or sealed lists."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FORBIDDEN = ('experiments/mvp/valsplit/', 'experiments/mvp/frontier/lab/c6/c6-maps/',
             'experiments/mvp/testv3/', 'data/raw/rcsppsuite/',
             'experiments/mvp/frontier/runs/test-', 'experiments/mvp/frontier/runs/unseen15-',
             'experiments/mvp/refs_tools/small-v21-instances.txt')


def install(module=None):
    """Adapt only input authorization; checker, solver and selected sets are unchanged."""
    if module is None:
        import catalog as module
    if getattr(module, '_engine_public_provenance', False):
        return module
    allowed = frozenset(json.loads((HERE/'approved-maps.json').read_text()))
    original_load_set = module.load_set

    def forbidden(path):
        raw = str(path).replace('\\', '/')
        return any(part in raw for part in FORBIDDEN)

    def ensure_safe(path):
        if forbidden(path):
            raise ValueError(f'Sealed or prohibited input: {path}')
        raw = Path(path)
        absolute = (ROOT/raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            relative = absolute.relative_to(ROOT).as_posix()
        except ValueError as error:
            raise ValueError('Input is outside this repository') from error
        if forbidden(relative) or not relative.endswith('.json') or relative.endswith('.audit.json'):
            raise ValueError(f'Sealed or unsupported input: {relative}')
        if relative not in allowed and relative not in module._train_allowlist():
            raise ValueError(f'Input lacks frozen public validation/TRAIN provenance: {relative}')
        return relative

    def load_set(name):
        if forbidden(name):
            raise ValueError(f'Sealed list is not allowed: {name}')
        return original_load_set(name)

    module.ensure_safe = module.assert_allowed = ensure_safe
    module.load_set = module.resolve_set = load_set
    module._validation_allowlist = lambda: allowed
    module._engine_public_provenance = True
    return module


def ensure_safe(path):
    return install().ensure_safe(path)
