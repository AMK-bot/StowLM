"""Reusable hull response and exact instance constants.

A fixed point load changes the weight curve exactly and the affine buoyancy
curve through two totals: mass and longitudinal moment. Keeping those two
response columns lets thousands of loading instances share a hull/slice/tank
linearisation. A coefficient family contains no booking manifest or background
fixed loads. Its independently calibrated domain must contain an instance's
narrow band; coefficients alone are never a calibrated model.

Source: Biran & López-Pulido (2014), ch.2–4 (weight and buoyancy moments).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib

import numpy as np
from scipy.optimize import brentq

from .model import LinearErrors, LinearModel, ValidityBand


@dataclass(frozen=True)
class CoefficientFamily:
    template: LinearModel
    response_sf: np.ndarray
    response_bm: np.ndarray
    buoyancy_sf: np.ndarray
    buoyancy_bm: np.ndarray
    lightship_sf: np.ndarray
    lightship_bm: np.ndarray
    parameters: dict
    cache_key: str


_ARRAYS=('response_sf','response_bm','buoyancy_sf','buoyancy_bm','lightship_sf','lightship_bm')
_FAMILY_MEMORY={}
_FIXED_TANK_MEMORY={}


def _fixed_tank_integrals(parameters, tank_id, volume_m3, stations):
    """Reuse liquid weight curves by actual solid, fill and station contents.

    Consumables are constants for one instance, but rebuilding their solids
    does not depend on its cargo or background loads. Fresh input/content keys
    permit reuse without assuming equal object ids or filesystem timestamps.
    """
    from .cache import cache_key,load_payload,store_payload
    from .tank_weights import unit_tank_weight_integrals
    from stowage.vessel.tanks import TankGeometry
    key=cache_key('fixed_tank_integrals',vessel_id=parameters.get('id','tank-solid'),
        params=parameters,inputs={
        'kind':'upright fixed liquid weight distribution',
        'tank_id':tank_id,'volume_m3':float(volume_m3),'stations_m':np.asarray(stations)})
    if key in _FIXED_TANK_MEMORY:
        return _FIXED_TANK_MEMORY[key]
    payload=load_payload(key,namespace='vessel')
    shape=np.asarray(stations).shape
    if not (isinstance(payload,dict) and all(isinstance(payload.get(name),np.ndarray)
            and payload[name].shape==shape and np.isfinite(payload[name]).all()
            for name in ('sf','bm'))):
        raw=next(record for record in parameters.get('tanks',[]) if record['id']==tank_id)
        solid=TankGeometry.from_params(raw,ship_params=parameters)
        sf,bm=unit_tank_weight_integrals(solid,volume_m3,stations)
        payload={'sf':np.asarray(sf),'bm':np.asarray(bm)}
        store_payload(key,payload,namespace='vessel')
    # bytes-backed arrays cannot have writeability re-enabled by a caller.
    result=tuple(np.frombuffer(np.asarray(payload[name],dtype=float).tobytes(),dtype=float)
                 .reshape(shape) for name in ('sf','bm'))
    if len(_FIXED_TANK_MEMORY)>=64:_FIXED_TANK_MEMORY.pop(next(iter(_FIXED_TANK_MEMORY)))
    _FIXED_TANK_MEMORY[key]=result
    return result


def family_band(instance, params=None):
    """Whole operating draught/trim/list domain, independent of bookings."""
    from .physics import geometry_for, _geometry_from_params
    hull=instance.vessel.hull
    if hull is None:
        raise ValueError('coefficient family requires vessel hull geometry')
    geometry=geometry_for(instance) if params is None else _geometry_from_params(params)
    high=min(hull.max_draught_m,geometry.max_z_m-.001)
    rho=instance.port.water_density_kg_m3
    # Background/cargo/tank loads are nonnegative; lightship is a universal
    # lower displacement bound. A 1kg guard covers point-window fit expansion.
    lower=max(1.,hull.lightship_mass_kg-1.)
    upper=geometry.submerged(high,high,0.,rho).displacement_kg
    if lower>=upper:
        raise ValueError('coefficient family has empty operating displacement interval')
    return ValidityBand(float(lower),float(upper),.001,geometry.max_z_m-.001,
                        hull.max_trim_m,hull.max_list_rad)


def coefficient_family(instance, parameters=None, band=None, *, force_rebuild=False):
    """Load/build immutable coefficients with no fixed loads or manifest key.

    Cold preparation integrates the actual hull and half-full tank geometry.
    Subsequent instances reuse its inert, checksum-verified numeric payload.
    ``force_rebuild`` bypasses the family cache for equivalence verification.
    """
    from .cache import coefficient_family_key,load_model,store_model
    from .physics import (_params_for,_geometry_from_params,_integration_matrices,
                          _km_support_lower_intercept,_tank_point,_maximum_tank_fsms,G)
    from .tank_weights import unit_tank_weight_integrals
    from stowage.vessel.tanks import TankGeometry
    from .dependencies import LINEAR_COEFFICIENTS_VERSION
    from stowage.rules.catalogue import get_rule
    if parameters is None:
        _,parameters=_params_for(instance)
    if band is None:
        band=family_band(instance,parameters)
    key=coefficient_family_key(instance,parameters,band)
    if not force_rebuild:
        if key in _FAMILY_MEMORY:
            return replace(_FAMILY_MEMORY[key],parameters=parameters)
        cached=load_model(key)
        if cached is not None and all(getattr(cached,'_family_'+name,None) is not None for name in _ARRAYS):
            arrays=[getattr(cached,'_family_'+name) for name in _ARRAYS]
            s=len(cached.station_x_m)
            if all(value.shape==((s,2) if name.startswith('response') else (s,))
                   for name,value in zip(_ARRAYS,arrays,strict=True)):
                family=CoefficientFamily(cached,*arrays,parameters,key)
                if len(_FAMILY_MEMORY)>=16:_FAMILY_MEMORY.pop(next(iter(_FAMILY_MEMORY)))
                _FAMILY_MEMORY[key]=family
                return family
    geometry=_geometry_from_params(parameters)
    hull=instance.vessel.hull
    if not np.allclose([geometry.lpp_m,geometry.beam_m,geometry.depth_m],
                       [hull.lpp_m,hull.beam_m,hull.depth_m],rtol=0.,atol=1e-8):
        raise ValueError('hull geometry inputs differ from declared vessel hull')
    rho=float(instance.port.water_density_kg_m3)
    cells=sorted(instance.vessel.cells,key=lambda cell:cell.id)
    tanks=sorted(instance.vessel.tanks,key=lambda tank:tank.id)
    stations=np.array([station.x_m for station in hull.stations])
    if not len(stations):
        raise ValueError('coefficient family requires permissible strength stations')
    def hydro(mass):
        draught=brentq(lambda t:geometry.submerged(t,t,0.,rho).displacement_kg-mass,
                      .001,geometry.max_z_m-.001)
        return geometry.hydrostatic_row(draught,rho)
    mid=(band.displacement_min_kg+band.displacement_max_kg)/2
    row=hydro(mid); t=row.draught_m
    endpoints=[hydro(mass) for mass in (band.displacement_min_kg,band.displacement_max_kg)]
    slope=(endpoints[1].kmt_m*endpoints[1].displacement_kg-
           endpoints[0].kmt_m*endpoints[0].displacement_kg)/(band.displacement_max_kg-band.displacement_min_kg)
    intercept=_km_support_lower_intercept(geometry,endpoints[0].draught_m,endpoints[1].draught_m,rho,slope)
    x=geometry.x_m; weights=geometry.integration_weights_m
    base=geometry.submerged(t,t,0.,rho).sectional_areas_m2*weights*rho
    epsilon=1e-4
    jacobian=np.column_stack([
        (geometry.submerged(t+da,t+df,0.,rho).sectional_areas_m2-
         geometry.submerged(t-da,t-df,0.,rho).sectional_areas_m2)*weights*rho/(2*epsilon)
        for da,df in ((epsilon,0.),(0.,epsilon))])
    targets=np.array([jacobian.sum(axis=0),x@jacobian])
    response=jacobian@np.linalg.inv(targets)
    buoyancy=base-response@np.array([mid,mid*row.lcb_m])
    integral_sf,integral_bm=_integration_matrices(geometry,stations,parameters)
    rsf=G*(integral_sf@response); rbm=G*(integral_bm@response)
    bsf=G*(integral_sf@buoyancy); bbm=G*(integral_bm@buoyancy)
    cell_x=np.array([cell.lcg_m for cell in cells]); cell_y=np.array([cell.tcg_m for cell in cells])
    nominal_z={cid:stack.base_z_m+(j+.5)*2.591
               for stack in instance.vessel.stacks for j,cid in enumerate(stack.cell_ids)}
    cell_z=np.array([nominal_z[cell.id] for cell in cells])
    cap=np.array([tank.capacity_m3*tank.content_density_kg_m3 for tank in tanks])
    arms=np.array([_tank_point(tank,mass/2) for tank,mass in zip(tanks,cap)]).reshape(-1,3)
    adjustable=np.array([tank.adjustable for tank in tanks],dtype=bool)
    def influences(arm,weight_sf,weight_bm):
        totals=np.vstack((np.ones(len(arm)),arm))
        return rsf@totals-G*weight_sf,G*weight_bm-rbm@totals
    sf_cell,bm_cell=influences(cell_x,(stations[:,None]>=cell_x).astype(float),
                              np.maximum(stations[:,None]-cell_x,0.))
    raw={record['id']:record for record in parameters.get('tanks',[])}
    tank_integrals=[unit_tank_weight_integrals(TankGeometry.from_params(raw[tank.id],ship_params=parameters),
                                             tank.capacity_m3/2,stations) for tank in tanks]
    tsf=np.column_stack([pair[0] for pair in tank_integrals]) if tanks else np.empty((len(stations),0))
    tbm=np.column_stack([pair[1] for pair in tank_integrals]) if tanks else np.empty((len(stations),0))
    sf_tank,bm_tank=influences(arms[:,0],tsf,tbm)
    sf_tank*=adjustable; bm_tank*=adjustable
    light_sf=np.zeros(len(stations)); light_bm=np.zeros(len(stations))
    for left,right,mass in hull.lightship_distribution:
        length=np.clip(stations-left,0.,right-left)
        light_sf+=mass*length/(right-left)
        light_bm+=mass/(right-left)*(length*(stations-left)-length**2/2)
    mass=hull.lightship_mass_kg
    moments=mass*np.array([hull.lightship_lcg_m,hull.lightship_tcg_m,hull.lightship_vcg_m])
    trim_gradient=np.array([1.,-1.])@np.linalg.inv(targets)
    trim=(-float(trim_gradient@np.array([mid,mid*row.lcb_m])),float(trim_gradient[1]),float(trim_gradient[0]))
    draught_slope=1/(row.tpc_kg_per_cm*100)
    zero=LinearErrors(0.,0.,0.,0.,np.zeros(len(stations)),np.zeros(len(stations)))
    model=LinearModel(instance.vessel.id,LINEAR_COEFFICIENTS_VERSION,rho,band,zero,
        tuple(cell.id for cell in cells),tuple(tank.id for tank in tanks),stations,
        cell_x,cell_y,cell_z,arms[:,0],arms[:,1],arms[:,2],cap,
        _maximum_tank_fsms(instance,parameters,tanks),adjustable,
        sf_cell,bm_cell,sf_tank,bm_tank,mass,*map(float,moments),
        bsf+rsf@np.array([mass,moments[0]])-G*light_sf,
        G*light_bm-bbm-rbm@np.array([mass,moments[0]]),
        (float(intercept),float(slope)),trim,(t-draught_slope*mid,draught_slope),
        max(float(get_rule('iscode.a2_2.initial_gm').parameters['minimum'].value),hull.min_gm_m),
        hull.max_trim_m,hull.max_list_rad,
        np.array([s.max_shear_n for s in hull.stations]),np.array([s.min_shear_n for s in hull.stations]),
        np.array([s.max_bending_nm for s in hull.stations]),np.array([s.min_bending_nm for s in hull.stations]))
    arrays=(rsf,rbm,bsf,bbm,light_sf,light_bm)
    for name,value in zip(_ARRAYS,arrays,strict=True):
        value.flags.writeable=False
        object.__setattr__(model,'_family_'+name,value)
    object.__setattr__(model,'_reference_params',parameters)
    object.__setattr__(model,'_geometry_fingerprint',hashlib.sha256(repr(parameters).encode()).hexdigest())
    from .operations import vessel_physics_identity
    object.__setattr__(model,'_vessel_physics_fingerprint',vessel_physics_identity(instance.vessel))
    for value in vars(model).values():
        if isinstance(value,np.ndarray):value.flags.writeable=False
    store_model(key,model)
    family=CoefficientFamily(model,*arrays,parameters,key)
    if len(_FAMILY_MEMORY)>=16:_FAMILY_MEMORY.pop(next(iter(_FAMILY_MEMORY)))
    _FAMILY_MEMORY[key]=family
    return family


def assemble_instance(family,instance,narrow_band,*,errors=None,diagnostics=None):
    """Apply current fixed loads exactly; no hull integrations or calibration.

    A caller must provide independently calibrated errors/diagnostics. A raw
    coefficient family's zero errors cannot silently become a solver model.
    """
    from .physics import _fixed,G
    from .bands import departure_manifest,nominal_region_heights,nominal_height,box_height
    from .operations import departure_manifest_identity, vessel_physics_identity
    from stowage.versions import LINEAR_MODEL_VERSION
    if errors is None or diagnostics is None:
        raise ValueError('instance assembly requires family calibration errors and diagnostics')
    model=family.template; broad=model.band
    from .cache import coefficient_family_key
    if coefficient_family_key(instance,family.parameters,broad)!=family.cache_key:
        raise ValueError('instance hull/slice/tanks/density differ from coefficient family')
    if instance.onboard.covers_off:
        raise ValueError('linear model requires closed hatch covers at departure')
    if not (broad.displacement_min_kg<=narrow_band.displacement_min_kg<narrow_band.displacement_max_kg<=broad.displacement_max_kg
            and broad.draught_min_m<=narrow_band.draught_min_m<narrow_band.draught_max_m<=broad.draught_max_m
            and 0<narrow_band.max_abs_trim_m<=broad.max_abs_trim_m
            and 0<narrow_band.max_abs_heel_rad<=broad.max_abs_heel_rad):
        raise ValueError('instance validity band exceeds the calibrated coefficient family domain')
    fixed=_fixed(instance)
    const_mass=float(sum(fixed[:,0])); moments=(fixed[:,0,None]*fixed[:,1:]).sum(axis=0)
    sf=family.lightship_sf.copy(); bm=family.lightship_bm.copy(); stations=model.station_x_m
    for x,y,z,mass in instance.vessel.fixed_loads:
        sf+=mass*(stations>=x);bm+=mass*np.maximum(stations-x,0.)
    for tank in instance.vessel.tanks:
        if tank.adjustable:continue
        mass=instance.onboard.tank_masses_kg.get(tank.id,0.)
        if not mass:continue
        tsf,tbm=_fixed_tank_integrals(family.parameters,tank.id,
                                     mass/tank.content_density_kg_m3,stations)
        sf+=mass*tsf;bm+=mass*tbm
    region=nominal_region_heights(instance)
    z_by_id={cid:stack.base_z_m+(j+.5)*region[stack.on_deck]
             for stack in instance.vessel.stacks for j,cid in enumerate(stack.cell_ids)}
    cell_z=np.array([z_by_id[cid] for cid in model.cell_ids])
    # A column cannot have more high boxes below a cell than the entire known
    # eligible inventory contains. Taking its largest j cell-token heights and
    # the largest half-height independently is conservative (a box may be used
    # twice in this bound); it does not claim an attainable packing.
    manifest=departure_manifest(instance)
    heights_by_region={}
    for on_deck in {stack.on_deck for stack in instance.vessel.stacks}:
        eligible=[box for box in manifest if not
            (('on_deck' in box.special_stowage and not on_deck) or
             ('under_deck' in box.special_stowage and on_deck))]
        heights=sorted([box_height(box) for box in eligible
                        for _ in range(1 if box.length_ft==20 else 2)],reverse=True)
        heights_by_region[on_deck]=heights or [region[on_deck]]
    upper_by_id={}; point_height_deviations={}
    for stack in instance.vessel.stacks:
        heights=heights_by_region[stack.on_deck]
        largest=heights[0];smallest=heights[-1]
        ascending=heights[::-1]
        for j,cid in enumerate(stack.cell_ids):
            support=sum(heights[:j])+max(0,j-len(heights))*largest
            upper_by_id[cid]=stack.base_z_m+min(support+largest/2,stack.max_height_m-smallest/2)
            lower=stack.base_z_m+sum(ascending[:j])+max(0,j-len(ascending))*smallest+smallest/2
            upper=stack.base_z_m+support+largest/2
            point_height_deviations[cid]=max(0.,upper-z_by_id[cid],z_by_id[cid]-lower)
    constraint_z=np.array([upper_by_id[cid] for cid in model.cell_ids])
    cargo_mass=float(sum(box.gross_mass_kg for box in manifest))
    domain=diagnostics.get('family_domain')
    if domain is not None:
        try:
            kg_low,kg_high=float(domain['kg_min_m']),float(domain['kg_max_m'])
            x_low,x_high=float(domain['fixed_x_min_m']),float(domain['fixed_x_max_m'])
            y_max=float(domain['fixed_y_abs_max_m'])
        except (KeyError,TypeError,ValueError) as exc:
            raise ValueError('family calibration has an invalid centroid domain') from exc
        if not np.isfinite([kg_low,kg_high,x_low,x_high,y_max]).all() or kg_low>kg_high or x_low>x_high or y_max<0:
            raise ValueError('family calibration has an invalid centroid domain')
        for x,y,z,mass in instance.vessel.fixed_loads:
            if mass>0 and not (kg_low<=z<=kg_high and x_low<=x<=x_high and abs(y)<=y_max):
                raise ValueError('positive fixed load centroid is outside the family calibration domain')
        if cargo_mass>0 and np.any(constraint_z>kg_high+1e-12):
            raise ValueError('possible cargo height is outside the family calibration domain')
    height_guard=max(point_height_deviations.values(),default=0.)*cargo_mass/narrow_band.displacement_min_kg
    if height_guard<1e-12:height_guard=0.
    # Raw atan2 angles can sit on opposite sides of the +/-pi branch, including
    # negative-GM points. Preserve the measured family maximum, and use the
    # full raw-angle difference bound when mixed heights perturb that branch.
    list_guard=float(2*np.pi) if height_guard else 0.
    point_errors=replace(errors,gm_m=errors.gm_m+height_guard,
                         list_rad=max(errors.list_rad,list_guard))
    from dataclasses import asdict
    instance_diagnostics=dict(diagnostics,
        family_cache_key=family.cache_key,
        family_calibration_band=asdict(broad),
        calibration_scope='shared hull/slice/tanks; independent of booking and fixed loads',
        family_calibration_samples=int(diagnostics.get('samples',0)),
        family_measured_absolute_errors=asdict(errors),
        analytic_point_height_guards={'max_cell_arm_deviation_m':max(point_height_deviations.values(),default=0.),
            'gm_m':height_guard,'list_rad':list_guard,
            'note':'Analytic inventory-height bound augments point errors; signed constraint errors retain family calibration and conservative cell arms.'})
    result=replace(model,version=LINEAR_MODEL_VERSION,band=narrow_band,errors=point_errors,cell_vcg_m=cell_z,
        const_mass_kg=const_mass,const_moment_x_kgm=float(moments[0]),
        const_moment_y_kgm=float(moments[1]),const_moment_z_kgm=float(moments[2]),
        const_sf_n=family.buoyancy_sf+family.response_sf@np.array([const_mass,moments[0]])-G*sf,
        const_bm_nm=G*bm-family.buoyancy_bm-family.response_bm@np.array([const_mass,moments[0]]))
    from .cache import fixed_loads_fingerprint
    metadata=dict(measurement_diagnostics=instance_diagnostics,_reference_params=family.parameters,
        _fixed_loads_fingerprint=fixed_loads_fingerprint(instance),
        _geometry_fingerprint=getattr(model,'_geometry_fingerprint'),
        # Identity guards are refreshed from current inputs on assembly, even
        # when the pure numerical coefficient payload came from an older hit.
        _vessel_physics_fingerprint=vessel_physics_identity(instance.vessel),
        _cargo_mass_kg=cargo_mass,
        _tank_mass_bounds_kg=getattr(narrow_band,'_tank_mass_bounds_kg',{}),
        _constraint_cell_vcg_m=constraint_z,
        _nominal_height_m=nominal_height(instance),hull_length_m=instance.vessel.hull.lpp_m,
        hull_max_draught_m=min(instance.vessel.hull.max_draught_m,instance.port.max_draught_m or instance.vessel.hull.max_draught_m),
        hull_min_draught_fwd_m=instance.vessel.hull.min_draught_fwd_m,
        _fixed_tank_masses={tank.id:instance.onboard.tank_masses_kg.get(tank.id,0.) for tank in instance.vessel.tanks if not tank.adjustable})
    if getattr(narrow_band,'_cargo_mass_kg',None) is not None:
        metadata['_manifest_ids'],metadata['_manifest_fingerprint']=departure_manifest_identity(instance)
    for name,value in metadata.items():object.__setattr__(result,name,value)
    for value in vars(result).values():
        if isinstance(value,np.ndarray):value.flags.writeable=False
    return result
