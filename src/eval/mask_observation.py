"""Observe the real mask's availability without rebuilding or changing its screen."""
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from threading import RLock


_FACTORY_OBSERVER_LOCK = RLock()


def mask_diagnostic_snapshot(engine, instance_id=None):
    """Copy final diagnostics for observers and planners that publish them directly."""
    diagnostics = getattr(engine, "diagnostics", None)
    if not isinstance(diagnostics, dict):
        return None
    return {**deepcopy(diagnostics), **({"instance_id": instance_id} if instance_id is not None else {})}


@contextmanager
def observe_mask_diagnostics():
    """Capture factory engines' final diagnostics within one evaluation worker.

    Observe IncrementalEngine initialization as well as the public factory: a
    factory alias imported before this context still constructs the same class.
    The temporary observer delegates unchanged and restores both entry points.
    Evaluation
    workers are separate processes; a lock prevents overlapping harness observers
    in a process. Planners that publish diagnostics directly remain supported by
    the metrics reader, including factories imported before this context.
    """
    import stowage.feasibility
    from stowage.feasibility._engine import IncrementalEngine

    diagnostics, engines = [], []
    with _FACTORY_OBSERVER_LOCK:
        factory = stowage.feasibility.make_feasibility_engine
        initialize = IncrementalEngine.__init__

        @wraps(initialize)
        def observe_initialization(engine, *args, **kwargs):
            initialize(engine, *args, **kwargs)
            instance = args[0] if args else kwargs.get('instance')
            engines.append((engine, getattr(instance, 'id', None)))

        def observe(*args, **kwargs):
            engine = factory(*args, **kwargs)
            instance = args[0] if args else kwargs.get('instance')
            engines.append((engine, getattr(instance, 'id', None)))
            return engine

        stowage.feasibility.make_feasibility_engine = observe
        IncrementalEngine.__init__ = observe_initialization
        try:
            yield diagnostics
        finally:
            stowage.feasibility.make_feasibility_engine = factory
            IncrementalEngine.__init__ = initialize
            seen = set()
            for engine, instance_id in engines:
                if id(engine) in seen:
                    continue
                seen.add(id(engine))
                snapshot = mask_diagnostic_snapshot(engine, instance_id)
                if snapshot is not None:
                    diagnostics.append(snapshot)
