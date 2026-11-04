"""Optional source-pinned necessary weather-KG row for final-stow CP models.

This is not a ballast feasibility certificate. It projects away every tank,
relaxes every operational restriction and adds only a necessary upper moment.
"""
from fractions import Fraction as F
import hashlib
import json
import math
from pathlib import Path, PurePosixPath

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
NUMERIC_SCOPE = dict(max_terms=1000,max_total_mass_kg=10**9,
                     max_mass_kg=10**8,max_abs_coordinate_m=1000,
                     max_stack_ordinal=100,max_ordinary_height_m=10)


def _numeric_scope(instance,tables):
    """Finite canonical TRAIN domain; never silently extrapolate tolerances."""
    hull=instance.vessel.hull
    objects=list(instance.vessel.fixed_loads)
    if (len(objects)+len(tables.box_ids)+len(instance.vessel.tanks)+1>
            NUMERIC_SCOPE['max_terms']):
        raise ValueError('Optional physics row exceeds finite summation domain')
    masses=[hull.lightship_mass_kg]+[q.mass_kg for q in objects]
    masses += [b.gross_mass_kg for b in instance.containers if b.id in instance.load_list]
    masses += [t.capacity_m3*t.content_density_kg_m3 for t in instance.vessel.tanks]
    if (any(not math.isfinite(m) or m<0 or m>NUMERIC_SCOPE['max_mass_kg'] for m in masses)
            or sum(masses)>NUMERIC_SCOPE['max_total_mass_kg']):
        raise ValueError('Optional physics row exceeds finite mass domain')
    positions=[hull.lightship_vcg_m]+[q.z_m for q in objects]
    positions += [float(z) for z in tables.stack_base_z]
    if any(not math.isfinite(z) or abs(z)>NUMERIC_SCOPE['max_abs_coordinate_m'] for z in positions):
        raise ValueError('Optional physics row exceeds finite coordinate domain')
    if any(int(k)>NUMERIC_SCOPE['max_stack_ordinal'] for row in tables.slot_k for k in row):
        raise ValueError('Optional physics row exceeds finite stack domain')
    if any(not 0<b.height_m<=NUMERIC_SCOPE['max_ordinary_height_m'] for b in instance.containers
           if b.id in instance.load_list):
        raise ValueError('Optional physics row exceeds finite height domain')


def max_ballast_benefit(tanks, allowance):
    """Exact maximum sum benefit*mass subject to independent caps and mass cap."""
    remaining=max(F(0),F(allowance))
    result=F(0)
    for cap,benefit in sorted(((F(c),F(b)) for c,b in tanks),
                              key=lambda item:item[1],reverse=True):
        if cap<0:
            raise ValueError('Tank capacity must be nonnegative')
        if benefit<=0 or remaining<=0:
            continue
        take=min(cap,remaining)
        result+=take*benefit
        remaining-=take
    return result


