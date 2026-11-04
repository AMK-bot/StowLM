"""Physical HEEL authority on a fixed cohort, independent of cargo rebooking.

This initial-GM design test is not a full manual-condition acceptance certificate.
D71 separately requalifies fills, cargo, load line and the seagoing criteria.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from stowage.checker.stability_equilibrium import floating_equilibrium
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_loading import liquid_cg, loading_condition
from stowage.conventions import make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel


REFERENCES = json.loads((Path(__file__).with_name("fixtures") / "heel_loaded_references.json").read_text())["rows"]


def reference_instance(vessel, reference):
    payload = build_instance().model_dump(mode="python")
    containers, placements = [], []
    for serial, (slot, mass) in enumerate(reference["cargo_cells"], 1):
        cid = make_container_id("QZXU", serial)
        containers.append(dict(id=cid, iso_type="22G1", length_ft=20,
            height_m=2.591, tare_kg=2200., gross_mass_kg=mass,
            max_gross_mass_kg=30480., pol="THLCH", pod="THBKK"))
        placements.append(dict(slot=slot, container_id=cid))
    tanks = {tank.id: tank for tank in vessel.tanks}
    pair = vessel.tank_pairs[0]
    masses = dict(reference["non_heel_tank_masses_kg"])
    for tid in (pair.port_tank, pair.starboard_tank):
        masses[tid] = .5 * tanks[tid].capacity_m3 * tanks[tid].content_density_kg_m3
    payload.update(vessel=vessel, containers=containers, load_list=[], discharge_list=[],
                   onboard=dict(vessel_id=vessel.id, placements=placements, tank_masses_kg=masses))
    payload["yard"]["stacks"] = []
    return Instance.model_validate(payload)


def physical_authority(vessel, params, reference):
    """Solve the same liquid/hull equilibrium used by the in-port criterion."""
    instance = reference_instance(vessel, reference)
    geometry = StabilityGeometry(params)
    condition = loading_condition(instance, instance.onboard, geometry=geometry,
        ship_params=params, free_surface_method=1, precomputed_soundings=True,
        small_tank_exemption=True)
    assert not condition.errors
    density = instance.port.water_density_kg_m3
    floating = floating_equilibrium(geometry, condition, density, vessel.hull.lpp_m, liquid_cg)
    _, drafts = geometry.free_trim_curve([0.], condition.mass_kg, condition.lcg_m,
        condition.kg_m, density,
        cg_callback=lambda phi, trim: liquid_cg(condition, phi, trim),
        initial_drafts=(floating.t_aft_m, floating.t_fwd_m))
    upright = geometry.full_submerged(*drafts[0], 0., density)
    normal = geometry.waterplane_normal(*drafts[0], 0.)
    gm = float(np.dot(np.asarray(upright.centroid_m)
        - [condition.lcg_m, 0., condition.kg_m], normal)) + upright.bm_t_m - condition.fsc_m
    assert math.isfinite(gm) and gm > 0, (reference["ship"], reference["condition"], gm)
    port = next(tank for tank in vessel.tanks if tank.id == vessel.tank_pairs[0].port_tank)
    moment = port.capacity_m3 * port.content_density_kg_m3 * port.table[-1].tcg_m
    angle = math.degrees(math.atan(moment / (condition.mass_kg * gm)))
    return gm, angle


@pytest.mark.slow
@pytest.mark.parametrize("name", ("feeder_500", "feeder_800", "feeder_1700"))
def test_actual_tanks_have_three_degree_authority_on_positive_gm_loaded_references(name):
    vessel, params = load_vessel(name), load_ship_params(name)
    rows = [row for row in REFERENCES if row["ship"] == name]
    assert {row["condition"] for row in rows} == {"C1", "C2", "C3"}
    for reference in rows:
        gm, angle = physical_authority(vessel, params, reference)
        assert gm > 0 and angle >= 3., (name, reference["condition"], gm, angle)
