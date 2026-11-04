"""Second independent edge qualification; failed v1 holdouts stay untouched.

The original 128 grid requests are evaluated afresh. An additional 512 scrambled
Sobol requests cover interior/deep masses, full trim/heel ranges and mixed tank
fill branches. Seed 54934 is a new untouched holdout; no observation from the
failed 54932 population is read or used for training. The exact construction is
the immutable, checksum-pinned edge_grid.py implementation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import resource
import time

import numpy as np
from scipy.stats import qmc

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VERSION = "2.0"
SEEDS = {"train": 54933, "holdout": 54934}


def base_sampler():
    path = HERE/"edge_grid.py"
    spec = importlib.util.spec_from_file_location("necessary_edges_immutable_v1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def specifications(phase, base):
    if phase not in SEEDS:
        raise ValueError("phase must be train or holdout")
    if phase == "holdout":
        # Reuse the frozen off-grid design with a genuinely new RNG stream.
        # A new module object keeps the v1 producer's process/module immutable.
        base.SEEDS = dict(SEEDS)
        rows = base.specifications("holdout")
    else:
        rows = base.specifications("train")
        # Independent scrambled Sobol coordinates cover every marginal stratum
        # evenly without a random draw's large accidental gaps. They choose
        # physical requests only; every equilibrium uses the original model.
        coordinates = qmc.Sobol(d=7, scramble=True, seed=SEEDS["train"]).random_base2(9)
        # Request index bits are correlated with Sobol coordinates. Independently
        # permute the balanced branch schedule rather than conditioning a fill
        # branch on a fixed low-bit slice of the mass/attitude sequence.
        branches = np.random.default_rng(SEEDS["train"]+100).permutation(512) % 16
        knots = (0., .5, .98-1e-8, .98, .98+1e-8, 1.)
        for i, point in enumerate(coordinates):
            branch = int(branches[i])
            fill_pattern = "uniform" if branch < 6 else "staggered" if branch < 8 else "independent-offgrid"
            rows.append(dict(index=128+i, kind="dense-v2-calibration",
                mass_fraction=float(.75+.25*point[0] if point[6] < .5 else .1+.8*point[0]),
                trim_fraction=float(2*point[1]-1), heel_fraction=float(2*point[2]-1),
                dry_kg_fraction=float(point[3]), fill_fraction=knots[branch % 6]
                    if branch < 8 else float(.001+.998*point[4]),
                fill_pattern=fill_pattern, pattern_offset=i,
                fill_seed=int(1+point[5]*(2**31-2))))
    for index, row in enumerate(rows):
        row["index"] = index
        row.pop("state_id", None)
        row["state_id"] = f"v2-{phase}-{index:03d}-"+base.digest(row)[:16]
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ship", required=True, choices=("feeder_500", "feeder_1000", "feeder_1700"))
    parser.add_argument("--phase", required=True, choices=tuple(SEEDS))
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pilot", action="store_true", help="Only four new calibration requests; always incomplete")
    args = parser.parse_args()
    if args.pilot and args.phase != "train":
        raise ValueError("pilot is a train-only four-request timing measurement")
    base = base_sampler()
    source, base_source = base.pin(__file__), base.pin(HERE/"edge_grid.py")
    requests = specifications(args.phase, base)
    began = time.monotonic()
    driver = base.load_driver()
    family, instance, _, diagnostics, before = driver.prepare(args.ship, args.prepared)
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.linear.family_calibration import domain_for
    geometry, domain = StabilityGeometry(family.parameters), domain_for(family, instance)
    config = dict(ship=args.ship, level="family", phase=args.phase, seed=SEEDS[args.phase],
        attempts=len(requests), requests_sha256=base.digest(requests),
        original_grid_requests=128 if args.phase == "train" else 0,
        additional_calibration_requests=512 if args.phase == "train" else 0,
        sampler_base_source=base_source, independent_failed_holdout_seed_excluded=54932,
        construction="pinned exact polygon displacement and actual method1 liquid moments",
        design="scrambled Sobol7D seed54933 training; PCG64 seed54934 independent holdout",
        affine_feasibility_filter=False, minimum_retained=64 if args.phase == "holdout" else 1,
        required_strata=sorted({label for request in requests for label in base.labels(request)}))
    path = args.output/args.ship/f"necessary-edges-{args.phase}.json"
    states = []
    if path.exists():
        previous = json.loads(path.read_text())
        if (args.pilot or previous.get("pilot") is True
                or previous["physical_inputs_start"] != before or previous["sampler_source"] != source
                or previous.get("sampler_base_source") != base_source
                or previous["sampler_config"]["requests_sha256"] != config["requests_sha256"]):
            raise ValueError("v2 edge checkpoint source/design/physical epoch mismatch; choose a fresh output")
        states = previous["states"]
    selected = requests[128:132] if args.pilot else requests
    target = len(selected) if args.limit is None else min(args.limit, len(selected))
    if target < 1:
        raise ValueError("limit must be positive")

    def save():
        if base.pin(__file__) != source or base.pin(HERE/"edge_grid.py") != base_source:
            raise ValueError("v2 or base edge sampler source changed during measurement")
        after = driver.snapshot(args.ship, family.parameters)
        if after != before:
            raise ValueError("physical/reference epoch changed during v2 edge measurement")
        admitted = [row for row in states if row["admitted"]]
        active_requests = selected if args.pilot else requests if args.phase == "train" else requests[:len(states)]
        coverage = base.coverage(active_requests, states)
        coverage["required_strata"] = config["required_strata"]
        enough = len(admitted) >= config["minimum_retained"]
        complete = (not args.pilot and (len(states) == len(requests) if args.phase == "train" else enough)
            and all(coverage["strata"].get(label, {}).get("evaluated", 0) > 0 for label in config["required_strata"]))
        current_config = dict(config, samples=len(admitted))
        inputs = dict(physical_inputs=before, config=current_config,
            builder_version=VERSION, source_sha256=source["sha256"])
        record = dict(format=1, kind="necessary-domain-edges-v1", ship=args.ship, level="family",
            phase=args.phase, role="grid" if args.phase == "train" else "independent-edge-holdout",
            seed=SEEDS[args.phase], pilot=args.pilot, complete=complete, samples=len(admitted),
            attempts=len(states), states=states, list_row_samples=admitted,
            physical_inputs_start=before, physical_inputs_end=after, physical_inputs_unchanged=True,
            band=asdict(family.template.band), domain=domain, station_x_m=family.template.station_x_m.tolist(),
            calibration=diagnostics, sampler_builder_version=VERSION, sampler_source=source,
            sampler_source_sha256=source["sha256"], sampler_base_source=base_source,
            sampler_config=current_config, sampler_inputs_hash=base.digest(inputs),
            reference_transfer_source=base.pin(args.prepared/args.ship/f"{args.phase}-light.json"),
            coverage=coverage, seconds=time.monotonic()-began,
            peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
            warning="Finite independent second campaign, not a universal bound or full-checker certificate; failed v1 holdouts are not training data.")
        driver.write(path, record)
        return record

    for request in selected[len(states):target]:
        states.append(base.evaluate(request, family, instance, geometry, domain))
        if len(states) % 8 == 0 or len(states) == target:
            record = save()
            print(json.dumps({"ship": args.ship, "phase": args.phase, "attempts": len(states),
                "retained": record["samples"], "seconds": record["seconds"]}), flush=True)
            if args.phase == "holdout" and record["complete"]:
                break
    record = save()
    print(json.dumps({"complete": record["complete"], "samples": record["samples"],
                      "attempts": record["attempts"], "seconds": record["seconds"]}), flush=True)
    if not args.pilot and args.limit is None and not record["complete"]:
        raise ValueError("v2 edge cap exhausted before coverage/retention requirement")


if __name__ == "__main__":
    main()
