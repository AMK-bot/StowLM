"""Use the copied engine finishing lanes; keep the independent runner check."""
from frozen_engine.finishing import finish_chain
from frozen_engine.frozen import planner


def finish(instance, plan, *, seed, finalize_s, label, context):
    try:
        return finish_chain(instance, plan, seed=seed, finalize_s=finalize_s, label=label)
    except Exception as error:
        result, metadata = planner._finish(instance, plan, seed=seed,
                                           finalize_s=finalize_s, label=label)
        metadata['acceleration_error'] = f'{type(error).__name__}: {error}'
        return result, metadata
