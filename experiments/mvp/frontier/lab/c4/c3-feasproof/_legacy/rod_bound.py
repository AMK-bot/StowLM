"""Outward whole-map deck rod inequalities from positive chain compliance.

No complete page pool or securing surrogate is assumed. Every actual legal
deck endchain maps to a positive contribution in these aggregate bay rows.
See rod_notes.md; feasibility remains a relaxation witness, never a stow pass.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
from functools import lru_cache
import hashlib
import importlib.util
import math
from pathlib import Path


LASHING_SHA256 = '6bf6a1ba92cf17687716fbb6eb8ac7bf528971356614134b84d5bb89f1ecfdb8'
GUARD = 1.-1.e-8


@lru_cache(maxsize=1)
def _physics():
    spec = importlib.util.spec_from_file_location('feasproof_rod_physics',Path(__file__).with_name('physics.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parameters(instance):
    from stowage.checker import lashing
    from stowage.frozen import active_stubs
    from stowage.conventions import parse_slot
    if active_stubs():
        raise ValueError('active frozen-interface override')
    if hashlib.sha256(Path(lashing.__file__).read_bytes()).hexdigest() != LASHING_SHA256:
        raise ValueError('unreviewed lashing source')
    if instance.onboard.placements or instance.onboard.covers_off:
        raise ValueError('rod aggregate currently requires empty decision arrival')
    F = Fraction
    boxes = {b.id:b for b in instance.containers}
    heights = [float(boxes[bid].height_m+(boxes[bid].oog.over_height_m if boxes[bid].oog else 0.))
               for bid in instance.load_list]
    if not heights or any(not math.isfinite(h) or h <= 0 for h in heights):
        raise ValueError('positive finite load-list box heights required')
    # Every relevant support sum contains at most all load-list heights.
    # The extra factor/one-metre reserve makes this a conservative bound on
    # intermediate absolute operands, not an assumed exact source geometry.
    base = max([abs(s.base_z_m) for s in instance.vessel.stacks]+[0.])
    bound = 2*(base+math.fsum(heights)+max(heights)+1.)
    ulp = F(math.ulp(bound))
    hmin,hmax = F(min(heights))-2*ulp,F(max(heights))+2*ulp
    if hmin <= 0:
        raise ValueError('height rounding envelope reaches zero')
    # fl(fl(bottom + fl(.45*height))-bottom) differs by at most three
    # conservative operand ulps from the exact .45*height product.
    rlo,rhi = F(.45)-4*ulp/hmin-F(1.e-12),F(.45)+4*ulp/hmin+F(1.e-12)
    if not 0 <= rlo <= rhi <= 1:
        raise ValueError('CG-fraction positivity not established')
    deck_indices = [(parse_slot(cid).tier,k) for s in instance.vessel.stacks
                    if s.on_deck for k,cid in enumerate(s.cell_ids)]
    starts = {tier-2*k for tier,k in deck_indices}
    ordinal_certified = len(starts) <= 1
    return dict(hmin=hmin,hmax=hmax,ulp=ulp,rlo=rlo,rhi=rhi,
                ordinal_certified=ordinal_certified,
                width=F(lashing.WIDTH),stiffness=F(3.7e6),
                ea1=F(lashing._rod_stiffness(1.,1)),ea2=F(lashing._rod_stiffness(1.,2)))


def _endpoint_coefficient(z,a,p,kind):
    """Rational lower endpoint K for rod, horizontal rod or vertical rod."""
    F = Fraction
    ph = _physics()
    z,W,k = F(z),p['width'],p['stiffness']
    ea = p['ea1'] if a == 1 else p['ea2']
    length_lo = ph._sqrt_lower(W*W+z*z)
    length_hi = ph._sqrt_upper(W*W+z*z)
    denominator_hi = k*length_hi**3+a*ea*W*W
    if kind == 'resultant':
        return ea*W*length_lo/denominator_hi
    if kind == 'horizontal':
        return ea*W*W/denominator_hi
    if kind == 'vertical':
        return ea*W*z/denominator_hi
    raise ValueError('unknown rod component')


def coefficient_floor(*,anchor_count,z_lower,z_upper,parameters,kind='resultant'):
    """Minimum K over the entire anchor-height interval, not sampled points.

    Resultant K has one stationary maximum. Horizontal K is decreasing.
    Vertical K also has at most one stationary maximum for positive height.
    Each minimum therefore occurs at an endpoint. The function uses rational
    lower enclosures at both endpoints, followed by a source-floating guard.
    """
    if anchor_count not in (1,2) or not 0 < z_lower <= z_upper:
        raise ValueError('invalid anchor interval')
    value = min(_endpoint_coefficient(z_lower,anchor_count,parameters,kind),
                _endpoint_coefficient(z_upper,anchor_count,parameters,kind))
    return _physics()._down(value)*GUARD


def _box_coefficient(instance,box,allocation,ordinal,p,gm_lo,kind):
    """Minimum end-share-weighted rod demand assigned to one physical cell."""
    from stowage.checker import lashing
    F = Fraction
    declared_height = F(float(box.height_m+(box.oog.over_height_m if box.oog else 0.)))
    hlo,hhi = declared_height-2*p['ulp'],declared_height+2*p['ulp']
    if hlo <= 0:
        raise ValueError('own height enclosure reaches zero')
    # For tier zero an endchain may stop after this box, or continue to an
    # anchor on box two. Higher tiers necessarily belong to a two-box anchor.
    if ordinal is None:
        cases = [(1,hlo-p['ulp'],hhi+p['ulp']),
                 (2,hlo+p['hmin']-p['ulp'],hhi+p['hmax']+p['ulp']),
                 (2,2*p['hmin']-p['ulp'],2*p['hmax']+p['ulp'])]
    elif ordinal == 0:
        cases = [(1,hlo-p['ulp'],hhi+p['ulp']),
                 (2,hlo+p['hmin']-p['ulp'],hhi+p['hmax']+p['ulp'])]
    elif ordinal == 1:
        cases = [(2,hlo+p['hmin']-p['ulp'],hhi+p['hmax']+p['ulp'])]
    else:
        cases = [(2,2*p['hmin']-p['ulp'],2*p['hmax']+p['ulp'])]
    K = min(coefficient_floor(anchor_count=a,z_lower=zlo,z_upper=zhi,parameters=p,kind=kind)
            for a,zlo,zhi in cases)
    if ordinal is None or ordinal == 0:
        inertial_weight,wind_weight = p['rlo'],F(.5)
    elif ordinal == 1:
        inertial_weight,wind_weight = 1+p['rlo'],F(1.5)
    else:
        inertial_weight,wind_weight = F(2),F(2)
    # Lashing's planned CG is at .45 of actual expanded height. Retain an
    # outward lower value even after its own floating support subtraction.
    zlo = F(allocation['support_z_lower_m'])+p['rlo']*hlo-3*p['ulp']
    motion = _physics().motion_floor(instance,gm_lower=gm_lo,z_lower=_physics()._down(zlo))
    mass = max(box.gross_mass_kg,2500. if box.length_ft == 20 else 3500.)
    oog = box.oog
    length = lashing.LENGTH[box.length_ft]
    expanded_l = length+(oog.over_length_fore_m+oog.over_length_aft_m if oog else 0.)
    wind = max(F(15700. if box.length_ft == 20 else 31600.),F(expanded_l)*hlo*1000)
    dx = (oog.over_length_fore_m-oog.over_length_aft_m)/2 if oog else 0.
    securing_length = 12.192 if box.length_ft == 45 else length
    # The class forgets horizontal OOG. Minima across members are taken by the
    # caller. Subtracting a source-rounding guard also encloses abs end-share.
    share = max(0.,min(abs(.5-dx/securing_length),abs(.5+dx/securing_length))-1.e-12)
    demand = K*(float(inertial_weight)*mass*motion['at_m_s2']+float(wind_weight*wind))
    return max(0.,demand*share*GUARD)


def attach_rod_rows(model,instance,*,gm_lo=0.):
    """Append safe global aggregate rod rows; return metadata, no solve.

    Every physical column has at most two root endchains. Every root rod is
    individually limited by 212500N resultant, 150000N horizontal and225000N
    vertical. Summing gives per-column capacities425000,300000,450000N.
    Long boxes contribute one end to each column,20s two ends to one column.
    """
    p = _parameters(instance)
    boxes = {b.id:b for b in instance.containers}
    columns = {'global':sum(1 for s in instance.vessel.stacks if s.on_deck and s.cell_ids)}
    limits = dict(resultant=425000.,horizontal=300000.,vertical=450000.)
    rows = {kind:defaultdict(dict) for kind in limits}
    root_rows = defaultdict(dict)
    model._rod_force_diagnostics = {}
    cache = {}
    for j,a in enumerate(model.allocations):
        cls = model.metadata['classes'][a['class_index']]
        for bay,deck,ordinal,count in a['signature']:
            if not deck:
                continue
            region = 'global'
            if ordinal == 0:
                root_rows[region][j] = root_rows[region].get(j,0.)+2*count/cls['units']
            certified_ordinal = ordinal if p['ordinal_certified'] else None
            for kind in limits:
                coefficients = []
                for bid in cls['members']:
                    key = (bid,a['support_z_lower_m'],certified_ordinal,gm_lo,kind)
                    if key not in cache:
                        cache[key] = _box_coefficient(instance,boxes[bid],a,certified_ordinal,p,gm_lo,kind)
                    coefficients.append(cache[key])
                # min share is a single end's force. A twenty occupies one
                # physical cell with two ends; a long spans two cells with
                # one end each. 2/units expresses both cases.
                coefficient = min(coefficients)*2*count/cls['units']
                row = rows[kind][region]
                row[j] = row.get(j,0.)+coefficient
    records = []
    for kind,by_bay in rows.items():
        for bay,row in sorted(by_bay.items()):
            cap = columns[bay]*limits[kind]
            name = f'rod_{kind}_bay_{bay}'
            model.add_row({j:v/1.e6 for j,v in row.items()},upper=cap/1.e6,name=name)
            records.append(dict(name=name,bay=bay,component=kind,
                                available_force_n=cap,physical_columns=columns[bay],
                                nonzero_coefficients=len(row),
                                required_force_n=None,
                                row_index=len(model.rows)-1))
            # Every real endchain starts at an occupied bottom tier. A20
            # starts two chains in one column; a long starts one per column.
            # This variable RHS removes fictional rods in empty columns and
            # the second fictional rod under a pure-long root.
            root_capacity = {j:v*limits[kind]/2 for j,v in root_rows[bay].items()}
            occupied_row = dict(row)
            for j,v in root_capacity.items():
                occupied_row[j] = occupied_row.get(j,0.)-v
            name = f'rod_occupied_roots_{kind}_bay_{bay}'
            model.add_row({j:v/1.e6 for j,v in occupied_row.items()},upper=0.,name=name)
            records.append(dict(name=name,bay=bay,component=kind,
                                available_force_n=None,physical_columns=columns[bay],
                                available_force_kind='occupied bottom-tier endchains',
                                nonzero_coefficients=len(occupied_row),required_force_n=None,
                                row_index=len(model.rows)-1))
            model._rod_force_diagnostics[len(model.rows)-1] = (dict(row),root_capacity)
    metadata = dict(source_sha256=LASHING_SHA256,gm_lashing_lower_m=gm_lo,
                    stiffness_n_m=3700000.,height_lower_m=float(p['hmin']),height_upper_m=float(p['hmax']),
                    cg_fraction_lower=float(p['rlo']),cg_fraction_upper=float(p['rhi']),
                    global_uniform_deck_tier_ordinal=p['ordinal_certified'],
                    source_operand_rounding_ulp_m=float(p['ulp']),rows=records,
                    status='outward necessary aggregate; no feasibility or infeasibility claim')
    model.metadata['rod'] = metadata
    return metadata


def evaluate_rod_rows(model,values):
    """Populate independently inspectable force/capacity diagnostics after LP."""
    result = []
    for record in model.metadata.get('rod',{}).get('rows',[]):
        if record.get('available_force_kind'):
            demand_row,capacity_row = model._rod_force_diagnostics[record['row_index']]
            demand = math.fsum(float(values[j])*v for j,v in demand_row.items())
            cap = math.fsum(float(values[j])*v for j,v in capacity_row.items())
        else:
            row = model.rows[record['row_index']]
            demand = math.fsum(float(values[j])*v*1.e6 for j,v in row.items())
            cap = record['available_force_n']
        result.append(dict(record,required_force_n=demand,
                           available_force_n=cap,ratio=demand/cap if cap else (0. if demand <= 0 else math.inf)))
    return result
