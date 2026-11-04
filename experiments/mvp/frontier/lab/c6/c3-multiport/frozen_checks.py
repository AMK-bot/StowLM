"""Observe copied solver check calls while preserving the native checker.

The observer receives the original instance, concrete plan and native report.
Observer errors propagate. Native modules and their checker bindings are never
patched, and the native report object is returned without modification.
"""
import frozen_env
from stowage.checker import check as _native_check

_observer = None


def set_observer(callback):
    global _observer
    if callback is not None and not callable(callback):
        raise TypeError('observer must be callable or None')
    _observer = callback


def check(instance, plan, *args, **kwargs):
    report = _native_check(instance, plan, *args, **kwargs)
    observer = _observer
    if observer is not None:
        observer(instance, plan, report)
    return report
