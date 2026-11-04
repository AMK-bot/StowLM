"""Fresh hull-bearing solver inputs, using the production synthetic generators.

The standard four rungs preserve random_yard's cargo distribution and screening.
Two additional full-ship workloads use 450 and 900 target boxes. A box is not a
TEU: a forty-foot box consumes two twenty-foot cells. Thus these deliberately
20-foot-dominant demand scenarios fit the nominal feeder capacities, whereas
the unconditioned profile's majority forty-foot mix cannot fit 900 boxes on a
1,000-TEU ship. Equipment probabilities are explicit synthetic conditioning,
not measured trade frequencies. No vessel limit or checker rule is changed.

APPROXIMATION: full-feeder demand is 88% dry20, 2% reefer20, 7% dry40,
2% high-cube40, 1% reefer high-cube40 by box. Cargo generation retains the
original loaded/empty, mass, POD, DG and special-stowage distributions.
Source: params/cargo/feeder_export.toml and generate.random_yard docstrings.

Only this process's generator bindings are temporarily patched; no production
files or cached hull fingerprints are modified. Missing current hull data is a
recorded generation failure, never an excuse to load an obsolete hull cache.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

# Keep numerical libraries inside this single-core generation reservation.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

from stowage.generate import random_yard as generator
from stowage.io import load_instance, save_instance
from stowage.params import load_param_file


PROFILES = {
    "tiny": ("tiny", "feeder_500", None),
    "small": ("small", "feeder_500", None),
    "medium": ("medium", "feeder_500", None),
    "full": ("full", "feeder_500", None),
    "feeder_500_450": ("full", "feeder_500", 450),
    "feeder_800_900": ("full", "feeder_800", 900),
}
EQUIPMENT_SHARES = {
    "dry_20": .88, "reefer_20": .02, "dry_40": .07,
    "high_cube_40": .02, "reefer_high_cube_40": .01,
}


def generate(profile: str, *, seed: int, blocking_ratio: float = .3):
    """Return a production Instance and the complete demand-conditioning audit."""
    size, vessel, count = PROFILES[profile]
    report = generator.RandomGenerationReport()
    if count is None:
        instance = generator.generate_random_instance(
            size, seed=seed, vessel_name=vessel, blocking_ratio=blocking_ratio,
            report=report)
    else:
        params = deepcopy(load_param_file("cargo", "feeder_export"))
        params["equipment"] = [e for e in params["equipment"]
                               if e["id"] in EQUIPMENT_SHARES]
        for row in params["equipment"]:
            row["share"]["value"] = EQUIPMENT_SHARES[row["id"]]
        # APPROXIMATION: each target retains its own ground footprint, as in
        # random_yard. Add ground space so relocations remain possible at 900.
        rung = generator.SizeRung(count, 8 if count > 500 else 5, 32, 8, 2)
        with patch.dict(generator.SIZE_LADDER, {"full": rung}), patch.object(
                generator, "load_param_file", return_value=params):
            instance = generator.generate_random_instance(
                "full", seed=seed, vessel_name=vessel,
                blocking_ratio=blocking_ratio, report=report)
        report.conditioning.append(
            "APPROXIMATION: explicit full-feeder equipment probabilities by box: "
            + json.dumps(EQUIPMENT_SHARES, sort_keys=True))
        instance.id = f"scalable-fresh.{profile}.{seed}"
        instance.family["demand_profile"] = profile
        instance.family["equipment_conditioning"] = json.dumps(
            EQUIPMENT_SHARES, sort_keys=True)
    hull = instance.vessel.hull
    if hull is None or not hull.stations or not hull.hydrostatics:
        raise ValueError("checker.missing_vessel_data: hull/stations/hydrostatics absent")
    boxes = [b for b in instance.containers if b.id in set(instance.load_list)]
    audit = {
        **asdict(report), "screening_rate": report.screening_rate,
        "profile": profile, "seed": seed,
        "target_teu": sum(b.length_ft / 20 for b in boxes),
        "target_mass_kg": sum(b.gross_mass_kg for b in boxes),
        "equipment_counts": dict(Counter(b.iso_type for b in boxes)),
        "length_counts": dict(Counter(b.length_ft for b in boxes)),
        "reefers": sum(b.reefer for b in boxes),
        "dg": sum(b.dg is not None for b in boxes),
        "vessel_cells": len(instance.vessel.cells),
        "vessel_stations": len(hull.stations),
        "vessel_hydrostatic_rows": len(hull.hydrostatics),
        "missing_vessel_data_structure": False,
        "checker_passed": None,
        "checker_note": "Structural data audit only; solver/finalize/full checker required.",
        "provenance_tainted": instance.provenance.tainted,
    }
    return instance, audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--blocking-ratio", type=float, default=.3)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).parent / "instances")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for profile in args.profiles:
        for seed in range(args.seed, args.seed + args.count):
            started = perf_counter()
            size, vessel, boxes = PROFILES[profile]
            param_path = Path("params/fleet") / f"{vessel}.toml"
            row = {"profile": profile, "seed": seed, "vessel": vessel,
                   "rung": size, "requested_boxes": boxes or generator.SIZE_LADDER[size].boxes,
                   "requested_blocking_ratio": args.blocking_ratio,
                   "fleet_params_sha256": sha256(param_path.read_bytes()).hexdigest(),
                   "equipment_probabilities_conditioned": EQUIPMENT_SHARES if boxes else None}
            try:
                instance, audit = generate(profile, seed=seed,
                                           blocking_ratio=args.blocking_ratio)
                folder = args.output / profile
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"seed-{seed:08d}.json"
                save_instance(instance, path)
                # Validate the exact serialized input used by the benchmark.
                loaded = load_instance(path)
                assert loaded == instance
                row.update(status="generated", path=str(path), **audit)
                row["generation_s"] = perf_counter() - started
                path.with_suffix(".audit.json").write_text(
                    json.dumps(row, indent=2, sort_keys=True) + "\n")
            except (ValueError, OSError) as exc:
                row.update(status="blocked", error=str(exc),
                           instance_written=False, checker_passed=None,
                           generation_s=perf_counter() - started)
            rows.append(row)
            print(json.dumps(row, sort_keys=True), flush=True)
            # Checkpoint failures too: interrupted generation is reviewable.
            (args.output / "generation-audit.json").write_text(
                json.dumps(rows, indent=2, sort_keys=True) + "\n")
    return int(any(r["status"] != "generated" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
