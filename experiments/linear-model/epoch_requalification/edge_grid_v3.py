"""Deterministic interior-mass faces, with immutable prior TRAIN lineage.

V1/v2 failed holdouts are never read. The v2 training observations retain their
original state dictionaries and checksum-pinned parent record. New observations
use the unchanged exact-attitude construction in pinned edge_grid.py. A fresh
54936 holdout tests the resulting envelope independently.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.util
from itertools import product
import json
from pathlib import Path
import resource
import time
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VERSION = "3.0"
SEEDS = {"train": 54935, "holdout": 54936}
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")


def base_sampler():
    spec = importlib.util.spec_from_file_location("v3_pinned_exact_edge_sampler", HERE/"edge_grid.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def specifications(phase, base):
    if phase == "train":
        patterns = [("uniform", x, None) for x in (0., .5, .98, 1.)]
        patterns += [("declared-budget", fraction, pattern) for fraction, pattern in (
            (.2, "uniform"), (.5, "uniform"), (.8, "uniform"),
            (.3, "aft-weighted"), (.6, "forward-weighted"), (.9, "staggered"))]
        rows = []
        for i, (mass, trim, heel, kg, (pattern, fill, budget_pattern)) in enumerate(product(
                tuple(i/10 for i in range(1, 11)), (-1., 1.), (-1., 1.), (0., 1.), patterns)):
            rows.append(dict(index=i, kind="v3-interior-mass-face", mass_fraction=mass,
                trim_fraction=trim, heel_fraction=heel, dry_kg_fraction=kg,
                fill_fraction=fill, fill_pattern=pattern, pattern_offset=i))
            if budget_pattern is not None:
                rows[-1].update(tank_budget_fraction=fill, tank_budget_pattern=budget_pattern)
    elif phase == "holdout":
        base.SEEDS = dict(SEEDS)  # Private imported module only; base file is immutable.
        rows = base.specifications("holdout")
    else:
        raise ValueError("phase must be train or holdout")
    for i, row in enumerate(rows):
        row["index"] = i
        row.pop("state_id", None)
        row["state_id"] = f"v3-{phase}-{i:03d}-"+base.digest(row)[:16]
    return rows


def budget_masses(capacities, weights, requested_mass):
    """Allocate a declared sampling budget over real capacity bounds.

    This selects an experimental fill profile; it does not change a requested
    uniform .5/.98/1 corner. Capacity-limited budget requests explicitly record
    their requested and realized totals rather than silently reweighting them.
    """
    capacities, weights = np.asarray(capacities, dtype=float), np.asarray(weights, dtype=float)
    if (capacities.shape != weights.shape or np.any(capacities <= 0) or np.any(weights <= 0)
            or not np.isfinite(capacities).all() or not np.isfinite(weights).all()
            or not np.isfinite(requested_mass) or requested_mass < 0):
        raise ValueError("invalid declared tank-budget inputs")
    target = min(float(requested_mass), float(capacities.sum()))
    result = np.zeros_like(capacities)
    free = np.ones(len(capacities), dtype=bool)
    remaining = target
    while free.any() and remaining > 0:
        proposed = remaining*weights[free]/weights[free].sum()
        indices = np.flatnonzero(free)
        capped = proposed >= capacities[indices]
        if not capped.any():
            result[indices] = proposed
            break
        result[indices[capped]] = capacities[indices[capped]]
        free[indices[capped]] = False
        remaining = max(0., target-float(result.sum()))
    return result


def materialize(ctx, request):
    request = dict(request)
    capacities = ctx.family.template.tank_capacity_kg
    if request["fill_pattern"] != "declared-budget":
        fractions = ctx.original_fractions(request, len(capacities))
        values = fractions*capacities
    else:
        band = ctx.family.template.band
        mass = band.displacement_min_kg+request["mass_fraction"]*(band.displacement_max_kg-band.displacement_min_kg)
        available = max(0., mass-ctx.instance.vessel.hull.lightship_mass_kg)
        desired = request["tank_budget_fraction"]*available
        pattern = request["tank_budget_pattern"]
        positions = ctx.family.template.tank_lcg_m
        extent = max(float(np.ptp(positions)), 1.)
        along = (positions-float(np.min(positions)))/extent
        if pattern == "uniform":
            factors = np.ones(len(capacities))
        elif pattern == "aft-weighted":
            factors = .25+1.5*(1-along)
        elif pattern == "forward-weighted":
            factors = .25+1.5*along
        else:
            factors = np.where((np.arange(len(capacities))+request["index"]) % 2, .1, 1.9)
        values = budget_masses(capacities, capacities*factors, desired)
        fractions = values/capacities
        values = fractions*capacities  # Identical arithmetic to the exact evaluator's input.
        request.update(available_mass_above_lightship_kg=available,
            requested_tank_budget_kg=desired, realized_tank_budget_kg=float(values.sum()),
            budget_capacity_limited=bool(desired > capacities.sum()))
    request["actual_tank_fractions"] = fractions.tolist()
    request["actual_all_tank_masses_kg"] = values.tolist()
    return request


def balanced_point_exact_z(original, displacement, buoyancy_cg, normal, base_mass, base_cg, dry_z):
    dry, point = original(displacement, buoyancy_cg, normal, base_mass, base_cg, dry_z)
    # z is the prescribed independent coordinate, not an inferred quotient.
    # Set that exact input before the fresh equilibrium; old states are never
    # clipped, corrected or relabelled by this numerical construction identity.
    point[2] = dry_z
    return dry, point


def context(ship, prepared):
    began = time.monotonic()
    base = base_sampler()
    source, base_source = base.pin(__file__), base.pin(HERE/"edge_grid.py")
    driver = base.load_driver()
    family, instance, _, diagnostics, before = driver.prepare(ship, prepared)
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.linear.family_calibration import domain_for
    ctx = SimpleNamespace(ship=ship, prepared=prepared, began=began, base=base,
        source=source, base_source=base_source, driver=driver, family=family,
        instance=instance, diagnostics=diagnostics, before=before,
        geometry=StabilityGeometry(family.parameters), domain=domain_for(family, instance))
    ctx.original_fractions = base.tank_fractions
    original_point = base.balanced_point
    def declared_fractions(request, count):
        values = np.asarray(request["actual_tank_fractions"], dtype=float)
        if values.shape != (count,) or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
            raise ValueError("invalid explicit tank profile")
        return values
    base.tank_fractions = declared_fractions
    base.balanced_point = lambda *args: balanced_point_exact_z(original_point, *args)
    return ctx


def checked_inputs(ctx):
    if ctx.base.pin(__file__) != ctx.source or ctx.base.pin(HERE/"edge_grid.py") != ctx.base_source:
        raise ValueError("v3/base sampler source changed during measurement")
    after = ctx.driver.snapshot(ctx.ship, ctx.family.parameters)
    if after != ctx.before:
        raise ValueError("numerical/reference inputs changed during v3 edge measurement")
    return after


def write_record(ctx, path, phase, states, config, coverage, *, complete, pilot=False,
                 prior_training=(), lattice_shards=()):
    after = checked_inputs(ctx)
    admitted = [state for state in states if state["admitted"]]
    config = dict(config, ship=ctx.ship, level="family", phase=phase, seed=SEEDS[phase],
        samples=len(admitted), sampler_base_source=ctx.base_source,
        sampler_dependencies=[ctx.base_source], prior_training=list(prior_training),
        lattice_shards=list(lattice_shards), forbidden_failed_holdout_seeds=[54932, 54934],
        tank_profile_algorithm="explicit mass-budget water filling; nominal uniform corners never rescaled",
        dry_point_z_construction="exact prescribed dry_z before fresh equilibrium",
        tank_capacity_kg=ctx.family.template.tank_capacity_kg.tolist(),
        materialized_profiles=[{key: state["request"][key] for key in (
            "state_id", "actual_tank_fractions", "requested_tank_budget_kg", "realized_tank_budget_kg")
            if key in state["request"]} for state in states if "actual_tank_fractions" in state["request"]])
    inputs = dict(physical_inputs=ctx.before, config=config,
        builder_version=VERSION, source_sha256=ctx.source["sha256"])
    record = dict(format=1, kind="necessary-domain-edges-v1", ship=ctx.ship, level="family",
        phase=phase, role="grid" if phase == "train" else "independent-edge-holdout",
        seed=SEEDS[phase], complete=complete, pilot=pilot, samples=len(admitted),
        attempts=len(states), states=states, list_row_samples=admitted,
        physical_inputs_start=ctx.before, physical_inputs_end=after, physical_inputs_unchanged=True,
        band=asdict(ctx.family.template.band), domain=ctx.domain,
        station_x_m=ctx.family.template.station_x_m.tolist(), calibration=ctx.diagnostics,
        sampler_builder_version=VERSION, sampler_source=ctx.source,
        sampler_source_sha256=ctx.source["sha256"], sampler_base_source=ctx.base_source,
        sampler_dependencies=[ctx.base_source], sampler_config=config,
        sampler_inputs_hash=ctx.base.digest(inputs), prior_training=list(prior_training),
        lattice_shards=list(lattice_shards), coverage=coverage,
        reference_transfer_source=ctx.base.pin(ctx.prepared/ctx.ship/f"{phase}-light.json"),
        seconds=time.monotonic()-ctx.began,
        peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
        warning="Finite independent third qualification. Prior TRAIN states retain original identities; failed holdouts are never enrichment; no complete-departure or uniform-bound claim.")
    ctx.driver.write(path, record)
    return record


def valid_record(ctx, record):
    if (record.get("complete") is not True or record.get("physical_inputs_start") != ctx.before
            or record.get("physical_inputs_end") != ctx.before or record.get("physical_inputs_unchanged") is not True):
        raise ValueError("incomplete/stale source edge population")
    for name in ("family_calibration_cache_key", "response_calibration_identity", "reference_identity", "family_domain"):
        if record["calibration"].get(name) != ctx.diagnostics.get(name):
            raise ValueError("prior edge calibration mismatch: "+name)
    for key in ("sampler_source", "sampler_base_source"):
        pin = record[key]
        if ctx.base.pin(ROOT/pin["path"]) != pin:
            raise ValueError("prior sampler source checksum mismatch")
    config = record["sampler_config"]
    inputs = dict(physical_inputs=record["physical_inputs_start"], config=config,
        builder_version=record["sampler_builder_version"], source_sha256=record["sampler_source"]["sha256"])
    if (record.get("sampler_inputs_hash") != ctx.base.digest(inputs)
            or config.get("sampler_base_source") != record["sampler_base_source"]
            or record.get("sampler_source_sha256") != record["sampler_source"]["sha256"]
            or record["attempts"] != len(record["states"])
            or record["samples"] != sum(state["admitted"] for state in record["states"])
            or record["list_row_samples"] != [state for state in record["states"] if state["admitted"]]):
        raise ValueError("prior sampler input hash or observation counts mismatch")


def shard_path(output, ship, index, count):
    return output/ship/f"necessary-lattice-{index:02d}-of-{count:02d}.json"


def lattice(ctx, output, *, index, count, pilot=False, limit=None):
    requests = [materialize(ctx, request) for request in specifications("train", ctx.base)]
    selected = [requests[i] for i in (84, 175, 276, 377, 478, 579, 675, 796)] if pilot else requests[index::count]
    target = len(selected) if limit is None else min(limit, len(selected))
    path = output/ctx.ship/"necessary-lattice-pilot.json" if pilot else shard_path(output, ctx.ship, index, count)
    config = dict(requests_sha256=ctx.base.digest(requests), attempts=len(selected),
        new_lattice_attempts=800, shard_index=index, shard_count=count,
        sampler_stage="lattice-shard", training_seeds=[SEEDS["train"]],
        affine_feasibility_filter=False, construction="pinned original exact attitude/force/moment solver",
        required_strata=sorted({label for request in selected for label in ctx.base.labels(request)}))
    states = []
    if path.exists():
        old = json.loads(path.read_text())
        if (pilot or old.get("pilot") or old["sampler_source"] != ctx.source
                or old.get("sampler_base_source") != ctx.base_source
                or old["physical_inputs_start"] != ctx.before
                or old["sampler_config"]["requests_sha256"] != config["requests_sha256"]
                or old["sampler_config"]["shard_count"] != count
                or old["sampler_config"]["shard_index"] != index):
            raise ValueError("v3 shard checkpoint source/design/physical epoch mismatch")
        states = old["states"]
    for request in selected[len(states):target]:
        states.append(ctx.base.evaluate(request, ctx.family, ctx.instance, ctx.geometry, ctx.domain))
        if len(states) % 16 == 0 or len(states) == target:
            coverage = ctx.base.coverage(selected, states)
            coverage["required_strata"] = config["required_strata"]
            record = write_record(ctx, path, "train", states, config, coverage,
                complete=not pilot and len(states) == len(selected), pilot=pilot)
            print(json.dumps(dict(ship=ctx.ship, shard=index, attempts=len(states),
                retained=record["samples"], seconds=record["seconds"])), flush=True)
    return path


def merge_training(ctx, output, prior_root, *, count):
    prior_path = prior_root/ctx.ship/"necessary-edges-train.json"
    prior = json.loads(prior_path.read_text())
    valid_record(ctx, prior)
    if (prior.get("phase") != "train" or prior.get("role") != "grid"
            or prior.get("seed") != 54933 or prior.get("sampler_builder_version") != "2.0"):
        raise ValueError("only unchanged v2 TRAIN evidence may enter v3 lineage")
    prior_pin = ctx.base.pin(prior_path)
    requests = [materialize(ctx, request) for request in specifications("train", ctx.base)]
    new_states = {}
    shard_pins = []
    for index in range(count):
        path = shard_path(output, ctx.ship, index, count)
        shard = json.loads(path.read_text())
        valid_record(ctx, shard)
        if (shard.get("sampler_builder_version") != VERSION or shard["sampler_source"] != ctx.source
                or shard["sampler_base_source"] != ctx.base_source
                or shard["sampler_config"].get("requests_sha256") != ctx.base.digest(requests)
                or shard["sampler_config"].get("shard_index") != index
                or shard["sampler_config"].get("shard_count") != count):
            raise ValueError("v3 lattice shard identity mismatch")
        expected = requests[index::count]
        if [row["request"] for row in shard["states"]] != expected:
            raise ValueError("v3 shard omitted/replaced a deterministic lattice request")
        for row in shard["states"]:
            if row["state_id"] in new_states:
                raise ValueError("duplicate v3 lattice observation")
            new_states[row["state_id"]] = row
        shard_pins.append(ctx.base.pin(path))
    if len(new_states) != 800:
        raise ValueError("v3 lattice requires exactly 800 freshly evaluated requests")
    ordered = [new_states[request["state_id"]] for request in requests]
    # Preserve original prior state dicts/IDs byte-for-byte at the JSON-value
    # level; ancestry is recorded on this new merged record, not retagged onto
    # old observations. No v1/v2 holdout record is opened here.
    states = prior["states"]+ordered
    lattice_coverage = ctx.base.coverage(requests, ordered)
    strata = {"prior-v2:"+key: value for key, value in prior["coverage"]["strata"].items()}
    strata.update({"lattice:"+key: value for key, value in lattice_coverage["strata"].items()})
    coverage = dict(strata=strata, required_strata=sorted(strata), requested=len(states),
        evaluated=len(states), retained=sum(state["admitted"] for state in states),
        exact_evaluated=sum("exact_gm_m" in state for state in states),
        interpretation="explicit unchanged prior TRAIN lineage plus 800 new deterministic physical requests")
    config = dict(attempts=len(states), requests_sha256=ctx.base.digest(requests),
        new_lattice_attempts=800, prior_training_attempts=prior["attempts"],
        prior_training_samples=prior["samples"], prior_training_states_sha256=ctx.base.digest(prior["states"]),
        training_seeds=[54933, SEEDS["train"]], sampler_stage="merged-training",
        affine_feasibility_filter=False, required_strata=sorted(strata))
    path = output/ctx.ship/"necessary-edges-train.json"
    if path.exists():
        old = json.loads(path.read_text())
        valid_record(ctx, old)
        if (old.get("sampler_source") != ctx.source or old.get("prior_training") != [prior_pin]
                or old.get("lattice_shards") != shard_pins or old.get("states") != states):
            raise ValueError("existing v3 merged training differs; choose a fresh output")
        return
    record = write_record(ctx, path, "train", states, config, coverage,
        complete=True, prior_training=[prior_pin], lattice_shards=shard_pins)
    if ctx.base.pin(prior_path) != prior_pin:
        raise ValueError("prior training record changed while merging")
    print(json.dumps(dict(ship=ctx.ship, merged_training_attempts=record["attempts"],
        retained=record["samples"], seconds=record["seconds"])), flush=True)


def holdout(ctx, output, *, limit=None):
    requests = [materialize(ctx, request) for request in specifications("holdout", ctx.base)]
    path = output/ctx.ship/"necessary-edges-holdout.json"
    config = dict(attempts=len(requests), requests_sha256=ctx.base.digest(requests),
        minimum_retained=64, training_seeds=[], sampler_stage="independent-holdout",
        affine_feasibility_filter=False,
        required_strata=sorted({label for request in requests for label in ctx.base.labels(request)}))
    states = []
    if path.exists():
        old = json.loads(path.read_text())
        if (old["sampler_source"] != ctx.source or old["sampler_base_source"] != ctx.base_source
                or old["physical_inputs_start"] != ctx.before
                or old["sampler_config"]["requests_sha256"] != config["requests_sha256"]):
            raise ValueError("v3 holdout checkpoint source/design/epoch mismatch")
        states = old["states"]
        if old.get("complete") is True:
            valid_record(ctx, old)
            return
    target = len(requests) if limit is None else min(limit, len(requests))
    complete = False
    for request in requests[len(states):target]:
        states.append(ctx.base.evaluate(request, ctx.family, ctx.instance, ctx.geometry, ctx.domain))
        if len(states) % 8 == 0 or len(states) == target:
            coverage = ctx.base.coverage(requests[:len(states)], states)
            coverage["required_strata"] = config["required_strata"]
            complete = (sum(state["admitted"] for state in states) >= 64
                and set(coverage["strata"]) == set(config["required_strata"]))
            record = write_record(ctx, path, "holdout", states, config, coverage, complete=complete)
            print(json.dumps(dict(ship=ctx.ship, holdout_attempts=len(states),
                retained=record["samples"], seconds=record["seconds"])), flush=True)
            if complete:
                break
    if limit is None and not complete:
        raise ValueError("v3 holdout cap exhausted before 64 retained/all strata")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ship", choices=SHIPS, required=True)
    parser.add_argument("--phase", choices=("lattice", "finalize", "holdout"), required=True)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prior-training-root", type=Path)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid lattice shard index/count")
    if args.pilot and args.phase != "lattice":
        raise ValueError("pilot is lattice-only and never qualification")
    ctx = context(args.ship, args.prepared)
    if args.phase == "lattice":
        lattice(ctx, args.output, index=args.shard_index, count=args.shard_count,
                pilot=args.pilot, limit=args.limit)
    else:
        if args.phase == "finalize":
            if args.prior_training_root is None:
                raise ValueError("finalization requires explicit prior TRAIN root")
            merge_training(ctx, args.output, args.prior_training_root, count=args.shard_count)
        holdout(ctx, args.output, limit=args.limit)


if __name__ == "__main__":
    main()
