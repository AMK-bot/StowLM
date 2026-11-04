"""Disable only measurement's periodic traceback watchdog instrumentation.

Two recorded workers wrote immutable final rows and then failed to return past
watchdog cancellation after incomplete native-stack dumps. A private view avoids
that instrumentation on future calls; it cannot release an already-blocked C
call. The process-wide faulthandler module and planning core are unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from types import ModuleType

_COUNTS = {"dump_traceback_later": 0, "cancel_dump_traceback_later": 0}


def _no_periodic_dump(*args, **kwargs):
    _COUNTS["dump_traceback_later"] += 1
    return None


def _no_periodic_cancel(*args, **kwargs):
    _COUNTS["cancel_dump_traceback_later"] += 1
    return None


def private_view(original):
    if getattr(original, "_review3_measurement_watchdog_disabled", False):
        return original
    view = ModuleType("binding.private_measurement_faulthandler")
    view.__dict__.update(vars(original))
    view.dump_traceback_later = _no_periodic_dump
    view.cancel_dump_traceback_later = _no_periodic_cancel
    view._review3_measurement_watchdog_disabled = True
    view._original_faulthandler = original
    return view


def install(*, modules=None):
    """Swap measure globals before trace setup; never signal any process."""
    if modules is None:
        from .. import measure
        target = Path(measure.__file__).resolve()
        modules = [module for module in list(sys.modules.values())
                   if getattr(module, "__file__", None)
                   and Path(module.__file__).resolve() == target]
    changed = []
    for module in modules:
        original = getattr(module, "faulthandler", None)
        if original is None:
            continue
        module.faulthandler = private_view(original)
        changed.append(module.__name__)
    return dict(pid=os.getpid(), modules=sorted(set(changed)), instrumentation_only=True,
                planning_core_changed=False, process_wide_faulthandler_changed=False,
                periodic_measurement_stack_dumps=False, cannot_rescue_inflight_cancel=True,
                disabled_calls=dict(_COUNTS))

