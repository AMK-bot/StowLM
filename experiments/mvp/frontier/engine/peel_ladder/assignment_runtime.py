# Copied from experiments/mvp/frontier/lab/c5/c5-peel/assignment_runtime.py; frozen body unchanged.
"""Imports for the copied assignment lane; never import another team's runtime."""
from pathlib import Path
import importlib.util
import importlib
import sys
from runtime import load_engine

HERE = Path(__file__).resolve().parent
VENDOR = HERE / 'assignment_vendor'

def load_assignment():
    load_engine()
    # The copied study contains a diagnostic script also named tiers.py. Load
    # the policy reader first; construction imports must never execute it.
    import tiers
    if Path(tiers.__file__).resolve() != HERE/'tiers.py':
        raise RuntimeError('Refusing foreign tier-policy module')
    paths = [VENDOR/'c5/c5-securing', VENDOR/'c4/c2-layout',
             VENDOR/'c1/fx-scout', VENDOR/'c1/leader', HERE]
    dependencies = ('gap', 'stow', 'rows', 'pages_sight', 'pages_balanced', 'pages',
                    'pages_base', 'pages_fast_dense', 'e9fix', 'repair', 'chainlab',
                    'ballast_opt', 'physics')
    expected = {name: next((path/(name+'.py') for path in paths
                           if (path/(name+'.py')).exists()), None) for name in dependencies}
    for dependency, path in expected.items():
        if path is None:
            raise RuntimeError(f'Missing copied dependency: {dependency}')
        existing = sys.modules.get(dependency)
        if existing is not None and Path(existing.__file__).resolve() != path:
            raise RuntimeError(f'Refusing foreign assignment dependency: {dependency}: {existing.__file__}')
    sys.path[:0] = [str(p) for p in paths]
    name = 'peel_assignment_ordinary'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, paths[0]/'method.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    for dependency in dependencies:
        importlib.import_module(dependency)
        path = Path(sys.modules[dependency].__file__).resolve()
        if path != expected[dependency]:
            raise RuntimeError(f'Refusing foreign assignment dependency: {dependency}: {path}')
    import alns
    if Path(alns.__file__).resolve() != HERE/'alns.py':
        raise RuntimeError(f'Refusing live native constructor: {alns.__file__}')
    # stow.py installs its private lab paths while importing. Keep public
    # helper names (tiers/method/masks) owned by this experiment afterwards.
    sys.path.insert(0, str(HERE))
    return sys.modules[name]
