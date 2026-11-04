"""Exact E8-only singleton search probe on empty valid non-frozen arrivals.

Never alters checker code. Replaces repeated whole-ship special_violations
calls only where its E8 envelope and distance are directly reproducible.
"""
from functools import wraps
import math
from time import perf_counter
import numpy as np


def reject(envelope, heats, clearance):
    for heat in heats:
        distance=math.sqrt(sum(max(a[0]-b[1],b[0]-a[1],0)**2 for a,b in zip(envelope,heat)))
        overlap=all(a[0]<b[1]-1e-9 and b[0]<a[1]-1e-9 for a,b in zip(envelope,heat))
        if overlap or distance+1e-9<clearance:
            return True
    return False


def fast_static(model):
    """Return the exact previous E8-filtered static mask, or None for fallback."""
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.checker.rules import _geometry_index
    from stowage.checker.rules_special import _clearance_parameter, _nonnegative
    from stowage.conventions import cells_of_slot, CONTAINER_LENGTH_M, CONTAINER_WIDTH_M
    p,T=model.p,model.T
    if len(p.instance.load_list)<=500 or p.instance.onboard.placements or rule_freeze_v1_enabled():
        return None
    cells,stacks,usable,problems=_geometry_index(p.instance)
    if problems:
        return None
    heat_boxes=[b for b in model.boxes if T.heat[b]]
    if any(p.boxes[T.box_ids[b]].oog is not None for b in heat_boxes):
        return None
    out=T.static.copy()
    clearance=_nonnegative(_clearance_parameter(p.instance,'expert.e8.away_from_heat','minimum_clearance','heat_clearance_m'))
    # Native missing-data violations are ignored by the inherited filter, which
    # retains only rule IDs containing away_from_heat.
    if clearance is None:
        return out
    heats=[((h.x_min_m,h.x_max_m),(h.y_min_m,h.y_max_m),(h.z_min_m,h.z_max_m)) for h in p.instance.vessel.heat_sources]
    locations=[]
    for slot in T.slot_ids:
        ids=cells_of_slot(slot)
        if any(cid not in usable for cid in ids):
            return None
        physical=[cells[cid] for cid in ids]
        locations.append((sum(c.lcg_m for c in physical)/len(physical),
                          sum(c.tcg_m for c in physical)/len(physical),
                          max(stacks[c.stack_id].base_z_m for c in physical)))
    by_geometry={}
    for b in heat_boxes:
        box=p.boxes[T.box_ids[b]]
        key=(box.length_ft,box.height_m)
        if key not in by_geometry:
            half=CONTAINER_LENGTH_M[box.length_ft]/2
            width=CONTAINER_WIDTH_M/2
            cache={}
            mask=[]
            for x,y,z in locations:
                pos=(x,y,z)
                if pos not in cache:
                    cache[pos]=reject(((x-half,x+half),(y-width,y+width),(z,z+box.height_m)),heats,clearance)
                mask.append(cache[pos])
            by_geometry[key]=np.asarray(mask,dtype=bool)
        out[b,by_geometry[key]]=False
        if not out[b].any():
            out[b]=T.static[b]
    return out


def install():
    import pages_base
    original=pages_base.Model._static
    if getattr(original,'_xl_exact_e8',False):
        return
    @wraps(original)
    def static(self):
        cached=getattr(self.p,'_cgscale_static',None)
        if cached is not None:
            return cached
        started=perf_counter()
        out=fast_static(self)
        if out is None:
            return original(self)
        self.p._cgscale_static=out
        self.p._xl_e8_static=dict(seconds=perf_counter()-started,exact_fast_lane=True)
        return out
    static._xl_exact_e8=True
    static._xl_original=original
    pages_base.Model._static=static