def derive(instance, tables):
    """Return exact lower cargo-moment coefficients and an outward upper RHS."""
    from stowage.frozen import active_stubs
    from stowage.checker import stability,visibility
    if active_stubs():
        raise ValueError('Frozen overrides are unsupported by optional physics row')
    expected=json.loads((HERE/'medium-physics-sources.json').read_text())
    for source,want in expected.items():
        if hashlib.sha256((ROOT/source).read_bytes()).hexdigest()!=want:
            raise ValueError('Optional physics row source mismatch: '+source)
    if (instance.onboard.placements or instance.onboard.covers_off or
            instance.discharge_list or instance.plan_calls or instance.forecasts):
        raise ValueError('Optional physics row supports empty closed decision arrival')
    hull=instance.vessel.hull
    ref=PurePosixPath(hull.hull_offsets_ref.split('#')[0])
    if str(ref.parent)!='params/fleet' or ref.suffix!='.toml':
        raise ValueError('Unsupported hull reference')
    # Stability's authoritative tank provider may differ from a visibility
    # hull-offset stem even if L/B/D happen to match. Restrict to the pinned
    # canonical feeder500 parameter bytes and consume its actual context.
    provider=instance.vessel.slice_of or instance.vessel.id
    if provider!=ref.stem or provider!='feeder_500':
        raise ValueError('Optional physics row needs canonical matching feeder500 provider')
    parameters=json.loads((HERE/'medium-physics-parameters.json').read_text())
    parameter_hash=hashlib.sha256((ROOT/ref).read_bytes()).hexdigest()
    if parameters.get(str(ref))!=parameter_hash:
        raise ValueError('Optional physics row canonical parameter bytes changed')
    geometry,params=stability._context(instance.vessel)
    if any(not math.isclose(a,b,rel_tol=1e-9) for a,b in zip(
            (geometry.lpp_m,geometry.beam_m,geometry.depth_m),
            (hull.lpp_m,hull.beam_m,hull.depth_m))):
        raise ValueError('Hull geometry mismatch')
    boxes={b.id:b for b in instance.containers}
    if set(tables.box_ids)!=set(instance.load_list):
        raise ValueError('Optional physics row requires exact load-only decision catalog')
    _numeric_scope(instance,tables)
    # All guards weaken the inequality. A whole kg of displacement and 1e-6m
    # vertical-coordinate allowance exceed source arithmetic tolerances.
    guard_z=F(1,10**6)
    maxdraft=min(hull.max_draught_m,instance.port.max_draught_m or hull.max_draught_m)
    K=F(3,2)*(F(maxdraft)+guard_z)
    fixed=visibility._fixed_loads(instance.vessel)
    M0=F(hull.lightship_mass_kg)+sum((F(q.mass_kg) for q in fixed),F(0))
    Z0=F(hull.lightship_mass_kg)*F(hull.lightship_vcg_m)
    Z0+=sum((F(q.mass_kg)*F(q.z_m) for q in fixed),F(0))
    M0+=sum((F(boxes[bid].gross_mass_kg) for bid in instance.load_list),F(0))
    _,_,_,dmax=stability._loading_policy(instance)
    raw={q['id']:q for q in params.get('tanks',[])}
    def number(value):
        return F(float(value['value'] if isinstance(value,dict) else value))
    tank_terms=[]
    tank_evidence=[]
    for tank in instance.vessel.tanks:
        g=raw[tank.id].get('geometry',raw[tank.id])
        # Bounding boxes enclose every actual liquid centroid, all fills/poses.
        zlo=number(g['z_min_m'])-guard_z
        zhi=number(g['z_max_m'])+guard_z
        if max(abs(zlo),abs(zhi))>NUMERIC_SCOPE['max_abs_coordinate_m'] or zlo>zhi:
            raise ValueError('Optional physics row tank extent outside finite domain')
        cap=F(tank.capacity_m3)*F(tank.content_density_kg_m3)*(1+F(1,10**10))+1
        tank_terms.append((cap,K-zlo))
        tank_evidence.append(dict(id=tank.id,z_lower_m=float(zlo),capacity_kg=float(cap)))
    benefit=max_ballast_benefit(tank_terms,F(dmax)+1-M0)
    rhs=K*M0-Z0+benefit+1
    hmin=min((F(boxes[bid].height_m) for bid in tables.box_ids),default=F(0))
    coeff={}
    for bi,bid in enumerate(tables.box_ids):
        box=boxes[bid]
        if not math.isfinite(box.gross_mass_kg) or box.gross_mass_kg<0:
            raise ValueError('Finite nonnegative cargo masses required')
        for si in range(len(tables.slot_ids)):
            supports=[F(float(tables.stack_base_z[int(j)]))+int(k)*hmin
                      for j,k in zip(tables.slot_stacks[si],tables.slot_k[si]) if j>=0]
            z=sum(supports,F(0))/len(supports)+F(box.height_m)/2-guard_z
            coeff[bi,si]=F(box.gross_mass_kg)*z
    return coeff,rhs,dict(K_upper_m=float(K),fixed_with_cargo_mass_kg=float(M0),
        fixed_vertical_moment_kg_m=float(Z0),ballast_benefit_kg_m=float(benefit),
        upper_cargo_moment_kg_m=float(rhs),tank_envelopes=tank_evidence,
        source_sha256=expected,geometry_parameter_sha256=hashlib.sha256((ROOT/ref).read_bytes()).hexdigest(),
        parameter_provider=provider,numeric_scope=NUMERIC_SCOPE,
        numerical_qualification='source-pinned canonical feeder500 within explicit finite magnitude domain; no scale-independent hull-roundoff claim',
        assumption='mandatory weather applicability; nonnegative FSC; closed full-checker hull envelope',
        coordinate_guard_m=float(guard_z))


def add_row(context):
    """Fail open on unsupported evidence, preserving the frozen bound interface."""
    try:
        coeff,rhs,info=derive(context['instance'],context['T'])
        terms={key:math.floor(coeff[key]) for key in context['chosen']}
        upper=math.ceil(rhs)
        if sum(abs(c) for c in terms.values())+abs(upper)>=2**61:
            raise ValueError('CP integer overflow guard')
    except (ValueError,KeyError,TypeError,AttributeError,OSError) as exc:
        return dict(added=False,reason=str(exc))
    context['model'].add(sum(terms[key]*var for key,var in context['chosen'].items())<=upper)
    return dict(added=True,integer_scale_kg_m=1,integer_upper=upper,**info)
