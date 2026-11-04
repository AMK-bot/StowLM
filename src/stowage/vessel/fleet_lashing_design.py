"""D56 lashing design maximum from actual D27 departure/arrival conditions.

APPROXIMATION: synthetic loading-manual design data, not an approved securing
manual. No passing-status filter or enclosing physical GM bound is substituted
for the six prescribed C1/C2/C3 departure/arrival corrected-GM evaluations.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np

from stowage.conventions import CONTAINER_HEIGHT_STD_M
from stowage.model import (Provenance, ShipState, VesselFieldProvenance,
    VoyageConditionIdentity, VoyageSpec, derive_arrival_liquid_state)
from stowage.model.vessel import FixedLoad
from stowage.vessel.fleet_manual import manual_port, standard_manual_conditions
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.tanks import TankGeometry


def _manual_voyage(recipe):
    identities=[]
    for index,phase in enumerate(('departure','arrival')):
        port=manual_port(recipe['route'][index])
        identities.append(VoyageConditionIdentity(
            id=f"D27.{recipe['condition']}.{phase}.{port.locode}",phase=phase,
            call_index=index,locode=port.locode,
            environment_id=f"D27.standard.{port.archetype}",
            density_id=f"{port.archetype}.water_density_kg_m3.{port.water_density_kg_m3:g}",
            water_density_kg_m3=port.water_density_kg_m3,
            provenance=Provenance(created_by='vessel.fleet_lashing_design.manual_condition',
                                  parents=[recipe['recipe_source'],*port.sources])))
    return VoyageSpec(departure=identities[0],arrival=identities[1],
        provenance=Provenance(created_by='vessel.fleet_lashing_design.manual_voyage',
            parents=['D50/D57: same cargo/ballast; every consumable tank retains ten percent proportionally',recipe['recipe_source']]))


def _cargo_points(vessel, placements):
    cells={cell.id:cell for cell in vessel.cells}
    stacks={stack.id:stack for stack in vessel.stacks}
    tops={sid:stack.base_z_m for sid,stack in stacks.items()}
    points=[]
    for cid,mass in sorted(placements,key=lambda item:(item[0][-2:],item[0])):
        cell=cells[cid]
        bottom=tops[cell.stack_id]
        points.append((cell.lcg_m,cell.tcg_m,bottom+CONTAINER_HEIGHT_STD_M/2,mass))
        tops[cell.stack_id]=bottom+CONTAINER_HEIGHT_STD_M
    return points


def _corrected_condition(vessel, ship_params, cargo, tank_masses, water_density):
    """Exact port of visibility._loading_condition's numerical physics path.

    Consume the supplied parameter context directly: no name-only checker cache,
    load_vessel recursion, experiment imports, or checker globals are modified.
    The finite-angle liquid-centroid iteration and tangent FSM match the shared
    visibility/lashing consumer. APPROXIMATION: the same initial-GM approximation applies:
    upright KB+BM-KG with equilibrium waterplane inertia and loaded-list/trim FSC.
    """
    hull=vessel.hull
    geometry=HullGeometry.from_params(ship_params)
    if not np.allclose([geometry.lpp_m,geometry.beam_m,geometry.depth_m],
                       [hull.lpp_m,hull.beam_m,hull.depth_m],rtol=1e-9):
        raise ValueError('D56 hull geometry dimensions disagree with vessel')
    masses=[(hull.lightship_lcg_m,hull.lightship_tcg_m,hull.lightship_vcg_m,hull.lightship_mass_kg)]
    loads=[FixedLoad.model_validate(raw.model_dump() if isinstance(raw,FixedLoad) else raw)
           for raw in vessel.fixed_loads]
    masses.extend((load.x_m,load.y_m,load.z_m,load.mass_kg) for load in loads)
    masses.extend(_cargo_points(vessel,cargo))
    tank_models={tank.id:tank for tank in vessel.tanks}
    raw_tanks={tank['id']:tank for tank in ship_params.get('tanks',[])}
    liquids=[]
    for tid,mass in tank_masses.items():
        if not mass:
            continue
        if tid not in tank_models or tid not in raw_tanks:
            raise ValueError(f'D56 missing tank geometry: {tid}')
        tank=tank_models[tid]
        solid=TankGeometry.from_params(raw_tanks[tid])
        if not math.isclose(solid.capacity_m3,tank.capacity_m3,rel_tol=1e-9) or not np.allclose(
                [solid.x_min_m,solid.x_max_m],[tank.x_aft_m,tank.x_fore_m],rtol=1e-9):
            raise ValueError(f'D56 tank geometry disagrees with vessel tank: {tid}')
        volume=mass/tank.content_density_kg_m3
        liquid=solid.liquid(volume)
        liquids.append((solid,volume,mass,tank.content_density_kg_m3))
        masses.append((*liquid.centroid_m,mass))
    total=sum(point[3] for point in masses)
    base=masses[:-len(liquids)] if liquids else masses
    previous=None
    for iteration in range(30):
        cg=np.sum([np.array(point[:3])*point[3] for point in masses],axis=0)/total
        equilibrium=geometry.equilibrium(total,*cg,water_density)
        pose=np.array([equilibrium.t_aft_m,equilibrium.t_fwd_m,equilibrium.heel_rad])
        if previous is not None and np.max(np.abs(pose-previous))<1e-7:
            break
        previous=pose
        trim=math.atan2(equilibrium.trim_m,geometry.lpp_m)
        masses=base+[(*solid.liquid(volume,equilibrium.heel_rad,trim).centroid_m,mass)
                     for solid,volume,mass,_ in liquids]
    else:
        raise ValueError('D56 tank-centroid/equilibrium iteration did not converge')
    fsm=sum(solid.liquid(volume,equilibrium.heel_rad,
                        math.atan2(equilibrium.trim_m,geometry.lpp_m)).fsm_m4*density
            for solid,volume,_,density in liquids)
    solid_gm=equilibrium.submerged.vcb_m+equilibrium.submerged.bm_t_m-cg[2]
    return dict(gm_m=float(solid_gm-fsm/total),gm_solid_m=float(solid_gm),
        fsc_m=float(fsm/total),mass_kg=float(total),lcg_m=float(cg[0]),tcg_m=float(cg[1]),
        kg_m=float(cg[2]),draught_aft_m=equilibrium.t_aft_m,
        draught_fwd_m=equilibrium.t_fwd_m,list_rad=equilibrium.heel_rad,
        equilibrium_iterations=iteration+1)


def evaluate_manual_condition(vessel, ship_params, recipe, phase):
    """Evaluate one prescribed phase using actual tanks, cargo and dock density."""
    if phase not in ('departure','arrival'):
        raise ValueError(f'unknown D56 manual phase: {phase}')
    voyage=_manual_voyage(recipe)
    departure=ShipState(vessel_id=vessel.id,tank_masses_kg=dict(recipe['departure_tank_masses_kg']))
    identity=voyage.departure if phase=='departure' else voyage.arrival
    tanks=(departure.tank_masses_kg if phase=='departure' else
           derive_arrival_liquid_state(departure,voyage,vessel=vessel).tank_masses_kg)
    metrics=_corrected_condition(vessel,ship_params,recipe['cargo_cells'],tanks,
                                 identity.water_density_kg_m3)
    return dict(**metrics,condition_id=identity.id,locode=identity.locode,
        water_density_kg_m3=identity.water_density_kg_m3,density_id=identity.density_id,
        density_sources=identity.provenance.parents,tank_masses_kg=dict(tanks))


def populate_lashing_design(vessel, ship_params, parameter_source):
    """Populate both hull fields after every prescribed GM evaluates successfully.

    Return the complete deterministic audit, or None for missing physical
    hull/tanks. Existing state is untouched on failure; no manual is fabricated
    for geometry-only or lightweight diagnostic test fixtures.
    """
    if vessel.hull is None or not vessel.tanks:
        return None
    if not isinstance(parameter_source,str) or not parameter_source.strip():
        raise ValueError('D56 design requires parameter-source provenance')
    recipes=standard_manual_conditions(vessel,ship_params)
    if [recipe['condition'] for recipe in recipes]!=['C1','C2','C3']:
        raise ValueError('D56 design requires all three C1/C2/C3 standard conditions')
    records=[]
    for recipe in recipes:
        for phase in ('departure','arrival'):
            metrics=evaluate_manual_condition(vessel,ship_params,recipe,phase)
            if not math.isfinite(metrics['gm_m']):
                raise ValueError('D56 standard-condition corrected GM must be finite')
            records.append(dict(**metrics,condition=recipe['condition'],phase=phase,
                ballast_label=recipe.get('ballast_label'),recipe_source=recipe.get('recipe_source'),
                cargo_reserve_ballast_label=recipe.get('cargo_reserve_ballast_label'),
                recipe_correction=recipe.get('recipe_correction'),
                ballast_witness_provenance=recipe.get('ballast_witness_provenance'),
                historical_witness=recipe.get('historical_witness',False),
                boxes=len(recipe.get('cargo_cells',[])),cargo_mass_kg=recipe.get('cargo_mass_kg'),
                cargo_budget_kg=recipe.get('cargo_budget_kg'),uniform_mass_kg=recipe.get('uniform_mass_kg'),
                cargo_sha256=hashlib.sha256(json.dumps(recipe.get('cargo_cells',[]),separators=(',',':')).encode()).hexdigest()))
    governing=max(records,key=lambda row:row['gm_m'])
    maximum=1.10*governing['gm_m']
    if not math.isfinite(maximum) or maximum<=0:
        raise ValueError('D56 loading-manual maximum corrected GM must be finite and positive')
    audit=dict(method='D56_D27_C1_C2_C3_departure_arrival_corrected_gm_v1',
        conditions=records,governing=dict(governing),factor=1.1,max_gm_lashing_m=maximum,
        route=list(recipes[0].get('route',())),
        excluded=['C4 ballast-only: outside D27 verified loading-condition envelope'],
        approximation='APPROXIMATION: synthetic loading-manual design data; not an approved lashing manual')
    encoded=json.dumps(audit,sort_keys=True,separators=(',',':'),allow_nan=False)
    evidence=VesselFieldProvenance(source=encoded,assumption=audit['approximation'],
        provenance=Provenance(created_by='vessel.fleet_lashing_design.populate_lashing_design',
            parents=[parameter_source,'docs/decisions.md:D27','docs/decisions.md:D50','docs/decisions.md:D56','docs/decisions.md:D57',
                     'experiments/physics-review/convergence/post_d30/harness2.py:build',
                     'experiments/physics-review/convergence/harness.py:place',
                     'src/stowage/model/fixtures.py: historical manual route THLCH->THBKK->THSGZ',
                     *(recipe['recipe_source'] for recipe in recipes)]).merge_nested(
                         vessel, *(Provenance.model_validate(recipe['ballast_witness_provenance'])
                                   for recipe in recipes if recipe.get('ballast_witness_provenance'))))
    vessel.hull.max_gm_lashing_m=maximum
    vessel.hull.max_gm_lashing_m_provenance=evidence
    return audit
