"""V3: exact liquid-root and loading reuse inside V2 search part probes only.

V2 callback order, native loading screens, watchdog and complete acceptance
checker are preserved. This caches completed `_direct_liquid` values, including
upright roots repeated by transfer envelopes at different heel angles. No
interpolation, reflection, rounding, altered solver, or tolerance is introduced.
A request-local loading-condition bank also survives inner workspace eviction.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from copy import copy
from functools import wraps
import hashlib
import struct
import time

# Separate upright roots survive the changing tilted-angle working set.
ENTRIES_PER_BANK = 4096
LOADING_LIMIT = 128


def _geometry_identity(solid):
    """Full exact solid content; preserve contour order and signed float zeros.

    Unsupported/custom/mutable provider classes bypass the cache. The two exact
    accepted classes freeze their geometry and own read-only section arrays.
    No canonical contour rotation or reflection can alter round-off identity.
    """
    import numpy as np
    from stowage.vessel.hull_tanks import SectionTankGeometry
    from stowage.vessel.tanks import TankGeometry
    if type(solid) not in (TankGeometry, SectionTankGeometry):
        return None
    digest = hashlib.sha256()
    digest.update(type(solid).__module__.encode()+b'/'+type(solid).__qualname__.encode())
    for axis in 'xyz':
        for bound in ('min', 'max'):
            digest.update(struct.pack('!d', float(getattr(solid, f'{axis}_{bound}_m'))))
    digest.update(struct.pack('!d', float(solid.capacity_m3)))
    if type(solid) is SectionTankGeometry:
        digest.update(bytes([bool(solid._prismatic)]))
        for value in solid._full_centroid:
            digest.update(struct.pack('!d', float(value)))
        arrays = [solid.x_m, solid.integration_weights_m]
        digest.update(struct.pack('!Q', len(solid.sections_yz_m)))
        for section in solid.sections_yz_m:
            digest.update(struct.pack('!Q', len(section)))
            arrays.extend(section)
        for array in arrays:
            value = np.asarray(array)
            if value.flags.writeable:
                return None
            digest.update(value.dtype.str.encode())
            digest.update(struct.pack('!Q', value.ndim))
            for size in value.shape:
                digest.update(struct.pack('!Q', size))
            digest.update(value.tobytes(order='C'))
    return digest.digest()


class _Workspace:
    def __init__(self):
        self.identities = OrderedDict()
        self.upright = OrderedDict()
        self.tilted = OrderedDict()

    def identity(self, solid):
        key = id(solid)
        if key not in self.identities:
            identity = _geometry_identity(solid)
            self.identities[key] = (solid, identity)  # Retain against ID reuse.
            if len(self.identities) > 128:
                self.identities.popitem(last=False)
        self.identities.move_to_end(key)
        return self.identities[key][1]


@contextmanager
def exact_liquid_reuse():
    """Worker/request-local, bounded cache; inactive during complete checking."""
    import numpy as np
    from finisher import _ACTIVE
    from stowage.checker import stability_tank_transfer as module
    original = module._direct_liquid
    # Every key binds the full immutable physical solid and every scalar input.
    # Ballast-candidate identity is not an input to this pure geometry kernel;
    # in particular upright envelope roots recur across finite-difference fills.
    workspace = _Workspace()

    @wraps(original)
    def direct(solid, volume_m3, heel_rad, trim_rad, *, polynomial=True):
        active = _ACTIVE.get()
        if active is None:
            return original(solid, volume_m3, heel_rad, trim_rad, polynomial=polynomial)
        geometry = workspace.identity(solid)
        if geometry is None:
            return original(solid, volume_m3, heel_rad, trim_rad, polynomial=polynomial)
        values = float(volume_m3), float(heel_rad), float(trim_rad)
        # Include dispatch mode as well as exact physical values. Nonfinite
        # queries follow the original validating path, without cache admission.
        if not np.isfinite(values).all():
            return original(solid, volume_m3, heel_rad, trim_rad, polynomial=polynomial)
        key = geometry, struct.pack('!ddd', *values), bool(polynomial)
        upright = values[1] == values[2] == 0.
        bank = workspace.upright if upright else workspace.tilted
        prefix = 'liquid_upright_' if upright else 'liquid_tilted_'
        counters = active.diagnostics.setdefault('scale_exact_reuse', {})
        if key in bank:
            bank.move_to_end(key)
            centre, level, elapsed = bank[key]
            counters[prefix+'hits'] = counters.get(prefix+'hits', 0)+1
            counters[prefix+'avoided_s'] = counters.get(prefix+'avoided_s', 0.)+elapsed
            return centre.copy(), level
        started = time.monotonic()
        centre, level = original(solid, volume_m3, heel_rad, trim_rad, polynomial=polynomial)
        elapsed = time.monotonic()-started
        centre = np.array(centre, copy=True)
        # Original exceptions/partial or nonfinite results are never cached.
        if not np.isfinite(centre).all() or not np.isfinite(level):
            return centre, level
        stored = centre.copy()
        stored.flags.writeable = False
        bank[key] = stored, level, elapsed
        if len(bank) > ENTRIES_PER_BANK:
            bank.popitem(last=False)
        counters[prefix+'misses'] = counters.get(prefix+'misses', 0)+1
        counters[prefix+'computed_s'] = counters.get(prefix+'computed_s', 0.)+elapsed
        return centre, level

    module._direct_liquid = direct
    try:
        yield
    finally:
        module._direct_liquid = original
        workspace.identities.clear()
        workspace.upright.clear()
        workspace.tilted.clear()


@contextmanager
def exact_loading_reuse():
    """Keep completed visibility/lashing equilibria across local workspaces.

    Finite-difference probes can evict a centre recipe from the inner
    three-candidate cache before its lashing callback. This outer content cache
    spans the one worker request, while the ACTIVE guard still excludes every
    complete-checker call. Keys contain the full actual instance and ship JSON.
    """
    from finisher import _ACTIVE, _state_key
    from stowage.checker import visibility
    original = visibility._loading_condition
    completed = OrderedDict()

    @wraps(original)
    def loading(instance, ship):
        active = _ACTIVE.get()
        if active is None:
            return original(instance, ship)
        key = _state_key(instance, ship)
        counters = active.diagnostics.setdefault('scale_exact_reuse', {})
        prefix = 'visibility_loading_worker_'
        if key in completed:
            completed.move_to_end(key)
            result, elapsed = completed[key]
            counters[prefix+'hits'] = counters.get(prefix+'hits', 0)+1
            counters[prefix+'avoided_s'] = counters.get(prefix+'avoided_s', 0.)+elapsed
            return copy(result)
        started = time.monotonic()
        result = original(instance, ship)  # No errors/partial work are retained.
        elapsed = time.monotonic()-started
        # LoadingCondition is frozen; shallow copies isolate its scalar fields.
        # Its shared HullGeometry is the same trusted cached geometry returned
        # by the original helper; consumers already share/read that provider.
        completed[key] = copy(result), elapsed
        if len(completed) > LOADING_LIMIT:
            completed.popitem(last=False)
        counters[prefix+'misses'] = counters.get(prefix+'misses', 0)+1
        counters[prefix+'computed_s'] = counters.get(prefix+'computed_s', 0.)+elapsed
        return copy(result)

    visibility._loading_condition = loading
    try:
        yield
    finally:
        visibility._loading_condition = original
        completed.clear()


def worker(instance, plan, *, time_limit_s, seed, deadline):
    """Compose V2 under the original owned worker/watchdog dispatch."""
    from finisher_v2 import worker as v2_worker
    # The outer wrapper must be installed before V2's inner exact_part_reuse
    # captures visibility._loading_condition as its unchanged miss provider.
    with exact_loading_reuse():
        with exact_liquid_reuse():
            return v2_worker(instance, plan, time_limit_s=time_limit_s, seed=seed,
                             deadline=deadline)


def install():
    """Call after frozen runtime load; return an idempotent restore hook."""
    from stowage.solver import ballast_v4_dispatch
    original = ballast_v4_dispatch._worker
    ballast_v4_dispatch._worker = worker

    def restore():
        if ballast_v4_dispatch._worker is worker:
            ballast_v4_dispatch._worker = original
    return restore
