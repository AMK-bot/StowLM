"""D199 exact numerical reuse inside the unchanged D192 V4 part probes.

No checker entry point or acceptance decision is replaced. Only completed
numerical helpers inside Evaluator._part share exact results for one candidate.
The full checker runs with the cache inactive, including V4's acceptance call.
The original owned-process watchdog remains in ballast_v4_dispatch.finish.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import time

_ACTIVE = ContextVar("scale_finisher_part_cache", default=None)


class _CandidateCache:
    """Disposable workspace; full content keys detect even in-place mutation."""

    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        self.values = {}

    def get(self, name, key, compute, limit=64):
        entries = self.values.setdefault(name, OrderedDict())
        counters = self.diagnostics.setdefault('scale_exact_reuse', {})
        prefix = name + '_'
        if key in entries:
            entries.move_to_end(key)
            counters[prefix+'hits'] = counters.get(prefix+'hits', 0)+1
            result, elapsed = entries[key]
            counters[prefix+'avoided_s'] = counters.get(prefix+'avoided_s', 0.)+elapsed
            return result
        started = time.monotonic()
        result = compute()  # exceptions and partial calculations are never cached
        elapsed = time.monotonic()-started
        entries[key] = result, elapsed
        if len(entries) > limit:
            entries.popitem(last=False)
        counters[prefix+'misses'] = counters.get(prefix+'misses', 0)+1
        counters[prefix+'computed_s'] = counters.get(prefix+'computed_s', 0.)+elapsed
        return result


def _state_key(instance, ship, *, cargo_only=False):
    # Pydantic's round-trip JSON preserves all actual inputs, including policy,
    # geometry, fixed/background cargo, OOG, covers and exact binary floats.
    # Geometry alone cannot depend on tank masses; loading always includes them.
    return (instance.model_dump_json(round_trip=True),
            ship.model_dump_json(round_trip=True,
                                 exclude={'tank_masses_kg'} if cargo_only else None))


@contextmanager
def exact_part_reuse():
    """Install worker-local wrappers, active only in public part probe calls.

    The last three candidate workspaces are retained to cover selected-margin
    revisits without an unbounded optimization-history cache. No cache survives
    this worker request. Complete full-checker calls use the original helpers.
    """
    from stowage.checker import visibility
    from stowage.solver.ballast_v4_evaluator import Evaluator
    from stowage.vessel.tanks import TankGeometry

    original_part = Evaluator._part
    original_cargo = visibility._all_cargo_geometry
    original_loading = visibility._loading_condition
    original_from_descriptor = TankGeometry.__dict__['from_params']
    original_from = TankGeometry.from_params
    workspaces = OrderedDict()

    @wraps(original_cargo)
    def cargo(instance, ship):
        cache = _ACTIVE.get()
        if cache is None:
            return original_cargo(instance, ship)
        return cache.get('cargo_geometry', _state_key(instance, ship, cargo_only=True),
                         lambda: original_cargo(instance, ship), limit=8)

    @wraps(original_loading)
    def loading(instance, ship):
        cache = _ACTIVE.get()
        if cache is None:
            return original_loading(instance, ship)
        return cache.get('loading_condition', _state_key(instance, ship),
                         lambda: original_loading(instance, ship), limit=8)

    @classmethod
    def from_params(cls, tank_params, *, ship_params=None):
        cache = _ACTIVE.get()
        if cache is None or cls is not TankGeometry:
            return original_from.__func__(cls, tank_params, ship_params=ship_params)
        # Complete supplied parameter content binds hull intersections as well
        # as embedded sections. No mtime, vessel-name or rounded-value identity.
        key = json.dumps([tank_params, ship_params], sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
        return cache.get('tank_geometry', key,
                         lambda: original_from(tank_params, ship_params=ship_params))

    @wraps(original_part)
    def part(evaluator, identity, phase, instance, ship, name):
        workspace_key = id(evaluator), identity
        cache = workspaces.get(workspace_key)
        if cache is None:
            cache = _CandidateCache(evaluator.diagnostics)
            workspaces[workspace_key] = cache
            if len(workspaces) > 3:
                workspaces.popitem(last=False)
        workspaces.move_to_end(workspace_key)
        token = _ACTIVE.set(cache)
        try:
            return original_part(evaluator, identity, phase, instance, ship, name)
        finally:
            _ACTIVE.reset(token)

    visibility._all_cargo_geometry = cargo
    visibility._loading_condition = loading
    TankGeometry.from_params = from_params
    Evaluator._part = part
    try:
        yield
    finally:
        Evaluator._part = original_part
        TankGeometry.from_params = original_from_descriptor
        visibility._loading_condition = original_loading
        visibility._all_cargo_geometry = original_cargo
        workspaces.clear()


def worker(instance, plan, *, time_limit_s, seed, deadline):
    """Pickleable spawn entry point retaining the existing hard watchdog."""
    from stowage.solver.ballast_v4 import finish
    with exact_part_reuse():
        return finish(instance, plan, time_limit_s=time_limit_s, seed=seed,
                      _deadline=deadline)


def install():
    """Call after frozen-runtime loading; modifies only its worker dispatch."""
    from stowage.solver import ballast_v4_dispatch
    ballast_v4_dispatch._worker = worker
