"""Exact process-local memoization of original C1-engine geometry helpers.

Preparation only: first call runs the native helper unchanged, repeated complete
input signatures reuse its exact result. No capacity rule or checker is patched.
"""
from collections import OrderedDict
from functools import wraps
import hashlib
import inspect
from pathlib import Path
import threading
from time import perf_counter

import numpy as np

# Filled from the pinned original source, not an installed/downloading solver.
EXPECTED_SOURCE = {'_nearest_cells': 'dfd45b0d4d2bd46a09457272f52fb8247012f0d298eae1ae91dcead1384af0e8', '_oog_zones': '807eed628ecb2ef5b503dfe5117689a719f9687f103f4b56242e248ccb4d988b'}


def _array(value):
    a=np.asarray(value)
    return (a.dtype.str,a.shape,a.tobytes(order='C'))


def _number(value):
    # Preserve dtype and every numeric bit, including signed zero/NaN payloads.
    return _array(value)


def geometry_key(engine):
    """Full ordered native geometry inputs; no vessel-ID-only shortcut.

    Cell ordering, IDs, all coordinate bits, and both complete footprint/index
    arrays participate. Stack records add a conservative full physical-layout
    signature even though the native donor helper needs only coordinates.
    """
    T=engine.T
    cells=tuple((c.id,c.stack_id,_number(c.lcg_m),_number(c.tcg_m),_number(c.vcg_base_m))
                for c in engine.instance.vessel.cells)
    stacks=tuple((s.id,s.bay,s.row,s.on_deck,tuple(s.cell_ids),_number(s.base_z_m),_number(s.max_height_m))
                 for s in engine.instance.vessel.stacks)
    return (tuple(T.cell_ids),tuple(T.slot_ids),tuple(T.stack_ids),
            _array(T.slot_cells),_array(T.slot_stacks),cells,stacks)


def radius_key(engine):
    """Use the native parameter lookup and identical radius arithmetic."""
    from stowage.checker.rules_special import _nonnegative, _parameter
    clearance=_nonnegative(_parameter(engine.instance,'expert.e9.over_width_length',
                                      'oog_clearance','minimum_clearance_m')) or 0.0
    over=max((max(b.oog.over_width_port_m,b.oog.over_width_starboard_m,
                  b.oog.over_length_fore_m,b.oog.over_length_aft_m)
              for b in engine.instance.containers if b.oog),default=0.0)
    radius=clearance+2*over+13.716+2.438
    return (_number(clearance),_number(over),_number(radius))


class GeometryCache:
    def __init__(self,max_entries=8):
        if not isinstance(max_entries,int) or max_entries<1:
            raise ValueError('max_entries must be a positive integer')
        self.max_entries=max_entries
        self.entries=OrderedDict()
        self.lock=threading.RLock()
        self.stats=dict(nearest_hits=0,nearest_misses=0,oog_hits=0,oog_misses=0,
                        nearest_native_s=0.,oog_native_s=0.)

    def _get(self,key):
        try:value=self.entries.pop(key)
        except KeyError:return None
        self.entries[key]=value
        return value

    def _put(self,key,value):
        self.entries[key]=value
        while len(self.entries)>self.max_entries:self.entries.popitem(last=False)

    def nearest(self,engine,k,native):
        key=('nearest',geometry_key(engine),(type(k).__module__,type(k).__qualname__,repr(k)))
        with self.lock:
            value=self._get(key)
            if value is not None:
                self.stats['nearest_hits']+=1
                return value.copy()
            self.stats['nearest_misses']+=1
            started=perf_counter();out=native(engine,k)
            self.stats['nearest_native_s']+=perf_counter()-started
            saved=out.copy();saved.flags.writeable=False
            self._put(key,saved)
            return out

    def oog(self,engine,native):
        key=('oog',geometry_key(engine),_array(engine._slot_xy),radius_key(engine))
        with self.lock:
            value=self._get(key)
            if value is not None:
                self.stats['oog_hits']+=1
                return list(value)
            self.stats['oog_misses']+=1
            started=perf_counter();out=native(engine)
            self.stats['oog_native_s']+=perf_counter()-started
            # Native result is list[tuple[int,...]]; tuples are immutable. Store
            # independent immutable containers and return a fresh outer list.
            self._put(key,tuple(tuple(zone) for zone in out))
            return out


_CACHE=GeometryCache()


def install():
    from stowage.feasibility import _engine
    owner=_engine.IncrementalEngine
    if getattr(owner._nearest_cells,'_c6_prep_geometry_cache',False):
        return
    originals={name:getattr(owner,name) for name in ('_nearest_cells','_oog_zones')}
    for name,native in originals.items():
        # inspect.getsource unwraps functools.wraps, which could otherwise hide
        # a previously installed, non-native helper behind the native source.
        if hasattr(native,'__wrapped__') or Path(native.__code__.co_filename).resolve()!=Path(_engine.__file__).resolve():
            raise RuntimeError(f'Geometry helper already wrapped/replaced: {name}; refuse layered cache')
        digest=hashlib.sha256(inspect.getsource(native).encode()).hexdigest()
        if digest!=EXPECTED_SOURCE[name]:
            raise RuntimeError(f'Unexpected original geometry helper source: {name}; refuse unverified cache')
    @wraps(originals['_nearest_cells'])
    def nearest(self,k):return _CACHE.nearest(self,k,originals['_nearest_cells'])
    @wraps(originals['_oog_zones'])
    def oog(self):return _CACHE.oog(self,originals['_oog_zones'])
    nearest._c6_prep_geometry_cache=True
    oog._c6_prep_geometry_cache=True
    nearest._c6_original=originals['_nearest_cells']
    oog._c6_original=originals['_oog_zones']
    owner._nearest_cells,owner._oog_zones=nearest,oog


def statistics():
    with _CACHE.lock:return dict(_CACHE.stats,entries=len(_CACHE.entries),max_entries=_CACHE.max_entries)
