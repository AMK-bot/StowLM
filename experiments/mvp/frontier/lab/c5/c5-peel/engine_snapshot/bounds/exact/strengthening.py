# Copied from experiments/mvp/frontier/lab/c3/c2-hatch/strengthening.py; engine integration changes are local.
"""Necessary final-state unary restrictions with outward geometric domains."""
from __future__ import annotations
import math
from stowage.checker.rules_special import _clearance_parameter, _nonnegative
from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M


def _distance(envelope, heat):
    return math.sqrt(sum(max(a[0]-b[1], b[0]-a[1], 0.)**2
                         for a, b in zip(envelope, heat)))


def safe_unary_mask(instance, tables):
    """Remove only placements violating E8 at every possible supported floor.

    No future or current order policy is assumed. Prefix support uses exactly
    k boxes per physical column below tier k. Global min/max box heights yield
    an optimistic interval, expanded by numerical tolerance. Convex distance
    to a rectangular heat source attains its maximum over that interval at an
    endpoint. If even that maximum violates clearance, every actual floor does.
    """
    T = tables
    mask = T.static.copy()
    cells = {c.id: c for c in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    # The production legacy DG unary path may assess an unsupported upper
    # slot on an empty ship. Rebuild DG rows optimistically, without retaining
    # any potentially geometry-dependent false negatives from that path.
    dg_relaxed = 0
    from stowage.checker.rules_imdg import _stowage, _doc_areas
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    freeze = rule_freeze_v1_enabled()
    stacks = {stack.id:stack for stack in instance.vessel.stacks}
    areas = _doc_areas(instance)
    for bi,bid in enumerate(T.box_ids):
        box = boxes[bid]
        if box.dg is None:
            continue
        dg_relaxed += 1
        for si in range(len(T.slot_ids)):
            physical = [cells[T.cell_ids[int(ci)]] for ci in T.slot_cells[si] if ci >= 0]
            ok = (box.length_ft == 20) == (len(physical) == 1)
            ok = ok and all(getattr(cell,f'accepts_{box.length_ft}') for cell in physical)
            ok = ok and (not box.reefer or any(cell.reefer_plug for cell in physical))
            ok = ok and not ('under_deck' in box.special_stowage and T.slot_deck[si])
            ok = ok and not ('on_deck' in box.special_stowage and not T.slot_deck[si])
            ok = ok and bool(T.valid_mass[bi])
            ok = ok and any(call.index>instance.call_index and call.locode==box.pod
                            for call in instance.rotation.calls)
            if T.slot_deck[si]:
                ordinal = max(int(k) for k in T.slot_k[si] if k>=0)+1
                ok = ok and ordinal<=instance.port.quay_crane.max_on_deck_tiers
                ok = ok and T.crane_limit is not None and ordinal<=T.crane_limit
                if not freeze:
                    ok = ok and T.e6_threshold is not None and T.e6_fallback is not None
                    if T.e6_threshold is not None and T.e6_fallback is False and box.gross_mass_kg>=T.e6_threshold:
                        ok = False
            ok = ok and all(T.height[bi] <= T.stack_hmax[int(sj)]+1e-12
                            for sj in T.slot_stacks[si] if sj >= 0)
            if ok:
                # Production's explicitly necessary no-ship screen defers
                # unknown upper-tier SW1/SW2 geometry rather than rejecting it.
                ok = not any(v.severity == 'hard' for v in _stowage(
                    instance,box,T.slot_ids[si],instance.call_index,cells,stacks,areas))
            mask[bi,si] = ok
    heights = [boxes[bid].height_m for bid in T.box_ids]
    hmin, hmax = min(heights, default=0.), max(heights, default=0.)
    removed = 0
    eps = 1e-7
    for bi, bid in enumerate(T.box_ids):
        box = boxes[bid]
        if 'away_from_heat' not in box.special_stowage:
            continue
        clearance = _nonnegative(_clearance_parameter(instance,
            'expert.e8.away_from_heat', 'minimum_clearance', 'heat_clearance_m'))
        if clearance is None:
            # Missing/invalid evidence makes every final placement fail closed.
            mask[bi, :] = False
            continue
        for si, slot in enumerate(T.slot_ids):
            if not mask[bi, si]:
                continue
            supports = [(int(sj), int(k)) for sj, k in zip(T.slot_stacks[si], T.slot_k[si]) if sj >= 0]
            lo = max(float(T.stack_base_z[sj]) + k*hmin for sj, k in supports) - eps
            hi = max(float(T.stack_base_z[sj]) + k*hmax for sj, k in supports) + eps
            # Securing requires equal actual floors for long-box support;
            # each individual column's finite envelope remains necessary.
            for sj, _ in supports:
                cap = float(T.stack_base_z[sj]) + float(T.stack_hmax[sj]) - box.height_m
                hi = min(hi, cap + eps)
            if lo > hi:
                mask[bi, si] = False
                removed += 1
                continue
            physical = [cells[T.cell_ids[int(ci)]] for ci in T.slot_cells[si] if ci >= 0]
            x = sum(c.lcg_m for c in physical)/len(physical)
            y = sum(c.tcg_m for c in physical)/len(physical)
            oog = box.oog
            horizontal = ((x-CONTAINER_LENGTH_M[box.length_ft]/2-(oog.over_length_aft_m if oog else 0.),
                           x+CONTAINER_LENGTH_M[box.length_ft]/2+(oog.over_length_fore_m if oog else 0.)),
                          (y-CONTAINER_WIDTH_M/2-(oog.over_width_starboard_m if oog else 0.),
                           y+CONTAINER_WIDTH_M/2+(oog.over_width_port_m if oog else 0.)))
            height = box.height_m+(oog.over_height_m if oog else 0.)
            for source in instance.vessel.heat_sources:
                heat = ((source.x_min_m, source.x_max_m),
                        (source.y_min_m, source.y_max_m),
                        (source.z_min_m, source.z_max_m))
                farthest = max(_distance((*horizontal, (z, z+height)), heat) for z in (lo, hi))
                overlap_xy = all(a[0] < b[1]-1e-9 and b[0] < a[1]-1e-9
                                 for a,b in zip(horizontal, heat))
                overlap_all = overlap_xy and hi < heat[2][1]-1e-9 and lo+height > heat[2][0]+1e-9
                if overlap_all or farthest+1e-9 < clearance:
                    mask[bi, si] = False
                    removed += 1
                    break
    return mask, {'heat_unary_removed': removed, 'dg_rows_rebuilt_optimistically': dg_relaxed}


def add_e6_bottom_oog(context):
    """Ordinary-heavy E6 witnesses with conservative possible OOG blockers.

    Empty bottom cells imply their entire physical columns are empty. The
    ordinary non-DG/no-booking candidate then has an invariant compatible hold
    placement except for lateral OOG envelopes elsewhere. Every possible OOG
    placement that could block it is credited optimistically. Thus selecting
    heavy deck cargo requires occupied witness cells OR a possible blocker.
    """
    from time import monotonic
    from stowage.checker.rules import placement_violations
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from .geometry_cuts import horizontal
    instance,T,model,chosen,occupied=(context[k] for k in ('instance','T','model','chosen','occupied'))
    if (rule_freeze_v1_enabled() or T.e6_fallback is not True or T.e6_threshold is None):
        return 0
    boxes={b.id:b for b in instance.containers}
    lateral={bi for bi,bid in enumerate(T.box_ids) if boxes[bid].oog and any((
        boxes[bid].oog.over_width_port_m,boxes[bid].oog.over_width_starboard_m,
        boxes[bid].oog.over_length_fore_m,boxes[bid].oog.over_length_aft_m))}
    if not lateral:
        return 0  # inherited ordinary empty-column cuts already cover this case
    from stowage.checker.rules_special import _interiors_overlap
    clearance=_nonnegative(_clearance_parameter(instance,'expert.e9.over_width_length',
                                               'oog_clearance','minimum_clearance_m'))
    if clearance is None:
        # Unknown policy cannot justify declaring an OOG placement harmless.
        return 0
    heights=[boxes[bid].height_m for bid in T.box_ids]
    hmin,hmax=min(heights),max(heights)
    empty=instance.onboard.model_copy(update={'placements':[],'covers_off':{}})
    oog_geometry=[]
    eps=1e-7
    for (bi,si),var in chosen.items():
        if bi not in lateral:
            continue
        box=boxes[T.box_ids[bi]]
        supports=[(int(sj),int(k)) for sj,k in zip(T.slot_stacks[si],T.slot_k[si]) if sj>=0]
        # The E6 query hypothetically removes b from its old deck position.
        # At most one support per column disappears. No hold support can be
        # removed by an on-deck b. Crediting all such floor drops is optimistic.
        lo=max(float(T.stack_base_z[sj])+max(0,k-int(T.stack_deck[sj]))*hmin
               for sj,k in supports)-eps
        hi=max(float(T.stack_base_z[sj])+k*hmax for sj,k in supports)+eps
        height=box.height_m+box.oog.over_height_m
        oog_geometry.append((var,horizontal(instance,box,T.slot_ids[si]),lo,hi+height))
    count=0
    for bi,bid in enumerate(T.box_ids):
        if monotonic()-context['started']>.4*context['budget_s']:
            break
        box=boxes[bid]
        if box.gross_mass_kg<T.e6_threshold or box.dg is not None or box.oog is not None or box.special_stowage:
            continue
        deck=[var for (b,si),var in chosen.items() if b==bi and T.slot_deck[si]]
        if not deck:
            continue
        for si,slot in enumerate(T.slot_ids):
            if (bi,si) not in chosen or T.slot_deck[si]:
                continue
            physical=[int(ci) for ci in T.slot_cells[si] if ci>=0]
            if any(T.cell_k[ci]!=0 for ci in physical):
                continue
            if any(v.severity=='hard' for v in placement_violations(instance,empty,box,slot,
                                                                        call_index=instance.call_index)):
                continue
            bh=horizontal(instance,box,slot)
            floor_b=max(float(T.stack_base_z[int(sj)]) for sj in T.slot_stacks[si] if sj>=0)
            top_b=floor_b+box.height_m
            blockers=[]
            for var,oh,lo,top in oog_geometry:
                horizontal_gap=math.hypot(*(max(oh[i][0]-bh[i][1],bh[i][0]-oh[i][1],0.) for i in (0,1)))
                close=_interiors_overlap(oh,bh) or horizontal_gap+1e-9<clearance
                possible_vertical=lo<=top_b+eps and top>=floor_b-eps
                if close and possible_vertical:
                    blockers.append(var)
            model.add(sum(deck)<=sum(occupied[ci] for ci in physical)+sum(blockers))
            count+=1
    return count
