"""Relocate numerical cache writes only; never replace checker or physics code."""
from contextlib import contextmanager
import importlib
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


@contextmanager
def native_caches():
    changed = []
    old = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
    root = HERE / 'cache' / 'agency-native'
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(root / 'vessels')
        for module_name, attribute, path in (
            ('stowage.checker.stability_tables', 'CACHE_DIR', root / 'stability'),
            ('stowage.checker.stability_sounding', 'CACHE_DIRECTORY', root / 'stability'),
            ('stowage.checker.stability_liquid', 'CACHE_DIRECTORY', root / 'stability'),
            ('stowage.checker.stability_liquid_tables', 'CACHE_DIRECTORY', root / 'stability'),
            ('stowage.linear.cache', '_DEFAULT_ROOT', root / 'linear'),
            ('experiments.mvp.pipeline.binding.runtime', 'HERE', HERE),
            ('experiments.mvp.pipeline.binding.runtime', 'ROOT', root),
            ('experiments.mvp.pipeline.lattice.gates', 'HERE', root / 'lattice'),
            ('experiments.mvp.pipeline.lattice.runtime', 'HERE', root / 'lattice'),
        ):
            module = importlib.import_module(module_name)
            changed.append((module, attribute, getattr(module, attribute)))
            setattr(module, attribute, path)
        tables = importlib.import_module('stowage.checker.stability_tables')
        for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
            function = getattr(tables, name)
            changed.append((function, '__kwdefaults__', function.__kwdefaults__))
            function.__kwdefaults__ = dict(function.__kwdefaults__ or {}, cache_dir=root / 'stability')
        yield
    finally:
        for obj, name, value in reversed(changed):
            setattr(obj, name, value)
        if old is None:
            os.environ.pop('STOWAGE_VESSEL_CACHE_DIR', None)
        else:
            os.environ['STOWAGE_VESSEL_CACHE_DIR'] = old
