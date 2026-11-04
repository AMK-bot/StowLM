"""Keep native derived-data cache writes within the adapter's file fence."""
from contextlib import contextmanager
from functools import wraps
import importlib
import os
from pathlib import Path
import threading

HERE = Path(__file__).resolve().parent
ROOT = HERE/'runtime-cache'
_LOCK = threading.RLock()


@contextmanager
def runtime_cache_scope():
    """Change paths only; preserve every native cache key and validation rule."""
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError('pipeline runtime already active; use a separate process')
    restores = []
    old_vessel = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
    try:
        vessel = Path(old_vessel).resolve() if old_vessel else ROOT/'vessels'
        if not vessel.is_relative_to(HERE):
            vessel = ROOT/'vessels'
        os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(vessel)
        for name, attribute, path in (
            ('stowage.checker.stability_tables', 'CACHE_DIR', ROOT/'stability'),
            ('stowage.checker.stability_sounding', 'CACHE_DIRECTORY', ROOT/'stability'),
            ('stowage.checker.stability_liquid', 'CACHE_DIRECTORY', ROOT/'stability'),
            ('stowage.checker.stability_liquid_tables', 'CACHE_DIRECTORY', ROOT/'stability'),
            ('stowage.linear.cache', '_DEFAULT_ROOT', ROOT/'linear'),
        ):
            module = importlib.import_module(name)
            restores.append((module, attribute, getattr(module, attribute)))
            setattr(module, attribute, path)
        tables = importlib.import_module('stowage.checker.stability_tables')
        for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
            function = getattr(tables, name)
            defaults = function.__kwdefaults__
            restores.append((function, '__kwdefaults__', defaults))
            function.__kwdefaults__ = dict(defaults, cache_dir=ROOT/'stability')
        yield
    finally:
        try:
            for obj, name, value in reversed(restores):
                setattr(obj, name, value)
            if old_vessel is None:
                os.environ.pop('STOWAGE_VESSEL_CACHE_DIR', None)
            else:
                os.environ['STOWAGE_VESSEL_CACHE_DIR'] = old_vessel
        finally:
            _LOCK.release()


def within_runtime_cache(function):
    @wraps(function)
    def call(*args, **kwargs):
        with runtime_cache_scope():
            return function(*args, **kwargs)
    return call
