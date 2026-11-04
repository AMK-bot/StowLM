"""ExIt requires the necessary securing screen to stay available (D115).

Production intentionally permits uncertain screens to defer to the full checker.
For this experiment an unavailable bound or calculation is a deployment error;
ordinary prefix repair exemptions remain the documented necessary-screen policy.
No model or accelerator imports occur here.
"""
from __future__ import annotations

from numbers import Real


class SecuringUnavailable(RuntimeError):
    """The securing layer cannot supply its declared necessary bounds."""


def _validate(screen):
    metrics = getattr(screen, 'metrics', {})
    faults = {key: value for key, value in metrics.items()
              if key.startswith(('unavailable:', 'fail_open:')) and value}
    reason = getattr(screen, 'unavailable_reason', None)
    if getattr(screen, 'bounds_available', False) is not True or reason or faults:
        raise SecuringUnavailable(f'ExIt securing unavailable/fail-open: {reason or faults or "bounds unavailable"}')
    return metrics


class GuardedSecuringScreen:
    """Transparent screen proxy; a fail-open result never reaches its caller."""

    def __init__(self, screen):
        self.inner = screen
        _validate(screen)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _call(self, name, *args, **kwargs):
        _validate(self.inner)
        result = getattr(self.inner, name)(*args, **kwargs)
        _validate(self.inner)
        return result

    def check_placement(self, *args, **kwargs):
        return self._call('check_placement', *args, **kwargs)

    def check_state(self, *args, **kwargs):
        return self._call('check_state', *args, **kwargs)

    def evaluate(self, *args, **kwargs):
        return self._call('evaluate', *args, **kwargs)


def guarded_factory(factory):
    """Retain production decoder/layer composition and guard its owned screen."""
    def guarded(instance, engine, codec, mask_config, n):
        decoders = factory(instance, engine, codec, mask_config, n)
        shared = {}
        for decoder in decoders:
            outer = decoder.engine
            screen = getattr(outer, 'securing_screen', None)
            if screen is None or getattr(outer, 'screen', None) is not screen:
                raise SecuringUnavailable('ExIt decoder is missing its securing screen')
            key = id(screen)
            if key not in shared:
                shared[key] = screen if isinstance(screen, GuardedSecuringScreen) else GuardedSecuringScreen(screen)
            outer.screen = outer.securing_screen = shared[key]
        securing_metrics(decoders)
        return decoders
    guarded.name = factory.name
    return guarded


def securing_metrics(decoders, *, require_evaluations=False):
    """Aggregate unique shared screens, with strict availability validation.

    Zero evaluations is legitimate for a dead end or hold-only map. The launch
    preflight and smoke require positive evaluations across their complete set.
    Prefix future-repair/single-chain exemptions are recorded separately and do
    not represent missing bounds or failed calculations.
    """
    screens = {}
    for decoder in decoders:
        screen = getattr(decoder.engine, 'securing_screen', None)
        if screen is None:
            raise SecuringUnavailable('ExIt decoder is missing its securing screen')
        screens[id(screen)] = screen
    if not screens:
        raise SecuringUnavailable('ExIt has no securing screens to inspect')
    result = {'screens': len(screens), 'evaluations': 0, 'unavailable': 0, 'fail_open': 0}
    for screen in screens.values():
        metrics = _validate(screen)
        for key, value in metrics.items():
            if isinstance(value, Real) and not isinstance(value, bool):
                result[key] = result.get(key, 0) + value
    result['bounds_available'] = True
    result['prefix_exemptions_policy'] = 'necessary-screen repair/single-chain deferrals; full checker remains mandatory'
    if require_evaluations and result['evaluations'] <= 0:
        raise SecuringUnavailable('ExIt securing preflight observed zero force evaluations')
    return result
