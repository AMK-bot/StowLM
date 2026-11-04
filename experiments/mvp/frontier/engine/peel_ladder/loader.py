"""Scoped absolute-import namespace for the unchanged measured C5 LP copies."""
from contextlib import contextmanager
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
from . import runtime

HERE = Path(__file__).resolve().parent
NAMES = ('runtime', 'tiers', 'finishing', 'masks', 'alns', 'assignment_method',
         'assignment_proposal', 'assignment_masks', 'elastic', 'assignment_runtime',
         'assignment_records', 'gap', 'stow', 'rows', 'pages_sight', 'pages_balanced',
         'pages', 'pages_base', 'pages_fast_dense', 'e9fix', 'repair', 'chainlab',
         'ballast_opt', 'physics', 'peel_assignment_ordinary')


def verify_copies():
    manifest = json.loads((HERE/'copied-manifest.json').read_text())
    for name, expected in manifest.items():
        data = (HERE/name).read_bytes()
        if sha256(data).hexdigest() != expected['copied_sha256']:
            raise RuntimeError(f'frozen peel copy changed: {name}')
        if sha256(data.split(b'\n', 1)[1]).hexdigest() != expected['original_sha256']:
            raise RuntimeError(f'frozen peel body changed: {name}')
    frozen = json.loads((HERE/'assignment-frozen-hashes.json').read_text())
    expected = frozen['checker_and_policy_files']['experiments/mvp/frontier/lab/c5/leader/tiers.toml']
    if sha256((HERE.parent/'tiers.toml').read_bytes()).hexdigest() != expected:
        raise RuntimeError('frozen tier policy changed')
    return {name: row['original_sha256'] for name,row in manifest.items()}


@contextmanager
def isolated():
    """Restore ambient modules, paths and numerical functions even on failure."""
    verify_copies()
    old_modules = {name: sys.modules.get(name) for name in NAMES}
    old_path = list(sys.path)
    # A private donor installer mutates this shared native class. Preserve the
    # caller's exact wrapper/native state before importing any private module.
    import stowage.feasibility._engine as native
    from stowage.checker import rules_special
    e9_owner = next(cls for cls in vars(native).values()
                    if isinstance(cls, type) and '_e9_batch' in vars(cls))
    original_batch = e9_owner._e9_batch
    original_parameter = rules_special._parameter
    import scipy.optimize as optimize
    old_opt = {name: getattr(optimize, name) for name in ('linprog','milp')}
    try:
        for name, original in old_opt.items():
            def one(*args, _original=original, **kwargs):
                kwargs['options'] = {**(kwargs.get('options') or {}), 'threads': 1}
                return _original(*args, **kwargs)
            setattr(optimize, name, one)
        for name in NAMES:
            sys.modules.pop(name, None)
        sys.modules['runtime'] = runtime
        sys.path.insert(0, str(HERE))
        tiers = importlib.import_module('tiers')
        tiers.TABLE_PATH = HERE.parent/'tiers.toml'
        tiers.ROOT = HERE.parents[4]
        masks = importlib.import_module('masks')
        masks.TABLE = tiers.TABLE_PATH
        method = importlib.import_module('assignment_method')
        yield method
    finally:
        # Restore process-local mutations before discarding the private module
        # that owns its closure. Root e9fix.uninstall relies on exact identity.
        e9_owner._e9_batch = original_batch
        rules_special._parameter = original_parameter
        for name in NAMES:
            sys.modules.pop(name, None)
            if old_modules[name] is not None:
                sys.modules[name] = old_modules[name]
        sys.path[:] = old_path
        for name, original in old_opt.items():
            setattr(optimize, name, original)
