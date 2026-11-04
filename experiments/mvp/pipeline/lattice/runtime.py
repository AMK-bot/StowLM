"""Scoped binding execution with all generated artifacts inside lattice/."""
from contextlib import contextmanager
from pathlib import Path
import os
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


@contextmanager
def discarded_plot_scope():
    """Skip only a plot that stage_physics discards while a hatch is off.

    Native stability and strength parts are untouched. compute_frame replaces
    both gz and gz_error on in-port hatch-off frames, so the returned frame is
    identical. Closed-hull plots retain the original implementation.
    """
    from app import stability
    original = stability._gz_curve
    policy = dict(open_hatch_curves_skipped=0,
                  policy='Withheld hatch-off plot skipped; native stability and strength parts unchanged.')

    def curve(instance, ship, part):
        if getattr(ship, 'covers_off', ()):
            policy['open_hatch_curves_skipped'] += 1
            return []
        return original(instance, ship, part)

    stability._gz_curve = curve
    try:
        yield policy
    finally:
        stability._gz_curve = original


def worker_entry(target, args, kwargs, instance, constrained, audit_path):
    """Importable replacement trampoline applies isolation after spawn."""
    from experiments.mvp.pipeline.binding import hooks
    with isolated_binding():
        if hooks.binding_active():
            hooks._event(audit_path, 'worker_hook_active', inherited=True)
            return target(*args, **kwargs)
        with hooks.binding_scope(instance, constrained, _audit_path=audit_path, _worker=True):
            return target(*args, **kwargs)


@contextmanager
def isolated_binding():
    """Patch references, never binding sources or the shared tempfile module."""
    from experiments.mvp.pipeline.binding import hooks, runtime
    root = HERE / 'runtime-cache'
    root.mkdir(parents=True, exist_ok=True)
    old = runtime.HERE, runtime.ROOT, hooks.tempfile, hooks._worker_entry
    old_vessel = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
    original_mkstemp = hooks.tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        kwargs['dir'] = root
        return original_mkstemp(*args, **kwargs)

    try:
        runtime.HERE, runtime.ROOT = HERE, root
        hooks.tempfile = SimpleNamespace(mkstemp=mkstemp)
        hooks._worker_entry = worker_entry
        os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(root / 'vessels')
        yield
    finally:
        runtime.HERE, runtime.ROOT, hooks.tempfile, hooks._worker_entry = old
        if old_vessel is None:
            os.environ.pop('STOWAGE_VESSEL_CACHE_DIR', None)
        else:
            os.environ['STOWAGE_VESSEL_CACHE_DIR'] = old_vessel
