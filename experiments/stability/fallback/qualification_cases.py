"""Numerical interpolation holdouts across the complete departure table domain.

ASSUMPTION: point-mass verification loads are synthetic test weights, not a cargo
stow or voyage certificate. They set prescribed list/trim without changing the
production hull, tanks, lightship or operating limits. Their nonnegative mass
pays for all actually carried liquids. Boundary list10° deliberately exceeds
operational list limits; the checker must still calculate and reject it quickly.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_loading import loading_condition, liquid_cg
from stowage.model import FixedLoad, Instance
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel


def domain_condition(name, rng, index):
    """Return a validated synthetic instance and explicit intended domain point."""
    params=load_ship_params(name)
    geometry=StabilityGeometry(params)
    vessel=load_vessel(name,with_hull=True)
    payload=build_instance().model_dump(mode='python')
    rho=payload['port']['water_density_kg_m3']
    # Cover masses and production fixed loads remain present in the baseline.
    fills={tank.id:(0. if index==0 else 1. if index==1 else
                   float((i+index)%2) if index==2 else float(rng.uniform(0.,1.)))
           for i,tank in enumerate(vessel.tanks)}
    masses={tank.id:fills[tank.id]*tank.capacity_m3*tank.content_density_kg_m3
            for tank in vessel.tanks}
    payload.update(id=f'liquid_domain_{name}_{index}',vessel=vessel,containers=[],load_list=[],discharge_list=[],
        onboard=dict(vessel_id=vessel.id,placements=[],tank_masses_kg=masses))
    payload['yard']['stacks']=[]
    payload['port']['max_draught_m']=vessel.hull.max_draught_m
    instance=Instance.model_validate(payload)
    baseline=loading_condition(instance,instance.onboard,geometry=geometry,ship_params=params,
        free_surface_method=1,precomputed_soundings=True,small_tank_exemption=True)
    if baseline.errors:
        raise ValueError('; '.join(baseline.errors))
    scantling=vessel.hull.scantling_draught_m
    maximum=geometry.submerged(scantling,scantling,0.,rho).displacement_kg
    if baseline.mass_kg > maximum:
        raise ValueError('actual liquid+lightship masses exceed scantling displacement')
    # First condition is genuinely light, including permanent cover masses.
    if index==0:
        return instance,dict(index=index,policy='empty tanks; no synthetic payload',
            mass_kg=baseline.mass_kg,lightship_mass_kg=vessel.hull.lightship_mass_kg,
            scantling_mass_kg=maximum,fill_fractions=fills)
    fraction=1. if index==1 else float(rng.uniform(.25,1.))
    mass=baseline.mass_kg+(maximum-baseline.mass_kg)*fraction
    cargo=mass-baseline.mass_kg
    if cargo <= 0:
        raise ValueError('no positive verification-load mass remains')
    starts=(0.,-10.,10.,2.7,-2.7)
    heel=np.deg2rad(starts[index%len(starts)])
    trim_fraction=(-1.,1.,.5,-.5,0.)[index%5]
    trim_m=trim_fraction*vessel.hull.max_trim_m
    def immersed(mean):
        return geometry.submerged(mean+trim_m/2,mean-trim_m/2,heel,rho)
    mean=brentq(lambda value:immersed(value).displacement_kg-mass,
                -.1*geometry.depth_m,geometry.depth_m+geometry.beam_m)
    underwater=immersed(mean)
    normal=geometry.waterplane_normal(mean+trim_m/2,mean-trim_m/2,heel)
    # A positive local metacentric margin selects the stable prescribed-list
    # branch instead of intentionally manufacturing an unstable equilibrium.
    # Full-curve checks still decide actual upright GM and all operating rules.
    full=geometry.full_submerged(mean+trim_m/2,mean-trim_m/2,heel,rho)
    target_margin=float(rng.uniform(.8,2.5))
    target_kg=min(.85*geometry.depth_m,
        underwater.vcb_m+(full.bm_t_m-baseline.fsc_m-target_margin)*normal[2])
    target=np.asarray(underwater.centroid_m)+(target_kg-underwater.vcb_m)/normal[2]*normal
    liquid_centre=np.asarray(liquid_cg(baseline,float(heel),float(np.arctan(trim_m/geometry.lpp_m))))
    load_centre=(mass*target-baseline.mass_kg*liquid_centre)/cargo
    if not (0 <= load_centre[0] <= geometry.lpp_m and abs(load_centre[1]) <= geometry.beam_m/2
            and 0 <= load_centre[2] <= geometry.depth_m+15.):
        raise ValueError(f'verification load lies outside physical ship envelope: {load_centre}')
    instance.vessel.fixed_loads.append(FixedLoad(x_m=float(load_centre[0]),
        y_m=float(load_centre[1]),z_m=float(load_centre[2]),mass_kg=cargo,kind='other'))
    # Revalidate the final fixed-load shape and physical loading contract.
    instance=Instance.model_validate(instance.model_dump(mode='python'))
    return instance,dict(index=index,policy='synthetic nonnegative point verification load',
        mass_kg=mass,lightship_mass_kg=vessel.hull.lightship_mass_kg,scantling_mass_kg=maximum,
        cargo_mass_kg=cargo,fill_fractions=fills,target_list_rad=float(heel),
        target_trim_m=float(trim_m),target_kg_m=target_kg,target_local_gm_margin_m=target_margin,
        verification_load_cg_m=load_centre.tolist())
