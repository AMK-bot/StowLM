"""Production KCS feeder benchmark with homogeneous cargo and no CG balancing.

ASSUMPTION: a reproducible dry benchmark, not an approved voyage condition.
All holds and the lowest two deck tiers carry identical standard 20ft boxes;
all tank masses are explicitly zero, so this omits voyage consumable demand.
Total mass is 90% of upright design-draught displacement in port water.
No fixed cargo, artificial moments, changed lightship, or ballast correction is
introduced. Source: production fleet parameters; IMO MSC.267(85), A2/B2.3.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from stowage.checker.stability import check_stability
from stowage.conventions import make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.hydrostatics import HullGeometry

FLEET = ("feeder_500", "feeder_800", "feeder_1700")
B_CRITERIA = {
    "area_0_30_mrad": (.009, "m rad"),
    "area_0_40_mrad": (.016, "m rad"),
    "area_30_40_mrad": (.006, "m rad"),
    "gz_at_or_above_30_m": (.033, "m"),
    "gz_max_m": (.042, "m"),
    "area_0_flood_mrad": (.029, "m rad"),
}


def build_departure(name: str) -> tuple[Instance, dict]:
    """Use the unmodified production HullModel and validate the whole instance."""
    start = perf_counter()
    vessel = load_vessel(name, with_hull=True)
    params = load_ship_params(name)
    payload = build_instance().model_dump(mode="python")
    rho = payload["port"]["water_density_kg_m3"]
    geometry = HullGeometry.from_params(params)
    design = geometry.submerged(vessel.hull.design_draught_m, vessel.hull.design_draught_m,
                                density_kg_m3=rho).displacement_kg
    selected_ids = {cid for stack in vessel.stacks
                    for cid in (stack.cell_ids[:2] if stack.on_deck else stack.cell_ids)}
    cells = [c for c in vessel.cells if c.id in selected_ids]
    target = .9 * design
    fixed_mass = sum(load[3] for load in vessel.fixed_loads)
    mass = (target - vessel.hull.lightship_mass_kg - fixed_mass) / len(cells)
    if not 2200 <= mass <= 32000:
        raise ValueError(f"homogeneous mass outside benchmark box range: {mass}")
    containers, placements = [], []
    for serial, cell in enumerate(cells, 1):
        cid = make_container_id("QZXU", serial)
        containers.append(dict(id=cid, iso_type="22G1", length_ft=20, height_m=2.591,
                               tare_kg=2200, gross_mass_kg=mass, pol="THLCH", pod="THBKK"))
        placements.append(dict(slot=cell.id, container_id=cid))
    payload.update(id=f"kcs_homogeneous_departure_{name}", vessel=vessel,
                   containers=containers, load_list=[], discharge_list=[],
                   onboard=dict(vessel_id=vessel.id, placements=placements,
                                tank_masses_kg={t.id: 0. for t in vessel.tanks}))
    payload["yard"]["stacks"] = []
    payload["port"]["max_draught_m"] = vessel.hull.max_draught_m
    instance = Instance.model_validate(payload)
    return instance, dict(vessel=name, condition="all holds + lowest two deck tiers; identical 20ft boxes; explicit dry tanks",
                          assumptions=["dry benchmark; no voyage consumables", "90% upright design displacement target"],
                          production_loader="load_vessel(name, with_hull=True)",
                          production_parameter_source=vessel.sources[0],
                          lpp_m=vessel.hull.lpp_m,
                          opening_count=len(params["envelope"].get("openings", [])),
                          container_count=len(cells), gross_mass_per_box_kg=mass,
                          target_displacement_kg=target, design_displacement_kg=design,
                          production_fixed_loads=vessel.fixed_loads, build_seconds=perf_counter()-start)


def run_departure(name: str) -> dict:
    instance, summary = build_departure(name)
    start = perf_counter()
    report = check_stability(instance, instance.onboard, call_index=0)
    summary.update(check_seconds=perf_counter()-start, ran=report.ran, error=report.error,
                   metrics=report.metrics, margins=report.margins,
                   violation_ids=sorted({v.rule_id for v in report.violations}))
    if report.ran:
        m, margins = report.metrics, report.margins
        criteria = {"A2.2.4 initial GM": dict(measured=m["gm_m"], minimum=.15,
                    unit="m", passes=m["gm_m"] >= .15-1e-12)}
        C = m["containership_form_factor"]
        for key, (coefficient, unit) in B_CRITERIA.items():
            criteria["B2.3 " + key] = dict(measured=m[key], minimum=coefficient/C,
                unit=unit, passes=margins["containership_"+key] >= -1e-12)
        criteria["B2.3 flooding >=30deg"] = dict(measured=m["angle_flooding_rad"],
            minimum=0.5235987755982988, unit="rad",
            passes=margins["containership_downflooding_30_margin_rad"] >= -1e-12)
        for key, minimum in (("weather_ratio", 1.), ("weather_steady_heel_margin_rad", 0.),
                             ("weather_table_applicability", 1.)):
            measured = m[key] if key in m else margins[key]+minimum
            criteria[key] = dict(measured=measured, minimum=minimum,
                unit="rad" if key.endswith("rad") else "1", passes=margins[key] >= -1e-12)
        summary["criteria"] = criteria
    return summary


def main():
    results = [run_departure(name) for name in FLEET]
    root = Path(__file__).resolve().parent
    (root/"kcs_departures.json").write_text(json.dumps(results, indent=2)+"\n")
    lines = ["# Production KCS homogeneous dry departures", "",
        "Reproduce: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/stability/kcs_departures.py`.", "",
        "ASSUMPTION: all holds plus the lowest two deck tiers, identical 20ft standard-height boxes, explicit zero tank masses. Target mass is 90% of upright design displacement. Production lightship, hull, openings, fixed loads and centres are unchanged; cargo masses do not vary along the vessel. This dry diagnostic is not an approved voyage or full-checker result.", "",
        "Each criterion shows measured / minimum (PASS or FAIL). Weather table applicability is a hard validity requirement.", ""]
    for result in results:
        lines += ["## "+result["vessel"], "", f"Boxes: {result['container_count']}; uniform gross mass {result['gross_mass_per_box_kg']:.2f} kg. Checker ran: {result['ran']}; time {result['check_seconds']:.3f} s.", ""]
        if result["error"]:
            lines += ["Error: "+result["error"], ""]
        for label, c in result.get("criteria", {}).items():
            lines.append(f"- {label}: {c['measured']:.6g} / {c['minimum']:.6g} {c['unit']} — {'PASS' if c['passes'] else 'FAIL'}")
        lines += ["", "Violations: "+", ".join(result["violation_ids"]), ""]
    (root/"kcs_departures.md").write_text("\n".join(lines)+"\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
