"""Measured linear hydrostatics, using the declared hull's numerical sections.

A buoyancy influence is the change in each immersed sectional mass when total
mass or longitudinal moment changes. Two waterplane perturbations determine
this derivative; Gauss weights are retained, never replaced by uniform spacing.
APPROXIMATION: derivatives are local, tank arms are half-full, and box heights
are standard. The measured residuals cover these approximations only for the
sampled validity band, not arbitrary extrapolation. Sources: Biran & López-
Pulido, Ship Hydrostatics and Stability (2014), chapters 2–4; IMO IS Code Part B
3.1 (free surfaces). Empirical maxima are validation evidence, not a mathematical
uniform error bound; independent validation should enlarge margins as needed.

Point GM/list estimates use the known tank fills' upright free-surface term.
Solver constraints independently reserve the maximum free-surface moment of
all tanks, including tanks currently full/empty. This deliberate conservative
reserve is reported separately from affine-row residual error. D49 requires
explicit margin directions: conservative rows reserve unsafe-side residuals
inward; necessary screens widen limits using independently measured opposite
residuals. Both qualifications are empirical; the exact checker certifies plans.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import hashlib
import json
from stowage.toml_cache import load_bytes as load_toml_bytes
import numpy as np
from scipy.optimize import brentq

from stowage.linear.model import LinearErrors, LinearModel, ValidityBand
from stowage.conventions import canonical_vessel_id
from stowage.vessel.fleet import list_fleet, load_ship_params
from stowage.vessel.hydrostatics import HullGeometry
from stowage.versions import LINEAR_MODEL_VERSION

G = 9.80665
_CACHE = {}
_EXACT_CONTEXT = {}


@lru_cache(maxsize=8)
def _parsed_linear_params(parameter_bytes):
    """Match fleet.load_ship_params parsing; the exact bytes own the cache key."""
    from .cache import prepare_parameters
    return prepare_parameters(load_toml_bytes(parameter_bytes))


def _params_for(instance):
    """Resolve the complete original fleet record, including slice provenance."""
    return _resolve_params_for(instance, shared=False)


def _prepared_linear_params(parameter_bytes):
    return _parsed_linear_params(parameter_bytes)


def _resolve_params_for(instance, *, shared):
    vessel = instance.vessel
    from stowage.vessel.fleet import _parameter_bytes
    names=list_fleet()
    identities=tuple(canonical_vessel_id(value) for value in
                     (getattr(vessel,'slice_of',None),vessel.id) if value is not None)
    preferred=[name for name in identities if name in names]
    # Resolve only the declared basename; retain imported provenance verbatim.
    source_names={canonical_vessel_id(part.rsplit('/',1)[-1])
                  for source in vessel.sources for part in source.split('.toml')[:-1]
                  if '/' in part}
    ordered=list(dict.fromkeys([*preferred,*names]))
    for name in ordered:
        # Read bytes on EVERY resolution; size/mtime are never authoritative.
        raw=_parameter_bytes(name)
        params=_prepared_linear_params(raw) if shared else _parsed_linear_params(raw)
        if params['id'] in identities or name in source_names:
            # Neither public callers nor another instance may mutate the
            # memoised parse tree through nested sections/provenance lists.
            if shared:
                return name,params
            from .cache import _canonical
            return name,json.loads(_canonical(params))
    raise ValueError('linear model requires declared HullGeometry fleet inputs')


_DEFAULT_PARAMS_FOR = _params_for


def _prepared_params_for(instance):
    """Internal immutable resolver, preserving explicitly replaced resolvers."""
    if _params_for is not _DEFAULT_PARAMS_FOR:
        return _params_for(instance)
    return _resolve_params_for(instance, shared=True)


def _geometry_from_params(params):
    """Reuse vessel section geometry independently of cargo/ballast instances."""
    from stowage.linear.cache import vessel_cache_key, load_payload, store_payload
    key=vessel_cache_key(params['id'],params,inputs={'nx':48,'nz':64,'kind':'HullGeometry'})
    # The key still validates current raw bytes and full source/parameter
    # identity before consulting this read-only numerical geometry cache.
    memory=getattr(_geometry_from_params,'_memory',None)
    if memory is None:
        memory={};_geometry_from_params._memory=memory
    if key in memory:
        return memory[key]
    def remember(hull):
        for value in (hull.x_m,hull.integration_weights_m,
                      *(polygon for section in hull.sections for polygon in section)):
            value.flags.writeable=False
        if len(memory)>=8:memory.pop(next(iter(memory)))
        memory[key]=hull
        return hull
    payload=load_payload(key,namespace='geometry')
    fields=('lpp_m','beam_m','depth_m','design_draught_m','density_kg_m3',
            'openings','max_z_m','x_m','integration_weights_m','sections')
    if params['hull_form']['family']=='stowlm_offsets_v1':
        # The KCS loft retains buoyancy outside AP/FP and its verified source
        # identity. Legacy payloads missing any of these fields are cache misses.
        fields+=('buoyant_x_min_m','buoyant_x_max_m','source_sha256')
    if isinstance(payload,dict) and set(payload)==set(fields):
        try:
            hull=HullGeometry.__new__(HullGeometry)
            for field in fields: setattr(hull,field,payload[field])
            if 'source_sha256' in fields:
                if hull.source_sha256!=params['hull_form']['offsets_sha256']:
                    raise ValueError('cached offset source identity differs from pinned geometry')
                if not np.isfinite([hull.buoyant_x_min_m,hull.buoyant_x_max_m]).all() or hull.buoyant_x_min_m>=hull.buoyant_x_max_m:
                    raise ValueError('invalid cached buoyant extents')
            if (hull.x_m.ndim!=1 or len(hull.x_m)!=len(hull.sections) or
                hull.integration_weights_m.shape!=hull.x_m.shape or
                not np.isfinite(hull.x_m).all() or not np.all(hull.integration_weights_m>0)):
                raise ValueError('invalid cached section grid')
            if any(np.asarray(p).ndim!=2 or np.asarray(p).shape[1]!=2 or
                   not np.isfinite(p).all() for polygons in hull.sections for p in polygons):
                raise ValueError('invalid cached section polygon')
            # Task 2.1 fingerprints the restored sections for its equilibrium
            # memo. Rebuild derived state rather than serialising memo entries.
            if hasattr(hull, '_finish_geometry'):
                hull._finish_geometry()
            return remember(hull)
        except (TypeError,ValueError,AttributeError): pass
    hull=HullGeometry.from_params(params)
    store_payload(key,{field:getattr(hull,field) for field in fields},namespace='geometry')
    return remember(hull)


def geometry_for(instance):
    """Recover full input geometry without inventing offsets for foreign hulls."""
    _, params = _prepared_params_for(instance)
    return _geometry_from_params(params)


def _integration_matrices(geometry, stations, params):
    """Cache exact Gauss-panel integration independently of loading/band."""
    from stowage.linear.cache import vessel_cache_key, load_payload, store_payload
    key=vessel_cache_key(params['id'],params,inputs={
        'kind':'Gauss-panel force/moment integration','station_x_m':stations,
        'x_m':geometry.x_m,'weights_m':geometry.integration_weights_m})
    cached=load_payload(key,namespace='integration')
    shape=(len(stations),len(geometry.x_m))
    if isinstance(cached,dict) and all(isinstance(cached.get(k),np.ndarray) and
        cached[k].shape==shape and np.isfinite(cached[k]).all() for k in ('sf','bm')):
        return cached['sf'],cached['bm']
    x=geometry.x_m; w=geometry.integration_weights_m
    sf=np.zeros(shape); bm=np.zeros(shape)
    for j in range(0,len(x),2):
        left=(x[j]+x[j+1])/2-w[j]
        right=left+2*w[j]
        d=np.clip(stations-left,0.,right-left)
        gap=x[j+1]-x[j]
        for k,c0,c1 in ((j,(x[j+1]-left)/gap,-1/gap),
                         (j+1,(left-x[j])/gap,1/gap)):
            force=c0*d+c1*d*d/2
            first=c0*d*d/2+c1*d**3/3
            sf[:,k]=force/w[k]
            bm[:,k]=((stations-left)*force-first)/w[k]
    store_payload(key,{'sf':sf,'bm':bm},namespace='integration')
    return sf,bm


def _tank_point(tank, mass):
    volume = mass / tank.content_density_kg_m3
    grid = [p.volume_m3 for p in tank.table]
    return np.array([np.interp(volume, grid, [getattr(p, arm) for p in tank.table])
                     for arm in ('lcg_m', 'tcg_m', 'vcg_m')])


def _maximum_tank_fsms(instance, params, tanks):
    """Cache the continuous slack-fill envelope independently of the manifest."""
    from stowage.linear.cache import vessel_cache_key, load_payload, store_payload
    from stowage.linear.tank_fsm import max_slack_fsm_m4
    from stowage.vessel.tanks import TankGeometry
    ids=tuple(tank.id for tank in tanks)
    key=vessel_cache_key(instance.vessel.id,params,
        inputs={'kind':'continuous upright maximum slack FSM','tank_ids':ids,
                'content_density_kg_m3':tuple(tank.content_density_kg_m3 for tank in tanks)})
    cached=load_payload(key,namespace='vessel')
    if (isinstance(cached,np.ndarray) and cached.shape==(len(tanks),)
            and np.isfinite(cached).all() and np.all(cached>=0)):
        return cached
    raw={tank['id']:tank for tank in params.get('tanks',[])}
    values=np.array([max_slack_fsm_m4(TankGeometry.from_params(
        raw[tank.id],ship_params=params))*tank.content_density_kg_m3 for tank in tanks])
    store_payload(key,values,namespace='vessel')
    return values


def _fixed(instance):
    h = instance.vessel.hull
    loads = [(h.lightship_mass_kg, h.lightship_lcg_m, h.lightship_tcg_m, h.lightship_vcg_m)]
    # Closed hatch covers are already part of lightship.
    loads += [(mass,x,y,z) for x,y,z,mass in instance.vessel.fixed_loads]
    for tank in instance.vessel.tanks:
        if not tank.adjustable:
            mass = instance.onboard.tank_masses_kg.get(tank.id, 0.)
            loads.append((mass, *_tank_point(tank, mass)))
    return np.asarray(loads, dtype=float)


def _weight_integrals(model, instance, cell_mass, tank_mass):
    """Upright cumulative weight and first moment, with declared lightship blocks."""
    from stowage.linear.tank_weights import unit_tank_weight_integrals
    from stowage.vessel.tanks import TankGeometry
    x = model.station_x_m
    sf = np.zeros(len(x)); bm = np.zeros(len(x))
    h = instance.vessel.hull
    for lo, hi, mass in h.lightship_distribution:
        length = np.clip(x-lo, 0., hi-lo)
        sf += mass * length / (hi-lo)
        bm += mass/(hi-lo) * (length*(x-lo)-length**2/2)
    loads = [(mass,x,y,z) for x,y,z,mass in instance.vessel.fixed_loads]
    loads += [(m, xx, 0, 0) for m, xx in zip(cell_mass, model.cell_lcg_m)]
    for i,tank in enumerate(sorted(instance.vessel.tanks,key=lambda t:t.id)):
        mass = tank_mass[i] if tank.adjustable else instance.onboard.tank_masses_kg.get(tank.id,0.)
        if mass:
            params=getattr(model,'_reference_params',None)
            if params is None: _,params=_params_for(instance)
            raw=next(record for record in params['tanks'] if record['id']==tank.id)
            solid=TankGeometry.from_params(raw,ship_params=params)
            weight_sf,weight_bm=unit_tank_weight_integrals(
                solid,mass/tank.content_density_kg_m3,x)
            sf+=mass*weight_sf; bm+=mass*weight_bm
    for mass,xx,_,_ in loads:
        sf += mass*(x>=xx)
        bm += mass*np.maximum(x-xx,0.)
    return sf,bm


def exact_condition(model, instance, cell_masses, tank_masses, *, geometry=None, cell_vcg_m=None):
    """Reference the 3.1 liquid/GM and 3.2 distributed-strength formulations.

    Random physical-cell masses become fixed point loads at their actual centres;
    this preserves the exact loading moments without inventing box identifiers.
    The accelerated polygon clipping is the same numerical hull as task 2.1.
    Stability follows the selected public 3.1 policy: method1 (production
    default) uses finite liquid transfer; method2 retains the Code's virtual-rise
    compatibility path. Strength always follows 3.2's physical finite-liquid equilibrium,
    which can differ from the regulatory stability attitude.
    """
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.checker.stability_loading import loading_condition
    from stowage.checker.strength import (
        integrate_strength, _tank_liquid, _tank_segments, _section_tank_point_loads,
    )
    from stowage.model import ShipState
    from stowage.vessel.tanks import TankGeometry
    import math
    import os

    try:
        method=int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1'))
    except ValueError as exc:
        raise ValueError('free-surface method must be 1 or 2') from exc
    if method not in (1,2):
        raise ValueError('free-surface method must be 1 or 2')

    params = getattr(model, '_reference_params', None)
    fingerprint = getattr(model, '_geometry_fingerprint', None)
    if params is None:
        _, params = _params_for(instance)
        fingerprint = hashlib.sha256(repr(params).encode()).hexdigest()
        object.__setattr__(model, '_reference_params', params)
        object.__setattr__(model, '_geometry_fingerprint', fingerprint)
    context_key = ('exact_geometry', fingerprint)
    fast = _EXACT_CONTEXT.get(context_key)
    if fast is None:
        fast = StabilityGeometry(params)
        _EXACT_CONTEXT[context_key] = fast
    heights = model.cell_vcg_m if cell_vcg_m is None else np.asarray(cell_vcg_m)
    loads = list(instance.vessel.fixed_loads)
    loads.extend((float(x), float(y), float(z), float(m)) for x,y,z,m in
                 zip(model.cell_lcg_m, model.cell_tcg_m, heights, cell_masses) if m)
    vessel = instance.vessel.model_copy(update={'fixed_loads': loads})
    reference = instance.model_copy(update={'vessel': vessel})
    tank_values = {t.id: float(tank_masses[i] if t.adjustable else
                   instance.onboard.tank_masses_kg.get(t.id, 0.))
                   for i,t in enumerate(sorted(instance.vessel.tanks, key=lambda t: t.id))}
    ship = ShipState(vessel_id=vessel.id, tank_masses_kg=tank_values)
    condition = loading_condition(reference, ship, geometry=fast, ship_params=params,
                                  free_surface_method=method, precomputed_soundings=True,
                                  small_tank_exemption=True)
    if method == 1 and os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT') != '1':
        from stowage.checker.stability_liquid import attach_tables
        condition = attach_tables(condition, params,
            vessel_hash=getattr(fast, '_stability_vessel_hash', None))
    if condition.errors:
        raise ValueError('; '.join(condition.errors))
    rho = model.water_density_kg_m3
    from stowage.linear.reference_equilibrium import liquid_equilibrium
    if method==2:
        eq=fast.equilibrium(condition.mass_kg,condition.lcg_m,condition.tcg_m,
                            condition.kg_m+condition.fsc_m,rho)
        upright=fast._solve(condition.mass_kg,condition.lcg_m,0.,
                            condition.kg_m+condition.fsc_m,rho,0.)
        # The strength judge retains physical liquid transfer under both
        # stability policies. Do not use a regulatory virtual KG for its loads.
        physical=loading_condition(reference,ship,geometry=fast,ship_params=params,
                                   free_surface_method=1)
        if physical.errors:
            raise ValueError('; '.join(physical.errors))
        strength_eq=liquid_equilibrium(fast,physical,rho)
    else:
        eq=liquid_equilibrium(fast,condition,rho)
        upright=liquid_equilibrium(fast,condition,rho,upright=True,initial=eq)
        # Strength integrates physical liquid geometry directly; stability may
        # use independently qualified transfer tables. Keep these references
        # separate even when both use method .1.
        if condition.liquid_tables:
            physical=loading_condition(reference,ship,geometry=fast,ship_params=params,
                                       free_surface_method=1)
            strength_eq=liquid_equilibrium(fast,physical,rho)
        else:
            strength_eq=eq
    submerged=fast.full_submerged(strength_eq.t_aft_m,strength_eq.t_fwd_m,
                                  strength_eq.heel_rad,rho)
    u = fast.full_submerged(upright.t_aft_m, upright.t_fwd_m, 0., rho)
    normal, _, _ = fast._axes(upright.t_aft_m, upright.t_fwd_m, 0.)
    gm = float(np.dot(np.asarray(u.centroid_m)-
               [condition.lcg_m, 0., condition.kg_m], normal))+u.bm_t_m-condition.fsc_m
    points = [(x,m) for x,y,z,m in loads]
    segments = []
    raw_tanks = {t['id']: t for t in params.get('tanks', [])}
    for tank in instance.vessel.tanks:
        mass = tank_values[tank.id]
        if mass:
            solid = TankGeometry.from_params(raw_tanks[tank.id],ship_params=params)
            trim = math.atan(strength_eq.trim_m/fast.lpp_m)
            volume = min(solid.capacity_m3, mass/tank.content_density_kg_m3)
            liquid = _tank_liquid(solid, volume, strength_eq.heel_rad, trim)
            if solid.is_rectangular:
                segments.extend(_tank_segments(solid, liquid, strength_eq.heel_rad, trim, tank.content_density_kg_m3))
            else:
                # The section quadrature is authoritative. In particular, do
                # not reconstruct continuous panels across excluded regions.
                points.extend(_section_tank_point_loads(
                    solid, volume, strength_eq.heel_rad, trim, tank.content_density_kg_m3))
    curves = integrate_strength(submerged.x_m, submerged.integration_weights_m,
                submerged.sectional_areas_m2*rho*G, vessel.hull.lightship_distribution,
                points, model.station_x_m, lpp_m=fast.lpp_m, distributed_segments=segments)
    return dict(displacement_kg=float(condition.mass_kg), gm_m=float(gm), kg_m=float(condition.kg_m),
                fsc_m=float(condition.fsc_m), km_m=float(gm+condition.kg_m+condition.fsc_m), trim_m=eq.trim_m,
                list_rad=eq.heel_rad, draught_m=eq.mean_draught_m,
                sf_n=curves.shear_n, bm_nm=curves.bending_nm,
                liquid_table_queries=int(condition.liquid_usage.get('table',0)),
                liquid_direct_queries=int(condition.liquid_usage.get('direct',0)))


def _linear_arrays(model, cell, tank, *, instance=None, solver=False):
    """Point estimate uses known slack FSM; constraints reserve every tank max.

    The three-argument compatibility form retains the all-tank reserve. Error
    measurements always supply the instance, separating a deliberately cautious
    constraint reserve from errors in the reported physical estimate.
    """
    d=model.const_mass_kg+sum(cell)+sum(tank)
    mx=model.const_moment_x_kgm+model.cell_lcg_m@cell+model.tank_lcg_m@tank
    my=model.const_moment_y_kgm+model.cell_tcg_m@cell+model.tank_tcg_m@tank
    vcg=getattr(model,'_constraint_cell_vcg_m',None) if solver else None
    if vcg is None: vcg=model.cell_vcg_m
    mz=model.const_moment_z_kgm+vcg@cell+model.tank_vcg_m@tank
    a,b=model.km_line
    if instance is None or solver:
        fsm=sum(model.tank_fsm_max_kgm)
    else:
        from stowage.linear.operations import actual_fsm_kgm
        fsm=actual_fsm_kgm(model,instance,tank)
    gm=(a+b*d-mz-fsm)/d
    return dict(gm_m=gm,trim_m=model.trim_line[0]+model.trim_line[1]*mx+model.trim_line[2]*d,
                list_rad=np.arctan2(my,d*gm),draught_m=model.draught_line[0]+model.draught_line[1]*d,
                sf_n=model.const_sf_n+model.sf_per_kg_cell@cell+model.sf_per_kg_tank@tank,
                bm_nm=model.const_bm_nm+model.bm_per_kg_cell@cell+model.bm_per_kg_tank@tank)


def measure_errors(model, instance, *, samples=64, seed=3701):
    """Deterministic independent random mass sweep; return maxima and 99th percentile.

    Sampling spans target displacements, independent random tank fills, sparse and
    dense random cell masses. Only converged conditions inside ALL band bounds
    count. Rejection count is recorded; absence of coverage is an explicit error.
    """
    from stowage.linear.bands import departure_manifest
    from stowage.linear.operations import actual_fsm_kgm
    params=getattr(model,'_reference_params',None)
    geometry=_geometry_from_params(params) if params is not None else geometry_for(instance)
    rng=np.random.default_rng(seed)
    residuals={k:[] for k in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')}
    signed={k:[] for k in (*residuals,'draught_aft_m','draught_forward_m')}
    affine_gms=[];exact_heels=[]
    coverage={k:[] for k in ('displacement_kg','trim_m','list_rad','draught_m')}
    gm_components={k:[] for k in ('km_support_m','height_arms_m','tank_arms_m','fsm_approx_m')}
    fsm_conservatism=[]
    liquid_usage={'table':0,'direct':0}
    unsafe_samples=0
    attempts=0; band=model.band; last_failure="no condition reached equilibrium"
    while len(residuals['gm_m']) < samples and attempts < max(200,samples*50):
        attempts+=1
        from stowage.linear.bands import sample_manifest_loading, project_ballast_for_sampling
        bounds=getattr(model,'_tank_mass_bounds_kg',{})
        tank=np.zeros(len(model.tank_ids))
        for i,tid in enumerate(model.tank_ids):
            if not model.tank_adjustable[i]: continue
            lower,upper=bounds.get(tid,(0.,model.tank_capacity_kg[i]))
            tank[i]=lower+(upper-lower)*rng.random()
            if attempts%8==0: tank[i]=lower
            elif attempts%9==0: tank[i]=upper
        cargo_mass=getattr(model,'_cargo_mass_kg',None)
        if cargo_mass is None: cargo_mass=sum(b.gross_mass_kg for b in departure_manifest(instance))
        target=model.const_mass_kg+cargo_mass+sum(tank)
        if not band.displacement_min_kg<=target<=band.displacement_max_kg: continue
        loading=sample_manifest_loading(model,instance,tank,rng)
        if loading is None: continue
        cell, true_heights=loading
        projected=project_ballast_for_sampling(model,instance,cell,true_heights,tank)
        if projected is None:
            if not (model.band.max_abs_trim_m>model.max_trim_m+1e-12 or model.band.max_abs_heel_rad>model.max_list_rad+1e-12):
                last_failure='no hydrostatic LP projection for the fixed cargo and tank windows'
                continue
            # Explicit expanded-band experiments may measure unsafe departures.
            # Symmetric fractions retain random tank variation without creating
            # an unrelated port/starboard imbalance in a saturated cargo plan.
            fractions={}
            for i,tid in enumerate(model.tank_ids):
                if not model.tank_adjustable[i]: continue
                group=(round(float(model.tank_lcg_m[i]),6),round(abs(float(model.tank_tcg_m[i])),6))
                fraction=fractions.setdefault(group,float(rng.uniform(.05,.95)))
                if attempts%8==0: fraction=0.
                elif attempts%9==0: fraction=1.
                lower,upper=bounds.get(tid,(0.,model.tank_capacity_kg[i]))
                tank[i]=lower+fraction*(upper-lower)
        else: tank=projected
        preliminary=_linear_arrays(model,cell,tank,instance=instance)
        if abs(preliminary['trim_m']) > band.max_abs_trim_m*1.3: continue
        try: exact=exact_condition(model,instance,cell,tank,geometry=geometry,
                                   cell_vcg_m=true_heights)
        except ValueError as exc:
            last_failure=str(exc)
            continue
        if not (band.displacement_min_kg <= exact['displacement_kg'] <= band.displacement_max_kg and
                band.draught_min_m <= exact['draught_m'] <= band.draught_max_m and
                abs(exact['trim_m']) <= band.max_abs_trim_m and abs(exact['list_rad']) <= band.max_abs_heel_rad): continue
        approx=_linear_arrays(model,cell,tank,instance=instance)
        affine=_linear_arrays(model,cell,tank,instance=instance,solver=True)
        affine_gms.append(affine['gm_m']);exact_heels.append(exact['list_rad'])
        for key in residuals:
            signed[key].append(np.asarray(affine[key])-np.asarray(exact[key]))
        for sign,key in ((1.,'draught_aft_m'),(-1.,'draught_forward_m')):
            signed[key].append(affine['draught_m']+sign*.5*affine['trim_m']
                               -exact['draught_m']-sign*.5*exact['trim_m'])
        for name in liquid_usage:
            liquid_usage[name]+=exact.get(f'liquid_{name}_queries',0)
        unsafe_samples+=int(exact['gm_m']<model.gm_min_m or abs(exact['trim_m'])>model.max_trim_m or abs(exact['list_rad'])>model.max_list_rad)
        displacement=exact['displacement_kg']
        estimated_kg=(model.const_moment_z_kgm+model.cell_vcg_m@cell+model.tank_vcg_m@tank)/displacement
        height_delta=float((true_heights-model.cell_vcg_m)@cell/displacement)
        components=dict(km_support_m=(model.km_line[0]+model.km_line[1]*displacement)/displacement-exact['km_m'],
            height_arms_m=height_delta, tank_arms_m=exact['kg_m']-estimated_kg-height_delta,
            fsm_approx_m=exact['fsc_m']-actual_fsm_kgm(model,instance,tank)/displacement)
        fsm_conservatism.append(float((sum(model.tank_fsm_max_kgm)-actual_fsm_kgm(model,instance,tank))/displacement))
        for key,value in components.items(): gm_components[key].append(float(value))
        for key in coverage: coverage[key].append(exact[key])
        for key in residuals: residuals[key].append(np.abs(approx[key]-exact[key]))
    if len(residuals['gm_m']) < samples:
        raise ValueError(f'validity-band sweep accepted only {len(residuals["gm_m"])} of {samples} required conditions in {attempts} attempts: {last_failure}')
    maxima={k:np.max(v,axis=0) for k,v in residuals.items()}
    p99={k:np.percentile(v,99,axis=0) for k,v in residuals.items()}
    errors=LinearErrors(maxima['gm_m'],maxima['trim_m'],maxima['list_rad'],maxima['draught_m'],maxima['sf_n'],maxima['bm_nm'])
    from stowage.linear.signed_errors import solver_signed_statistics
    diagnostics={'samples':len(residuals['gm_m']),'attempts':attempts,'seed':seed,
                 'signed_errors':solver_signed_statistics(signed,exact_list_rad=exact_heels,
                     affine_gm_m=affine_gms,gm_min_m=model.gm_min_m),
                 'signed_residual_definition':'affine solver (maximum slack FSM) minus exact; positive is unsafe for lower bounds',
                 'liquid_usage':liquid_usage,
                 'unsafe_samples':unsafe_samples,'expanded_unsafe_sampling':bool(model.band.max_abs_trim_m>model.max_trim_m+1e-12 or model.band.max_abs_heel_rad>model.max_list_rad+1e-12),
                 'max':{k:np.asarray(v).tolist() for k,v in maxima.items()},
                 'p99':{k:np.asarray(v).tolist() for k,v in p99.items()},
                 'coverage':{k:{'min':float(min(v)),'max':float(max(v))} for k,v in coverage.items()},
                 'gm_components':{k:{'min':min(v),'max':max(v),'max_abs':max(abs(x) for x in v)}
                                  for k,v in gm_components.items()},
                 'constraint_conservatism':{'fsm_reserve_m':{'min':min(fsm_conservatism),'max':max(fsm_conservatism)}}}
    return errors,diagnostics


def _polygon_upright_polynomials(polygon):
    """Exact local V, Mz, waterplane I/A/Q polynomials for one section polygon.

    A signed crossing of an upward polygon edge is a right-hand waterline end;
    a downward crossing is a left-hand end. Integrating those affine endpoints
    gives breadth, transverse first moment and second moment without clipping at
    every candidate draught. Coordinates are local to each vertical interval.
    """
    p=np.asarray(polygon,dtype=np.longdouble)
    q=np.roll(p,-1,axis=0)
    if np.sum(p[:,0]*q[:,1]-q[:,0]*p[:,1])<=0:
        raise ValueError('upright support requires counter-clockwise section polygons')
    edges=q[:,1]!=p[:,1]
    p=p[edges]; q=q[edges]
    knots=np.unique(np.r_[p[:,1],q[:,1]])
    left=knots[:-1]; right=knots[1:]; dz=right-left
    mid=(left+right)/2
    # Adjacent vertex heights can be one floating-point ULP apart: their
    # midpoint may round to the right endpoint. Half-open interval membership
    # uses the exactly represented left endpoint instead.
    active=(left[:,None]>=np.minimum(p[:,1],q[:,1]))&(left[:,None]<np.maximum(p[:,1],q[:,1]))
    slope=(q[:,0]-p[:,0])/(q[:,1]-p[:,1])
    y=np.where(active,p[:,0]+(left[:,None]-p[:,1])*slope,0.)
    m=np.where(active,slope,0.)
    sign=np.where(active,np.sign(q[:,1]-p[:,1]),0.)
    def total(value): return np.sum(sign*value,axis=1,dtype=np.longdouble)
    width=np.column_stack((total(y),total(m)))
    first=np.column_stack((total(y*y)/2,total(y*m),total(m*m)/2))
    inertia=np.column_stack((total(y**3)/3,total(y*y*m),total(y*m*m),total(m**3)/3))
    # Polynomial area and vertical first moment include everything below each
    # local interval, rather than resetting the submerged body at band minimum.
    area_step=width[:,0]*dz+width[:,1]*dz*dz/2
    mz_step=left*width[:,0]*dz+(left*width[:,1]+width[:,0])*dz*dz/2+width[:,1]*dz**3/3
    area_base=np.r_[np.longdouble(0.),np.cumsum(area_step,dtype=np.longdouble)[:-1]]
    mz_base=np.r_[np.longdouble(0.),np.cumsum(mz_step,dtype=np.longdouble)[:-1]]
    volume=np.column_stack((area_base,width[:,0],width[:,1]/2,np.zeros(len(dz))))
    moment=np.column_stack((mz_base,left*width[:,0],(left*width[:,1]+width[:,0])/2,width[:,1]/3))
    return knots,volume,moment,inertia,width,first,np.sum(area_step),np.sum(mz_step)


def _km_support_lower_intercept(geometry, lo, hi, density, slope):
    """Continuous minimum of KM*mass-slope*mass, without per-knot hull solves.

    Upright polygon breadth is affine between vertex heights. Volume is
    quadratic; vertical volume moment and uncentred transverse inertia are
    cubic. Their longitudinal Gauss-weighted sums are translated to local
    interval coordinates, normalised to [0,1], and minimised analytically.
    The fast quadratic-root branch verifies zero waterplane first moment.
    Asymmetric waterplanes retain Q²/A and use its exact quartic stationary
    equation, rather than incorrectly treating central inertia as cubic.
    """
    knots=np.unique(np.r_[lo,hi,*[p[:,1] for section in geometry.sections for p in section]])
    knots=knots[(knots>=lo)&(knots<=hi)].astype(np.longdouble)
    left=knots[:-1]; width=knots[1:]-left; mid=(knots[:-1]+knots[1:])/2
    count=len(left)
    volume=np.zeros((count,4),dtype=np.longdouble)
    moment=np.zeros_like(volume); inertia=np.zeros_like(volume)
    area=np.zeros((count,2),dtype=np.longdouble); first=np.zeros((count,3),dtype=np.longdouble)
    absolute_scale=np.zeros(count,dtype=np.longdouble)
    def translate(coeff,delta):
        result=np.zeros((len(delta),coeff.shape[1]),dtype=np.longdouble)
        # Coefficients are relative to each polygon's immediately preceding
        # vertex, so near-horizontal edges never create large global powers.
        from math import comb
        for power in range(coeff.shape[1]):
            for target in range(power+1):
                result[:,target]+=coeff[:,power]*comb(power,target)*delta**(power-target)
        return result
    for section,weight in zip(geometry.sections,geometry.integration_weights_m,strict=True):
        for polygon in section:
            z,v,m,i,a,q,full_v,full_m=_polygon_upright_polynomials(polygon)
            indices=np.searchsorted(z,left,side='right')-1
            wet=(indices>=0)&(indices<len(z)-1)
            above=indices>=len(z)-1
            volume[above,0]+=weight*full_v
            moment[above,0]+=weight*full_m
            absolute_scale[above]+=abs(weight)*(abs(full_m)+abs(slope*full_v))
            if not wet.any(): continue
            delta=left[wet]-z[indices[wet]]
            vs=translate(v[indices[wet]],delta)
            ms=translate(m[indices[wet]],delta)
            ins=translate(i[indices[wet]],delta)
            ars=translate(a[indices[wet]],delta)
            qs=translate(q[indices[wet]],delta)
            volume[wet]+=weight*vs; moment[wet]+=weight*ms; inertia[wet]+=weight*ins
            area[wet]+=weight*ars; first[wet]+=weight*qs
            scale=np.power(width[wet,None],np.arange(4))
            absolute_scale[wet]+=abs(weight)*np.sum((abs(ms)+abs(ins)+abs(slope*vs))*scale,axis=1)
    normalisation=width[:,None]**np.arange(4)
    support=(moment+inertia-slope*volume)*normalisation
    area*=width[:,None]**np.arange(2)
    first*=width[:,None]**np.arange(3)
    first_bound=np.sum(abs(first),axis=1)
    # Reflection symmetry is checked from integrated coefficients, not assumed
    # from a vessel name. Q is a transverse waterplane first area moment.
    symmetric=bool(np.all(first_bound<=np.longdouble(1e-12)*np.maximum(1.,np.sum(abs(area),axis=1)*geometry.beam_m)))
    candidates=[support[:,0],np.sum(support,axis=1)]
    if symmetric:
        qa=3*support[:,3]; qb=2*support[:,2]; qc=support[:,1]
        scale=np.maximum(1.,np.maximum(abs(qa),np.maximum(abs(qb),abs(qc))))
        quadratic=abs(qa)>np.finfo(np.longdouble).eps*scale*16
        discriminant=qb*qb-4*qa*qc
        real=quadratic&(discriminant>=0)
        root_disc=np.sqrt(np.maximum(discriminant,0.))
        stable_q=-.5*(qb+np.where(qb>=0,root_disc,-root_disc))
        for root in (np.divide(stable_q,qa,out=np.full(count,np.nan,dtype=np.longdouble),where=real),
                     np.divide(qc,stable_q,out=np.full(count,np.nan,dtype=np.longdouble),where=real&(stable_q!=0)),
                     np.divide(-qc,qb,out=np.full(count,np.nan,dtype=np.longdouble),where=(~quadratic)&(qb!=0))):
            valid=np.isfinite(root)&(root>0)&(root<1)
            value=np.full(count,np.inf,dtype=np.longdouble)
            r=root[valid]; c=support[valid]
            value[valid]=c[:,0]+r*(c[:,1]+r*(c[:,2]+r*c[:,3]))
            candidates.append(value)
        minimum=min(np.min(values) for values in candidates)
        # Retain the tiny central-inertia correction even below the symmetry
        # tolerance; its interval upper bound makes this a lower support line.
        area_min=np.minimum(area[:,0],area.sum(axis=1))
        correction=np.max(np.divide(first_bound**2,area_min,out=np.zeros(count,dtype=np.longdouble),where=area_min>0))
        minimum-=correction
    else:
        from numpy.polynomial.polynomial import polyadd,polymul,polyder,polyroots,polyval
        minimum=np.longdouble(np.inf)
        for p,a,q in zip(support,area,first,strict=True):
            # d(P-Q²/A)*A² = P' A² -2 Q Q' A + Q² A'.
            derivative=polyadd(polymul(polyder(p),polymul(a,a)),
                polyadd(-2*polymul(polymul(q,polyder(q)),a),polymul(polymul(q,q),polyder(a))))
            roots=polyroots(np.asarray(derivative,dtype=float))
            points=[0.,1.,*[r.real for r in roots if abs(r.imag)<1e-9 and 0<r.real<1]]
            for point in points:
                aw=polyval(point,a)
                if aw>0: minimum=min(minimum,polyval(point,p)-polyval(point,q)**2/aw)
    # A horizontal roof can lose waterplane inertia exactly at an endpoint.
    # Evaluate both actual endpoint conventions, not only polynomial one-sided
    # limits; two hull integrations replace thousands of per-knot calls.
    for draught in (lo,hi):
        wet=geometry.submerged(float(draught),float(draught),0.,density)
        exact=wet.volume_m3*wet.vcb_m+wet.transverse_inertia_m4-slope*wet.volume_m3
        minimum=min(minimum,np.longdouble(exact))
    # Bound floating-point summation/translation error relative to the terms
    # actually added, rather than just the potentially cancelling final value.
    guard=max(np.longdouble(1e-4),density*np.max(absolute_scale)*np.longdouble(2e-12))
    return float(density*minimum-guard)


def build_model(instance, band=None, *, _samples=64, _force_rebuild=False):
    """Reuse a calibrated geometry family, then add this booking's constants.

    Calibration never depends on fixed-load records, booking identities or cargo
    mass. An explicit instance band restricts the shared coefficient domain;
    it does not silently refit slopes using an unmeasured loading condition.
    """
    from .cache import model_cache_key
    from .reusable import coefficient_family, assemble_instance
    from .family_calibration import (calibrated_family, reference_inputs_cached,
                                     reference_cache_identity, synchronize_reference)
    from .margin_policy import policy_inputs
    from .bands import band_for_instance
    from .operations import public_loading_policy
    loading_policy=public_loading_policy(instance)
    for rule, overrides in instance.rule_overrides.items():
        if (overrides and rule.startswith(('iscode.', 'iacs.', 'port.call.draft'))
                and not (loading_policy is not None and rule in
                    ('iscode.loading.trim','iscode.loading.displacement'))):
            raise ValueError(f'{rule}: linear safety overrides must be supplied as vessel/port limits')
    if instance.vessel.hull is None:
        raise ValueError('linear model requires a vessel hull')
    if instance.onboard.covers_off:
        raise ValueError('linear model requires closed hatch covers at departure')
    _, parameters = _prepared_params_for(instance)
    unsupported=[tank['id'] for tank in parameters.get('tanks',[])
                 if tank.get('geometry',tank).get('shape','rectangular_prism')
                 not in ('rectangular_prism','hull_sections','hull_intersection')]
    if unsupported:
        raise ValueError('linear model requires supported exact tank geometry: '+', '.join(sorted(unsupported)))
    reference = reference_inputs_cached(parameters)
    synchronize_reference(reference)
    key=(model_cache_key(instance,band,_samples,parameters),
         hashlib.sha256(repr(reference_cache_identity(reference)).encode()).hexdigest(),
         hashlib.sha256(repr(policy_inputs()).encode()).hexdigest())
    if not _force_rebuild and key in _CACHE:
        from stowage.versions import LINEAR_MODEL_VERSION as current_model_version
        model = _CACHE[key]
        binding_updates = {}
        vessel_id = getattr(instance.vessel, 'id', None)
        if vessel_id is not None and getattr(model, 'vessel_id', None) != vessel_id:
            binding_updates['vessel_id'] = vessel_id
        if model.version != current_model_version:
            binding_updates['version'] = current_model_version
        if getattr(model, '_manifest_fingerprint',None) is not None:
            from .operations import departure_manifest_identity
            manifest_ids, manifest_fingerprint = departure_manifest_identity(instance)
            if manifest_fingerprint != model._manifest_fingerprint:
                binding_updates.update(_manifest_ids=manifest_ids,
                                       _manifest_fingerprint=manifest_fingerprint)
        if getattr(model, '_fixed_loads_fingerprint', None) is not None:
            from .cache import fixed_loads_fingerprint
            fixed_fingerprint = fixed_loads_fingerprint(instance)
            if fixed_fingerprint != model._fixed_loads_fingerprint:
                binding_updates['_fixed_loads_fingerprint'] = fixed_fingerprint
        if binding_updates:
            # Provenance and exact-manifest/fixed-load bindings are refreshed without
            # invalidating physical coefficients/calibration (D30).
            from copy import copy
            model = copy(model)
            for name, value in binding_updates.items():
                object.__setattr__(model, name, value)
            _CACHE[key] = model
        return model
    if band is None:
        band=band_for_instance(instance)
    family=coefficient_family(instance,parameters,force_rebuild=_force_rebuild)
    errors,diagnostics=calibrated_family(family,instance,
        samples=192 if _samples==64 else _samples,force_rebuild=_force_rebuild)
    model=assemble_instance(family,instance,band,errors=errors,diagnostics=diagnostics)
    if (vessel_id := getattr(instance.vessel, 'id', None)) is not None:
        object.__setattr__(model, 'vessel_id', vessel_id)
    if loading_policy is not None:
        object.__setattr__(model,'measurement_diagnostics',dict(
            model.measurement_diagnostics,loading_policy=loading_policy))
    if len(_CACHE)>32:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key]=model
    return model
