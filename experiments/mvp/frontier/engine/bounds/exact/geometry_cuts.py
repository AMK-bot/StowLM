# Copied from experiments/mvp/frontier/lab/c4/c2-hatch/geometry_cuts.py; engine integration changes are local.
"""Outward-rounded final cargo floors and lazy geometric disjunctions."""
import math
from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M
from stowage.checker.rules_special import _clearance_parameter, _nonnegative, _interiors_overlap
from stowage.rules.catalogue import rule_freeze_v1_enabled
SCALE = 1000000
EPS = 3  # three micrometres of outward relaxation, exceeding checker tolerance


def floor(value):
    n,d = float(value).as_integer_ratio()
    return n*SCALE//d


def ceil(value):
    n,d = float(value).as_integer_ratio()
    return -(-n*SCALE//d)


def add_floor_domains(context):
    if 'slot_floor_lo' in context:
        return
    T, model = context['T'],context['model']
    boxes = {b.id:b for b in context['instance'].containers}
    cell_lo,cell_hi = {},{}
    for sj, column in enumerate(T.stack_cells):
        low,high = floor(T.stack_base_z[sj]),ceil(T.stack_base_z[sj])
        for ci in column:
            cell_lo[ci],cell_hi[ci] = low,high
            # The checker's special geometry accumulates regular box height,
            # excluding overheight. One physical column counts each box once.
            low = low+sum(floor(boxes[T.box_ids[bi]].height_m)*var
                          for bi,var in context['cell_placements'][ci])
            high = high+sum(ceil(boxes[T.box_ids[bi]].height_m)*var
                            for bi,var in context['cell_placements'][ci])
    slot_lo,slot_hi = {},{}
    for si in range(len(T.slot_ids)):
        physical = [int(ci) for ci in T.slot_cells[si] if ci >= 0]
        slot_lo[si] = model.new_int_var(-10**12,10**12,f'floor_lo_{si}')
        slot_hi[si] = model.new_int_var(-10**12,10**12,f'floor_hi_{si}')
        model.add_max_equality(slot_lo[si],[cell_lo[ci] for ci in physical])
        model.add_max_equality(slot_hi[si],[cell_hi[ci] for ci in physical])
    context['slot_floor_lo'],context['slot_floor_hi'] = slot_lo,slot_hi
    context['geometry_seen'] = set()


def horizontal(instance, box, slot):
    from stowage.conventions import cells_of_slot
    cells = {c.id:c for c in instance.vessel.cells}
    physical = [cells[cid] for cid in cells_of_slot(slot)]
    x,y = (sum(getattr(c,axis) for c in physical)/len(physical) for axis in ('lcg_m','tcg_m'))
    o = box.oog
    return ((x-CONTAINER_LENGTH_M[box.length_ft]/2-(o.over_length_aft_m if o else 0.),
             x+CONTAINER_LENGTH_M[box.length_ft]/2+(o.over_length_fore_m if o else 0.)),
            (y-CONTAINER_WIDTH_M/2-(o.over_width_starboard_m if o else 0.),
             y+CONTAINER_WIDTH_M/2+(o.over_width_port_m if o else 0.)))


def add_oog_pair(context, assignment, ids):
    if len(ids) != 2:
        return False
    instance,T,model,chosen = (context[k] for k in ('instance','T','model','chosen'))
    boxes = {b.id:b for b in instance.containers}
    a,b = sorted(ids)
    slots = [T.slot_index[assignment[bid]] for bid in (a,b)]
    key = ('oog',a,slots[0],b,slots[1])
    add_floor_domains(context)
    if key in context['geometry_seen']:
        return False
    ah,bh = (horizontal(instance,boxes[bid],assignment[bid]) for bid in (a,b))
    gap = math.hypot(*(max(ah[i][0]-bh[i][1],bh[i][0]-ah[i][1],0.) for i in (0,1)))
    clearance = _nonnegative(_clearance_parameter(instance,'expert.e9.over_width_length','oog_clearance','minimum_clearance_m'))
    if clearance is None or not (_interiors_overlap(ah,bh) or gap+1e-9 < clearance):
        return False
    xa,xb = (chosen[T.box_index[bid],si] for bid,si in zip((a,b),slots))
    alo,blo = (context['slot_floor_lo'][si] for si in slots)
    ahi,bhi = (context['slot_floor_hi'][si] for si in slots)
    ha,hb = (floor(boxes[bid].height_m+(boxes[bid].oog.over_height_m if boxes[bid].oog else 0.)) for bid in (a,b))
    direction = model.new_bool_var(f'geometry_direction_{len(context["geometry_seen"])}')
    model.add(ahi >= blo+hb-EPS).only_enforce_if([xa,xb,direction])
    model.add(bhi >= alo+ha-EPS).only_enforce_if([xa,xb,direction.Not()])
    context['geometry_seen'].add(key)
    return True


def add_heat(context, assignment, bid):
    instance,T,model,chosen = (context[k] for k in ('instance','T','model','chosen'))
    box = next(b for b in instance.containers if b.id == bid)
    si = T.slot_index[assignment[bid]]
    add_floor_domains(context)
    clearance = _nonnegative(_clearance_parameter(instance,'expert.e8.away_from_heat','minimum_clearance','heat_clearance_m'))
    if clearance is None:
        return False
    h = horizontal(instance,box,assignment[bid])
    height = box.height_m+(box.oog.over_height_m if box.oog else 0.)
    added = False
    for source in instance.vessel.heat_sources:
        key = ('heat',bid,si,source.id)
        if key in context['geometry_seen']:
            continue
        hb = ((source.x_min_m,source.x_max_m),(source.y_min_m,source.y_max_m))
        dxy = math.hypot(*(max(h[i][0]-hb[i][1],hb[i][0]-h[i][1],0.) for i in (0,1)))
        overlap = _interiors_overlap(h,hb)
        if dxy+1e-9 >= clearance and not overlap:
            continue
        # E8 permits exact face contact at zero clearance; positive distance
        # uses the checker's numerical tolerance before outward rounding.
        gap = math.sqrt(max(0.,max(0.,clearance-1e-9)**2-dxy*dxy))
        x = chosen[T.box_index[bid],si]
        direction = model.new_bool_var(f'heat_direction_{len(context["geometry_seen"])}')
        lo,hi = context['slot_floor_lo'][si],context['slot_floor_hi'][si]
        model.add(hi >= floor(source.z_max_m+gap)-EPS).only_enforce_if([x,direction])
        model.add(lo+floor(height) <= ceil(source.z_min_m-gap)+EPS).only_enforce_if([x,direction.Not()])
        context['geometry_seen'].add(key)
        added = True
    return added


def add_all_oog_pairs(context):
    """Exact necessary E9 vertical separation, grouping identical envelopes.

    Grouping changes no geometry: a group shares length, height and every OOG
    overhang, at the same nominal slot. A Boolean says any group member uses
    that slot. This avoids one disjunction per container identity.
    """
    instance,T,model,chosen = (context[k] for k in ('instance','T','model','chosen'))
    boxes = {b.id:b for b in instance.containers}
    owners = [bi for bi,bid in enumerate(T.box_ids) if boxes[bid].oog and any((
        boxes[bid].oog.over_width_port_m,boxes[bid].oog.over_width_starboard_m,
        boxes[bid].oog.over_length_fore_m,boxes[bid].oog.over_length_aft_m))]
    if not owners:
        return 0
    add_floor_domains(context)
    groups={}
    for (bi,si),var in chosen.items():
        b=boxes[T.box_ids[bi]];o=b.oog
        sig=(b.length_ft,b.height_m+(o.over_height_m if o else 0.),
             o.over_width_port_m if o else 0.,o.over_width_starboard_m if o else 0.,
             o.over_length_fore_m if o else 0.,o.over_length_aft_m if o else 0.)
        groups.setdefault((si,sig),[]).append((bi,var))
    clearance=_nonnegative(_clearance_parameter(instance,'expert.e9.over_width_length','oog_clearance','minimum_clearance_m'))
    if clearance is None:
        return 0
    count=0
    for bi in owners:
        owner=boxes[T.box_ids[bi]]
        for (b,si),xa in list(chosen.items()):
            if b!=bi:
                continue
            ah=horizontal(instance,owner,T.slot_ids[si])
            physical=set(int(ci) for ci in T.slot_cells[si] if ci>=0)
            for (sj,sig),members in groups.items():
                if physical.intersection(int(ci) for ci in T.slot_cells[sj] if ci>=0):
                    continue
                others=[(bj,var) for bj,var in members if bj!=bi]
                if not others:
                    continue
                bh=horizontal(instance,boxes[T.box_ids[others[0][0]]],T.slot_ids[sj])
                gap=math.hypot(*(max(ah[i][0]-bh[i][1],bh[i][0]-ah[i][1],0.) for i in (0,1)))
                if not (_interiors_overlap(ah,bh) or gap+1e-9<clearance):
                    continue
                use=model.new_bool_var(f'oog_group_{bi}_{si}_{sj}_{count}')
                model.add_max_equality(use,[var for _,var in others])
                direction=model.new_bool_var(f'oog_eager_direction_{count}')
                alo,ahi=context['slot_floor_lo'][si],context['slot_floor_hi'][si]
                blo,bhi=context['slot_floor_lo'][sj],context['slot_floor_hi'][sj]
                ha=floor(owner.height_m+(owner.oog.over_height_m if owner.oog else 0.))
                hb=floor(sig[1])
                model.add(ahi>=blo+hb-EPS).only_enforce_if([xa,use,direction])
                model.add(bhi>=alo+ha-EPS).only_enforce_if([xa,use,direction.Not()])
                count+=1
    return count
