"""Load the start-of-task engine snapshot under its required absolute package name."""
from pathlib import Path
import importlib.util
import sys
import os

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]

def load_engine():
    sys.dont_write_bytecode = True
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        os.environ[key] = '1'
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'runtime-cache'/str(os.getpid()))
    # Cap every later imported SciPy HiGHS call, including snapshot lanes
    # whose original alias omits the explicit thread option.
    import scipy.optimize as optimize
    for name in ('linprog','milp'):
        original=getattr(optimize,name)
        if not getattr(original,'_peel_one_thread',False):
            def one(*args,_original=original,**kwargs):
                kwargs['options']={**(kwargs.get('options') or {}),'threads':1}
                return _original(*args,**kwargs)
            one._peel_one_thread=True
            setattr(optimize,name,one)
    # Native Frontier modules were copied as well: frozen.py resolves ../planner.py.
    sys.path.insert(0, str(HERE))
    existing = sys.modules.get('engine')
    package = HERE/'engine_snapshot'
    if existing is not None:
        if Path(existing.__file__).resolve() != package/'__init__.py':
            raise RuntimeError('Refusing live or foreign engine already imported')
        return existing
    spec = importlib.util.spec_from_file_location('engine', package/'__init__.py',
                                                  submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['engine'] = module
    spec.loader.exec_module(module)
    return module
