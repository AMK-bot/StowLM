"""Finite boundary qualification from prescribed *exact* floating attitudes.

The construction solves the original polygon displacement at a requested mass,
trim and heel. Dry-load moments balance buoyancy after subtracting the actual
method-1 liquid moments. No affine feasibility screen chooses or filters states.
This measures the linear response on synthetic physical-domain point loads; it
does not certify cargo securing, GZ/weather or a complete departure plan.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
from itertools import product
import json
import math
from pathlib import Path
import resource
import time

import numpy as np
from scipy.optimize import brentq

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EDGE_GRID_VERSION = "1.0"
SEEDS = {"train": 54931, "holdout": 54932}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def pin(path):
    path = Path(path).resolve()
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def specifications(phase):
    """128 training requests; a fixed cap of 256 candidates for 64 holdouts."""
    if phase not in SEEDS:
        raise ValueError("phase must be train or holdout")
    rows = []
    if phase == "train":
        for mass, trim, heel, kg, fill in product((0., 1.), (-1., 1.), (-1., 1.),
                                                (0., 1.), (0., .5, .98, 1.)):
            rows.append(dict(kind="corner", mass_fraction=mass, trim_fraction=trim,
                heel_fraction=heel, dry_kg_fraction=kg, fill_fraction=fill,
                fill_pattern="uniform"))
        # Deep draughts receive half the supplementary requests. Staggering the
        # fill branches also covers asymmetric port/starboard and peak tanks.
        for i in range(64):
            rows.append(dict(kind="edge", mass_fraction=(.25, .5, .75, .9, .97, .99, 1., 1.)[i % 8],
                trim_fraction=(-1., -.75, 0., .75, 1.)[(i // 8+i) % 5],
                heel_fraction=(-1., -.5, 0., .5, 1.)[(i // 4+i) % 5],
                dry_kg_fraction=(.05, .35, .65, .95)[(i // 2) % 4],
                fill_fraction=(0., .5, .98-1e-8, .98, .98+1e-8, 1.)[i % 6],
                fill_pattern="staggered", pattern_offset=i))
    else:
        rng = np.random.default_rng(SEEDS[phase])
        for i in range(256):
            rows.append(dict(kind="independent-offgrid", mass_fraction=float(rng.uniform(
                .8 if i % 2 else .05, .999999)), trim_fraction=float(
                    (-1. if i % 2 else 1.)*rng.uniform(.9, .999999)),
                heel_fraction=float((-1. if (i // 2) % 2 else 1.)*rng.uniform(.8, .999999)),
                dry_kg_fraction=float(rng.uniform(.01, .99)),
                fill_fraction=float(rng.uniform(.01, .97)),
                fill_pattern="independent-offgrid", pattern_offset=i,
                fill_seed=int(rng.integers(1, 2**31))))
    for index, row in enumerate(rows):
        row["index"] = index
        row["state_id"] = f"{phase}-{index:03d}-"+digest(row)[:16]
    return rows


def tank_fractions(request, count):
    if request["fill_pattern"] == "uniform":
        return np.full(count, request["fill_fraction"])
    if request["fill_pattern"] == "independent-offgrid":
        rng = np.random.default_rng(request["fill_seed"])
        values = rng.uniform(.001, .999999, count)
        # Independent points straddle the 98% branch without reusing any knot.
        if count:
            values[request["pattern_offset"] % count] = .98+(-1 if request["index"] % 2 else 1)*float(rng.uniform(1e-6, .001))
        return values
    knots = (0., .5, .98-1e-8, .98, .98+1e-8, 1.)
    return np.asarray([request["fill_fraction"] if i % 2 == 0 else
        knots[(i+request["pattern_offset"]) % len(knots)] for i in range(count)])


def balanced_point(displacement, buoyancy_cg, normal, base_mass, base_cg, dry_z):
    """Balance the two original waterplane moment equations, without fitting.

    Equilibrium requires total CG and buoyancy CG to differ only along the
    waterplane normal. Fix the dry point's z, then solve its x/y moments exactly.
    This is force/moment bookkeeping, not a replacement hydrostatic model.
    """
    dry = float(displacement-base_mass)
    if dry <= 0:
        raise ValueError("requested displacement leaves no positive dry load")
    normal = np.asarray(normal, dtype=float)
    if abs(normal[2]) < 1e-12:
        raise ValueError("waterplane normal has no usable vertical component")
    base_moment = base_mass*np.asarray(base_cg, dtype=float)
    total_z = (base_moment[2]+dry*dry_z)/displacement
    target = np.asarray(buoyancy_cg)+(total_z-buoyancy_cg[2])*normal/normal[2]
    point = (displacement*target-base_moment)/dry
    return dry, point


def labels(request):
    mass = request["mass_fraction"]
    mass_label = "minimum" if mass == 0 else "maximum" if mass == 1 else f"quarter-{min(int(mass*4), 3)}"
    return (request["kind"], "mass:"+mass_label,
            "trim-sign:"+str(int(np.sign(request["trim_fraction"]))),
            "heel-sign:"+str(int(np.sign(request["heel_fraction"]))),
            "dry-kg:"+("minimum" if request["dry_kg_fraction"] == 0 else
                "maximum" if request["dry_kg_fraction"] == 1 else "interior"),
            "fills:"+request["fill_pattern"], "fill-anchor:"+(
                "offgrid" if request["fill_pattern"] == "independent-offgrid" else str(request["fill_fraction"])))


def coverage(requests, states):
    strata = {}
    for request in requests:
        for label in labels(request):
            stats = strata.setdefault(label, dict(requested=0, evaluated=0, exact_evaluated=0, retained=0))
            stats["requested"] += 1
    for row in states:
        for label in labels(row["request"]):
            stats = strata[label]
            stats["evaluated"] += 1
            stats["exact_evaluated"] += int("exact_gm_m" in row)
            stats["retained"] += int(row["admitted"])
    return {"strata": strata, "requested": len(requests), "evaluated": len(states),
        "exact_evaluated": sum("exact_gm_m" in row for row in states),
        "retained": sum(row["admitted"] for row in states),
        "interpretation": "evaluated means processed, including explicitly impossible physical requests; exact_evaluated distinguishes converged physics"}


def load_driver():
    spec = importlib.util.spec_from_file_location("edge_epoch_driver", HERE/"driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(request, family, instance, geometry, domain):
    from stowage.checker.stability_loading import loading_condition, liquid_cg
    from stowage.checker.stability_liquid import attach_tables
    from stowage.linear.physics import exact_condition, _linear_arrays
    from stowage.linear.reusable import assemble_instance
    from stowage.model import FixedLoad, ShipState

    started = time.monotonic()
    base, band, hull = family.template, family.template.band, instance.vessel.hull
    tanks = sorted(instance.vessel.tanks, key=lambda tank: tank.id)
    fills = tank_fractions(request, len(tanks))
    values = fills*base.tank_capacity_kg
    mass = band.displacement_min_kg+request["mass_fraction"]*(band.displacement_max_kg-band.displacement_min_kg)
    trim = request["trim_fraction"]*band.max_abs_trim_m
    heel = request["heel_fraction"]*band.max_abs_heel_rad
    row = dict(state_id=request["state_id"], request=request, admitted=False,
        qualified_domain=False, requested_mass_kg=mass, requested_trim_m=trim,
        requested_list_rad=heel, requested_tank_fractions=fills.tolist())
    ship = ShipState(vessel_id=instance.vessel.id,
        tank_masses_kg={tank.id: float(value) for tank, value in zip(tanks, values, strict=True)})
    empty = instance.model_copy(update={"containers": [], "load_list": [], "discharge_list": [],
        "onboard": ship, "vessel": instance.vessel.model_copy(update={"fixed_loads": []})})
    try:
        condition = loading_condition(empty, ship, geometry=geometry,
            ship_params=family.parameters, free_surface_method=1,
            precomputed_soundings=True, small_tank_exemption=True)
        if condition.errors:
            raise ValueError("; ".join(condition.errors))
        if mass <= condition.mass_kg:
            raise ValueError("requested displacement leaves no positive dry load; tank fills were not clipped")
        condition = attach_tables(condition, family.parameters)
        # Root only the exact polygon displacement with the requested full
        # signed trim and heel held fixed. No affine rows appear in this root.
        def submerged(mean):
            return geometry.submerged(mean+trim/2, mean-trim/2, heel, base.water_density_kg_m3)
        mean = brentq(lambda draught: submerged(draught).displacement_kg-mass,
            .001-abs(trim)/2, geometry.max_z_m+abs(trim)/2, xtol=1e-11)
        buoyancy = submerged(mean)
        normal, _, _ = geometry._axes(mean+trim/2, mean-trim/2, heel)
        actual_cg = liquid_cg(condition, heel, math.atan(trim/geometry.lpp_m))
        dry_z = domain["kg_min_m"]+request["dry_kg_fraction"]*(domain["kg_max_m"]-domain["kg_min_m"])
        dry, point = balanced_point(mass, buoyancy.centroid_m, normal,
                                   condition.mass_kg, actual_cg, dry_z)
        row["fixed_point"] = dict(x_m=float(point[0]), y_m=float(point[1]),
                                 z_m=float(point[2]), mass_kg=dry)
        centroid = (domain["fixed_x_min_m"]-1e-7 <= point[0] <= domain["fixed_x_max_m"]+1e-7
            and abs(point[1]) <= domain["fixed_y_abs_max_m"]+1e-7
            and domain["kg_min_m"]-1e-7 <= point[2] <= domain["kg_max_m"]+1e-7)
        row["physical_centroid"] = bool(centroid)
        # Out-of-centroid requests remain auditable rejected corners. They are
        # never moved/clipped into the admitted family domain.
        if not centroid:
            raise ValueError("balanced fixed point lies outside the physical centroid domain")
        point_load = FixedLoad(**row["fixed_point"])
        state = empty.model_copy(update={"vessel": empty.vessel.model_copy(update={"fixed_loads": [point_load]})})
        current = assemble_instance(family, state, band, errors=base.errors,
                                   diagnostics={"calibration_synthetic": True})
        cell = np.zeros(len(base.cell_ids))
        tank = values*base.tank_adjustable
        exact = exact_condition(current, state, cell, tank, cell_vcg_m=current.cell_vcg_m)
        affine = _linear_arrays(current, cell, tank, instance=state, solver=True)
        my = float(current.const_moment_y_kgm+current.tank_tcg_m@tank)
        checks = dict(displacement=band.displacement_min_kg-1e-6 <= exact["displacement_kg"] <= band.displacement_max_kg+1e-6,
            draught=band.draught_min_m-1e-7 <= exact["draught_m"] <= band.draught_max_m+1e-7,
            trim=abs(exact["trim_m"]) <= band.max_abs_trim_m+1e-7,
            list=abs(exact["list_rad"]) <= band.max_abs_heel_rad+1e-7)
        admitted = bool(all(checks.values()) and centroid)
        row.update(admitted=admitted, qualified_domain=admitted, exact_domain=checks,
            mass_kg=float(exact["displacement_kg"]),
            affine_restoring_kgm=float(exact["displacement_kg"]*affine["gm_m"]),
            transverse_moment_kgm=my, cell_masses_kg=cell.tolist(), tank_masses_kg=tank.tolist(),
            all_tank_masses_kg=values.tolist(), cell_vcg_m=current.cell_vcg_m.tolist())
        for metric in ("gm_m", "trim_m", "list_rad", "draught_m", "sf_n", "bm_nm"):
            row["affine_"+metric] = np.asarray(affine[metric]).tolist()
            row["exact_"+metric] = np.asarray(exact[metric]).tolist()
        aft, fore = exact["draught_m"]+exact["trim_m"]/2, exact["draught_m"]-exact["trim_m"]/2
        row["quantity_pass"] = dict(gm=exact["gm_m"] >= current.gm_min_m,
            draught_aft=aft <= hull.max_draught_m, draught_forward=hull.min_draught_fwd_m <= fore <= hull.max_draught_m,
            sf=bool(np.all(exact["sf_n"] >= current.sf_min_n) and np.all(exact["sf_n"] <= current.sf_max_n)),
            bm=bool(np.all(exact["bm_nm"] >= current.bm_min_nm) and np.all(exact["bm_nm"] <= current.bm_max_nm)))
        row["manual_pass"] = dict(trim=hull.min_trim_m <= exact["trim_m"] <= hull.max_trim_m,
            displacement=hull.min_displacement_kg <= exact["displacement_kg"] <= hull.max_displacement_kg)
        row["target_replay_error"] = dict(mass_kg=float(exact["displacement_kg"]-mass),
            trim_m=float(exact["trim_m"]-trim), list_rad=float(exact["list_rad"]-heel))
        if not admitted:
            row["rejection_reason"] = "solved equilibrium lies outside the declared exact family domain"
    except (ValueError, ArithmeticError) as exc:
        row["rejection_reason"] = str(exc)
    row["seconds"] = time.monotonic()-started
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ship", required=True, choices=("feeder_500", "feeder_1000", "feeder_1700"))
    parser.add_argument("--phase", required=True, choices=tuple(SEEDS))
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    began = time.monotonic()
    driver = load_driver()
    family, instance, _, diagnostics, before = driver.prepare(args.ship, args.prepared)
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.linear.family_calibration import domain_for
    geometry, domain = StabilityGeometry(family.parameters), domain_for(family, instance)
    source = pin(__file__)
    requests = specifications(args.phase)
    config = dict(ship=args.ship, level="family", phase=args.phase, seed=SEEDS[args.phase],
        attempts=len(requests), construction="exact polygon displacement and actual method1 liquid moments",
        affine_feasibility_filter=False, requests_sha256=digest(requests),
        minimum_retained=64 if args.phase == "holdout" else 1,
        required_strata=sorted({label for request in requests for label in labels(request)}))
    path = args.output/args.ship/f"necessary-edges-{args.phase}.json"
    states = []
    if path.exists():
        previous = json.loads(path.read_text())
        if (previous["physical_inputs_start"] != before or previous["sampler_source"] != source
                or previous["sampler_config"]["requests_sha256"] != config["requests_sha256"]):
            raise ValueError("edge checkpoint source/physical inputs changed; choose a fresh output")
        states = previous["states"]
    target = len(requests) if args.limit is None else min(args.limit, len(requests))
    if target < 1:
        raise ValueError("limit must be positive")
    def save():
        after = driver.snapshot(args.ship, family.parameters)
        if after != before or pin(__file__) != source:
            raise ValueError("edge physical/sampler inputs changed during evaluation")
        admitted = [row for row in states if row["admitted"]]
        current_config = dict(config, samples=len(admitted))
        identity = dict(physical_inputs=before, config=current_config,
            builder_version=EDGE_GRID_VERSION, source_sha256=source["sha256"])
        current_coverage = coverage(requests if args.phase == "train" else requests[:len(states)], states)
        current_coverage["required_strata"] = config["required_strata"]
        enough = len(admitted) >= current_config["minimum_retained"]
        complete = ((len(states) == len(requests) if args.phase == "train" else enough)
            and all(current_coverage["strata"].get(label, {}).get("evaluated", 0) > 0
                    for label in config["required_strata"]))
        record = dict(format=1, kind="necessary-domain-edges-v1", ship=args.ship, level="family",
            phase=args.phase, role="grid" if args.phase == "train" else "independent-edge-holdout",
            seed=SEEDS[args.phase], complete=complete, samples=len(admitted),
            attempts=len(states), states=states, list_row_samples=admitted,
            physical_inputs_start=before, physical_inputs_end=after, physical_inputs_unchanged=True,
            band=asdict(family.template.band), domain=domain, station_x_m=family.template.station_x_m.tolist(),
            calibration=diagnostics, sampler_builder_version=EDGE_GRID_VERSION,
            sampler_source_sha256=source["sha256"], sampler_source=source,
            sampler_config=current_config, sampler_inputs_hash=digest(identity),
            reference_transfer_source=pin(args.prepared/args.ship/f"{args.phase}-light.json"),
            coverage=current_coverage, seconds=time.monotonic()-began,
            peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
            warning="Finite synthetic physical-domain edge evidence; no full departure approval or domain-wide proof.")
        driver.write(path, record)
        return record
    for request in requests[len(states):target]:
        states.append(evaluate(request, family, instance, geometry, domain))
        # Checkpoint every point; heavyweight source/reference snapshots are
        # checked at the shard boundary, not paid for once per root iteration.
        # save() still validates fresh content before publishing each snapshot.
        if len(states) % 8 == 0 or len(states) == target:
            record = save()
            print(json.dumps({"ship": args.ship, "phase": args.phase, "attempts": len(states),
                "retained": record["samples"], "seconds": record["seconds"]}), flush=True)
            if args.phase == "holdout" and record["complete"]:
                break
    record = save()
    print(json.dumps({"complete": record["complete"], "coverage": record["coverage"],
                      "seconds": record["seconds"]}), flush=True)
    if args.limit is None and not record["complete"]:
        raise ValueError("edge request cap exhausted before the declared coverage/retention requirement")


if __name__ == "__main__":
    main()
