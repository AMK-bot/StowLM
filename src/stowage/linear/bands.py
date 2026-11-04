"""Instance-specific mass bands, with the manifest held fixed.

A validity interval is an operating window, not a new cargo generator. Departure
cargo is onboard cargo minus discharges plus the load list; only adjustable
ballast changes displacement. Optional tank windows narrow that interval further.
Hydrostatic draught endpoints are calculated from task 2.1 geometry, and trim and
list remain ship operating limits. Source: Biran & López-Pulido (2014), ch. 2–4.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from stowage.linear.model import ValidityBand


def departure_manifest(instance):
    """Known boxes expected aboard at departure, deterministically ordered."""
    ids = ({p.container_id for p in instance.onboard.placements} - set(instance.discharge_list)) | set(instance.load_list)
    boxes = {b.id:b for b in instance.containers}
    missing=ids-boxes.keys()
    if missing: raise ValueError(f'departure manifest has unknown boxes: {sorted(missing)}')
    return tuple(boxes[i] for i in sorted(ids))


def ballast_windows(instance, windows=None):
    """Adjustable tank-id → closed mass interval, checked against capacity."""
    windows=windows or {}
    tanks={t.id:t for t in instance.vessel.tanks if t.adjustable}
    if set(windows)-tanks.keys(): raise ValueError('ballast window refers to an unknown/nonadjustable tank')
    result={}
    for tid,tank in sorted(tanks.items()):
        cap=tank.capacity_m3*tank.content_density_kg_m3
        lower,upper=windows.get(tid,(0.,cap))
        if not np.isfinite([lower,upper]).all() or not 0<=lower<=upper<=cap:
            raise ValueError(f'invalid ballast mass window for {tid}')
        result[tid]=(float(lower),float(upper))
    return result


def band_for_instance(instance, *, ballast_windows_kg=None, max_abs_trim_m=None):
    """Narrow band for the known manifest and specified adjustable-tank ranges.

    Tank windows also have to be enforced by the consuming optimisation model;
    a ValidityBand stores displacement, not individual tank bounds. Use windows
    around an anticipated ballast solution to reduce hydrostatic curvature error.
    ``max_abs_trim_m`` optionally restricts trim below the vessel operating limit:
    this is a local model-validity restriction, not a vessel-wide accuracy claim.
    The chosen trim also bounds the mean-draught endpoint expansion. The default
    retains the full ship trim limit. The tiny expansion when all ballast windows
    are points only avoids a zero-width mass fit.
    """
    from stowage.linear.physics import _fixed, geometry_for
    h=instance.vessel.hull
    if h is None: raise ValueError('linear band requires a vessel hull')
    trim=h.max_trim_m if max_abs_trim_m is None else float(max_abs_trim_m)
    if not np.isfinite(trim) or not 0<trim<=h.max_trim_m:
        raise ValueError('max_abs_trim_m must be finite, positive and no larger than the vessel trim limit')
    geometry=geometry_for(instance); rho=instance.port.water_density_kg_m3
    from stowage.linear.upright_mass import upright_mass
    upright=upright_mass(geometry)
    def upright_displacement(draught):
        return (upright.mass(draught,rho) if upright is not None else
                geometry.submerged(draught,draught,0.,rho).displacement_kg)
    constant=float(sum(_fixed(instance)[:,0]))
    cargo=sum(box.gross_mass_kg for box in departure_manifest(instance))
    windows=ballast_windows(instance,ballast_windows_kg)
    minimum=constant+cargo+sum(v[0] for v in windows.values())
    maximum=constant+cargo+sum(v[1] for v in windows.values())
    cap_draught=min(h.max_draught_m,instance.port.max_draught_m or h.max_draught_m)
    cap=(upright.capped_mass(geometry,cap_draught,rho) if upright is not None else
         upright_displacement(cap_draught))
    maximum=min(maximum,cap)
    if maximum<minimum: raise ValueError('manifest and ballast minimum exceed the operating displacement')
    if maximum-minimum<max(1.,minimum*1e-9):
        radius=max(.5,minimum*1e-9)
        minimum-=radius; maximum+=radius
    def draught(mass):
        return brentq(lambda d:upright_displacement(d)-mass,
                      .001,geometry.max_z_m-.001)
    lo,hi=draught(minimum),draught(maximum)
    # The band concerns mean draught. Endpoint operational limits are separate
    # affine rows in linear_constraints, with error margins on trim and draught.
    band=ValidityBand(float(minimum),float(maximum),max(.001,lo-trim/2),
        min(geometry.max_z_m-.001,hi+trim/2),trim,h.max_list_rad)
    object.__setattr__(band,'_tank_mass_bounds_kg',windows)
    object.__setattr__(band,'_cargo_mass_kg',float(cargo))
    return band


def box_height(box):
    """Physical height includes declared over-height, conservatively for KG."""
    return box.height_m+(box.oog.over_height_m if box.oog is not None else 0.)


def nominal_height(instance):
    """Cell-token mean known box height; exact for uniform-height manifests.

    A forty-foot box occupies two physical cells and therefore contributes two
    height tokens. Mixed-height arrangements retain a measured residual; this
    nominal arm is not described as a conservative upper bound.
    """
    manifest=departure_manifest(instance)
    tokens=sum(1 if b.length_ft==20 else 2 for b in manifest)
    return (sum(box_height(b)*(1 if b.length_ft==20 else 2) for b in manifest)/tokens
            if tokens else 2.591)


def nominal_region_heights(instance):
    """Known height means within hold/deck eligibility classes."""
    result={}
    for deck in (False,True):
        boxes=[b for b in departure_manifest(instance)
               if not (('on_deck' in b.special_stowage and not deck) or
                       ('under_deck' in b.special_stowage and deck))]
        tokens=sum(1 if b.length_ft==20 else 2 for b in boxes)
        result[deck]=(sum(box_height(b)*(1 if b.length_ft==20 else 2) for b in boxes)/tokens
                      if tokens else nominal_height(instance))
    return result


def _sample_whole_columns(model, instance, rng):
    """Exact saturated-stack shortcut, including heavy boxes in bottom tiers.

    A full mass budget does not require occupying every physical cell. A group
    of equal boxes can saturate a stack with n=capacity/gross boxes, or a paired
    forty-foot column with n=(capacity_left+capacity_right)/gross. Counts must be
    integers and the known inventory must be consumed exactly. Empty upper
    cells retain nominal heights because they contribute no mass or moment.
    """
    from collections import Counter
    boxes=departure_manifest(instance); stacks=instance.vessel.stacks
    if not np.isclose(sum(b.gross_mass_kg for b in boxes),sum(s.max_mass_kg for s in stacks),rtol=0.,atol=1e-4): return None
    groups=Counter((b.length_ft,box_height(b),float(b.gross_mass_kg),
                    tuple(sorted(str(x) for x in b.special_stowage))) for b in boxes)
    cells={c.id:c for c in instance.vessel.cells}; index={cid:i for i,cid in enumerate(model.cell_ids)}
    lookup={(s.bay,s.row,s.on_deck):s for s in stacks}
    used=set(); mass=np.zeros(len(model.cell_ids)); heights=model.cell_vcg_m.copy()
    def count_for(members,key):
        length,height,gross,tags=key
        if gross<=0: return None
        n=int(round(sum(s.max_mass_kg for s in members)/gross))
        if n<1 or any(n>len(s.cell_ids) for s in members): return None
        if any(abs(gross*n/len(members)-s.max_mass_kg)>1e-5 for s in members): return None
        for stack in members:
            if (('on_deck' in tags and not stack.on_deck) or
                ('under_deck' in tags and stack.on_deck) or n*height>stack.max_height_m+1e-7): return None
            if not all(getattr(cells[cid],f'accepts_{length}') for cid in stack.cell_ids[:n]): return None
        if len(members)==2:
            left,right=members
            if abs(left.base_z_m-right.base_z_m)>1e-7: return None
            if any(a[3:]!=b[3:] for a,b in zip(left.cell_ids[:n],right.cell_ids[:n],strict=True)): return None
        return n
    def fill(members,key,n):
        length,height,gross,tags=key
        for stack in members:
            for j,cid in enumerate(stack.cell_ids[:n]):
                mass[index[cid]]=gross/len(members)
                heights[index[cid]]=stack.base_z_m+(j+.5)*height
            used.add(stack.id)
        groups[key]-=n
    # Some saturated inventories place maximum-gross boxes below a lighter
    # remainder box. Keep masses and identities from the manifest; this is an
    # exact two-mass recipe, not permission to split/reweight a real container.
    categories={}
    for key in groups:
        category=(key[0],key[1],key[3])
        categories.setdefault(category,[]).append(key)
    if any(len(keys)>1 for keys in categories.values()):
        counts=groups.copy(); recipe_used=set()
        recipe_mass=np.zeros_like(mass); recipe_heights=heights.copy()
        def recipe_for(members,category):
            length,height,tags=category
            if len(members)==2 and (abs(members[0].max_mass_kg-members[1].max_mass_kg)>1e-5 or
                    abs(members[0].base_z_m-members[1].base_z_m)>1e-7): return None
            if any(('on_deck' in tags and not stack.on_deck) or
                   ('under_deck' in tags and stack.on_deck) for stack in members): return None
            capacity=sum(stack.max_mass_kg for stack in members)
            keys=sorted(categories[category],key=lambda k:k[2],reverse=True)
            for heavy in keys:
                gross=heavy[2]
                if gross<=0: continue
                n=int(np.floor((capacity+1e-5)/gross))
                if n>counts[heavy] or n>min(len(stack.cell_ids) for stack in members): continue
                remainder=capacity-n*gross
                chosen=[heavy]*n
                if abs(remainder)>1e-5:
                    residual=next((key for key in keys if abs(key[2]-remainder)<1e-5 and
                                   key[2]<=gross+1e-5 and counts[key]>n*(key==heavy)),None)
                    if residual is None: continue
                    chosen.append(residual)
                if not chosen: continue
                nbox=len(chosen)
                if any(nbox>len(stack.cell_ids) or nbox*height>stack.max_height_m+1e-7 or
                       not all(getattr(cells[cid],f'accepts_{length}') for cid in stack.cell_ids[:nbox])
                       for stack in members): continue
                if len(members)==2 and any(a[3:]!=b[3:] for a,b in
                        zip(members[0].cell_ids[:nbox],members[1].cell_ids[:nbox],strict=True)): continue
                return chosen
            return None
        def recipe_fill(members,chosen):
            height=chosen[0][1]
            for stack in members:
                for j,key in enumerate(chosen):
                    i=index[stack.cell_ids[j]]
                    recipe_mass[i]=key[2]/len(members)
                    recipe_heights[i]=stack.base_z_m+(j+.5)*height
                recipe_used.add(stack.id)
            counts.subtract(chosen)
        possible=True
        for category in sorted(categories,reverse=True):
            if category[0]==20: continue
            pairs=[(stack,lookup[(stack.bay+2,stack.row,stack.on_deck)]) for stack in stacks
                   if (stack.bay-1)%4==0 and (stack.bay+2,stack.row,stack.on_deck) in lookup]
            rng.shuffle(pairs)
            # Fill the most constrained columns first; stable sorting keeps random ties.
            pairs.sort(key=lambda pair:min(len(stack.cell_ids) for stack in pair))
            for members in pairs:
                if any(stack.id in recipe_used for stack in members): continue
                chosen=recipe_for(members,category)
                if chosen is not None: recipe_fill(members,chosen)
            if any(counts[key] for key in categories[category]): possible=False; break
        remaining=list(stacks); rng.shuffle(remaining)
        remaining.sort(key=lambda stack:len(stack.cell_ids))
        if possible:
            for stack in remaining:
                if stack.id in recipe_used: continue
                choices=[category for category in categories if category[0]==20]
                rng.shuffle(choices)
                for category in choices:
                    chosen=recipe_for((stack,),category)
                    if chosen is not None:
                        recipe_fill((stack,),chosen); break
                else:
                    possible=False; break
        if possible and not any(counts.values()): return recipe_mass,recipe_heights
    # Larger gross masses are more restrictive (they cannot fill smaller stack
    # capacities); place them first, retaining random columns within each class.
    for key in sorted(groups,key=lambda k:(k[0],k[2],k[1],k[3]),reverse=True):
        if key[0]==20: continue
        pairs=[]
        for stack in stacks:
            if (stack.bay-1)%4: continue
            other=lookup.get((stack.bay+2,stack.row,stack.on_deck))
            if other is not None: pairs.append((stack,other))
        rng.shuffle(pairs)
        # Fill the most constrained columns first; stable sorting keeps random ties.
        pairs.sort(key=lambda pair:min(len(stack.cell_ids) for stack in pair))
        for left,right in pairs:
            if left.id in used or right.id in used: continue
            n=count_for((left,right),key)
            if n is None or groups[key]<n: continue
            fill((left,right),key,n)
        if groups[key]: return None
    remaining=list(stacks); rng.shuffle(remaining)
    remaining.sort(key=lambda stack:len(stack.cell_ids))
    for stack in remaining:
        if stack.id in used: continue
        matches=[]
        for key,count in groups.items():
            if key[0]!=20 or count==0: continue
            n=count_for((stack,),key)
            if n is not None and count>=n: matches.append((key,n))
        if not matches: return None
        key,n=matches[rng.integers(len(matches))]
        fill((stack,),key,n)
    return (mass,heights) if not any(groups.values()) else None


def sample_manifest_loading(model, instance, tank, rng):
    """Random bottom-up real-box loading; return cell masses and true centres.

    Placement is conditional on a coarse longitudinal moment target, then exact
    equilibrium decides band membership. Stack weight, height, accepted length,
    aligned supports and the no-20-on-40 rule are enforced during sampling.
    This is validation sampling, not a complete feasibility/packing algorithm:
    an unsuccessful greedy attempt returns None and never changes box masses.
    """
    fast=_sample_whole_columns(model,instance,rng)
    if fast is not None: return fast
    manifest=departure_manifest(instance)
    cargo=sum(b.gross_mass_kg for b in manifest)
    cell_index={cid:i for i,cid in enumerate(model.cell_ids)}
    cells={c.id:c for c in instance.vessel.cells}
    stacks=sorted(instance.vessel.stacks,key=lambda s:s.id)
    state={s.id:[0,s.base_z_m,0.,0] for s in stacks} # depth,z,mass,last length
    lookup={(s.bay,s.row,s.on_deck):s for s in stacks}
    mass=np.zeros(len(model.cell_ids)); heights=model.cell_vcg_m.copy()
    if not cargo: return mass,heights
    displacement=model.const_mass_kg+cargo+sum(tank)
    c0,cx,cd=model.trim_line
    fixed_mx=model.const_moment_x_kgm+model.tank_lcg_m@tank
    ends=c0+cx*(fixed_mx+cargo*np.array([model.cell_lcg_m.min(),model.cell_lcg_m.max()]))+cd*displacement
    lower=max(min(ends),-model.band.max_abs_trim_m*1.05)
    upper=min(max(ends),model.band.max_abs_trim_m*1.05)
    if lower>=upper: return None
    desired=((rng.uniform(lower,upper)-c0-cd*displacement)/cx-fixed_mx)/cargo
    length=instance.vessel.hull.lpp_m
    remaining_mass=float(cargo)
    remaining_moment=float(desired*cargo)
    transverse=rng.uniform(-.3,.3)
    # Forty-foot boxes first avoid stranding a pair behind unequal support
    # heights. Descending mass reduces random greedy bin-packing failures.
    order=sorted(manifest,key=lambda b:(b.length_ft==20,-b.gross_mass_kg,rng.random()))
    for box in order:
        candidates=[]
        height=box_height(box)
        for stack in stacks:
            if 'on_deck' in box.special_stowage and not stack.on_deck: continue
            if 'under_deck' in box.special_stowage and stack.on_deck: continue
            index,z,used,last=state[stack.id]
            if index>=len(stack.cell_ids): continue
            cid=stack.cell_ids[index]; cell=cells[cid]
            if box.length_ft==20:
                if not cell.accepts_20 or last in (40,45): continue
                members=[stack]
            else:
                if (stack.bay-1)%4: continue
                partner=lookup.get((stack.bay+2,stack.row,stack.on_deck))
                if partner is None: continue
                pi,pz,pm,pl=state[partner.id]
                if pi>=len(partner.cell_ids) or abs(pz-z)>1e-6: continue
                partner_cell=cells[partner.cell_ids[pi]]
                if cid[-2:]!=partner_cell.id[-2:]: continue
                if not all(getattr(c,f'accepts_{box.length_ft}') for c in (cell,partner_cell)): continue
                members=[stack,partner]
            if any(state[s.id][2]+box.gross_mass_kg/len(members)>s.max_mass_kg+1e-7 or
                   state[s.id][1]+height>s.base_z_m+s.max_height_m+1e-7 for s in members): continue
            if box.length_ft==20:
                x,y=cell.lcg_m,cell.tcg_m
            else:
                x=.5*(cell.lcg_m+partner_cell.lcg_m)
                y=.5*(cell.tcg_m+partner_cell.tcg_m)
            candidates.append((members,x,y))
        if not candidates: return None
        if box.length_ft!=20:
            # Complete an opened forty-foot column before consuming another
            # pair; scattered partial columns strand capacity from twenty-foot
            # boxes because a twenty may not rest on a forty.
            opened=[candidate for candidate in candidates if state[candidate[0][0].id][0]>0]
            if opened: candidates=opened
        candidate_x=np.array([x for _,x,_ in candidates])
        candidate_y=np.array([y for _,_,y in candidates])
        scaled=(candidate_x-length/2)/length
        transverse_logits=transverse*candidate_y/instance.vessel.hull.beam_m
        desired_remaining=remaining_moment/remaining_mass
        def probabilities(beta):
            logits=beta*scaled+transverse_logits
            weights=np.exp(logits-logits.max())
            return weights/weights.sum()
        def mean(beta): return float(candidate_x@probabilities(beta))
        low_mean,high_mean=mean(-200.),mean(200.)
        # Adapt to the moment still needed after every real box. Clamping only
        # the proposal mean to current legal candidates never changes a box,
        # stack constraint, target trim, or its final exact band admission.
        if desired_remaining<=low_mean: beta=-200.
        elif desired_remaining>=high_mean: beta=200.
        elif high_mean-low_mean<=1e-10: beta=0.
        else: beta=brentq(lambda b:mean(b)-desired_remaining,-200.,200.)
        probability=probabilities(beta)
        members,chosen_x,_=candidates[rng.choice(len(candidates),p=probability)]
        remaining_moment-=box.gross_mass_kg*chosen_x
        remaining_mass-=box.gross_mass_kg
        for stack in members:
            index,z,used,last=state[stack.id]; i=cell_index[stack.cell_ids[index]]
            mass[i]=box.gross_mass_kg/len(members); heights[i]=z+height/2
            state[stack.id]=[index+1,z+height,used+box.gross_mass_kg/len(members),box.length_ft]
    return mass,heights


def project_ballast_for_sampling(model, instance, cell, heights, tank):
    """Project a random tank draw into a hydrostatic interior using an L1 LP.

    L1 distance minimises total absolute change from the genuinely random draw.
    Cargo stays fixed. Only GM/list/trim/draught/displacement are conditioned;
    SF/BM are deliberately absent so strength errors remain observable. This is
    a sampling aid, not a ballast feasibility certificate. Exact equilibrium
    still admits/rejects every condition. Source: scipy.optimize.linprog/HiGHS.
    """
    from scipy.optimize import linprog, OptimizeWarning
    import warnings
    size=len(tank)
    if not size: return tank
    active=model.tank_adjustable.astype(float)
    lower=np.zeros(size); upper=np.zeros(size)
    windows=getattr(model,'_tank_mass_bounds_kg',{})
    for i,tid in enumerate(model.tank_ids):
        if active[i]: lower[i],upper[i]=windows.get(tid,(0.,model.tank_capacity_kg[i]))
    dc=model.const_mass_kg+sum(cell)
    xc=model.const_moment_x_kgm+model.cell_lcg_m@cell
    yc=model.const_moment_y_kgm+model.cell_tcg_m@cell
    zc=model.const_moment_z_kgm+heights@cell
    tx=model.tank_lcg_m*active; ty=model.tank_tcg_m*active; tz=model.tank_vcg_m*active
    a,b=model.km_line; fsm=sum(model.tank_fsm_max_kgm)
    c0,cx,cd=model.trim_line; d0,dd=model.draught_line
    objective=np.r_[np.zeros(size),1/np.maximum(model.tank_capacity_kg,1.)]
    bounds=list(zip(lower,upper))+[(0.,None)]*size
    expanded=(model.band.max_abs_trim_m>model.max_trim_m+1e-12 or model.band.max_abs_heel_rad>model.max_list_rad+1e-12)
    for interior in (.65,.9,.99):
        rows=[]; rhs=[]
        def add(coefficient,constant,lo=-np.inf,hi=np.inf):
            if np.isfinite(hi): rows.append(np.r_[coefficient,np.zeros(size)]); rhs.append(hi-constant)
            if np.isfinite(lo): rows.append(np.r_[-coefficient,np.zeros(size)]); rhs.append(constant-lo)
        add(active,dc,model.band.displacement_min_kg,model.band.displacement_max_kg)
        limit=interior*(model.band.max_abs_trim_m if expanded else min(model.max_trim_m,model.band.max_abs_trim_m))
        trim_c=c0+cx*xc+cd*dc; trim_t=cx*tx+cd*active
        add(trim_t,trim_c,-limit,limit)
        if not expanded:
            add(tz+(model.gm_min_m-b)*active,zc+(model.gm_min_m-b)*dc+fsm-a,hi=0.)
        restoring_c=a+b*dc-zc-fsm; restoring_t=b*active-tz
        angle=np.tan(.3*(model.band.max_abs_heel_rad if expanded else min(model.max_list_rad,model.band.max_abs_heel_rad)))
        for sign in (-1.,1.): add(sign*ty-angle*restoring_t,sign*yc-angle*restoring_c,hi=0.)
        add(dd*active,d0+dd*dc,model.band.draught_min_m,model.band.draught_max_m)
        maximum=min(instance.vessel.hull.max_draught_m,
                    instance.port.max_draught_m or instance.vessel.hull.max_draught_m)
        add(dd*active+.5*trim_t,d0+dd*dc+.5*trim_c,hi=maximum)
        add(dd*active-.5*trim_t,d0+dd*dc-.5*trim_c,
            lo=instance.vessel.hull.min_draught_fwd_m,hi=maximum)
        # x - u <= random draw, -x - u <= -random draw.
        for i in range(size):
            row=np.zeros(2*size); row[i]=1.; row[size+i]=-1.
            rows.append(row); rhs.append(tank[i])
            row=np.zeros(2*size); row[i]=-1.; row[size+i]=-1.
            rows.append(row); rhs.append(-tank[i])
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',OptimizeWarning)
            result=linprog(objective,A_ub=np.asarray(rows),b_ub=np.asarray(rhs),bounds=bounds,
                method='highs',options={'threads':1,'parallel':False})
        if result.success: return np.clip(result.x[:size],lower,upper)
    return None
