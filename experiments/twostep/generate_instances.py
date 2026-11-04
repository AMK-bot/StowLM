"""Regenerate task-4.8 inputs through the production full-hull builder.

No fallback geometry, operational limits or strength stations are invented.
Failure to build the full vessel is recorded as an input-generation failure,
not a failed legal plan. One seeded instance per rung is a smoke cohort only.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import resource
import time

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"
HERE = Path(__file__).resolve().parent
# Caches are disposable outputs and remain within this task's scope fence.
os.environ.setdefault("STOWAGE_VESSEL_CACHE_DIR", str(HERE / "cache" / "vessels"))


def generate(rung: str, seed: int, directory: Path = HERE / "instances") -> dict:
    from stowage.generate.random_yard import RANDOM_YARD_VERSION, RandomGenerationReport, generate_random_instance
    from stowage.io import load_instance, save_instance
    from stowage.versions import component_versions

    start = time.perf_counter()
    directory = directory / rung
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"seed-{seed:08d}.json"
    audit = RandomGenerationReport()
    row = dict(rung=rung, seed=seed, generator="random_yard", generator_version=RANDOM_YARD_VERSION,
               blocking_ratio=0.3, vessel="feeder_500", versions=component_versions(),
               source_parameter_sha256=hashlib.sha256((HERE.parents[1]/"params/fleet/feeder_500.toml").read_bytes()).hexdigest(),
               status="generation_error", instance_path=str(path.relative_to(HERE)))
    try:
        instance = generate_random_instance(rung, seed=seed, blocking_ratio=0.3, report=audit)
        hull = instance.vessel.hull
        if hull is None or not hull.hydrostatics or not hull.cross_curves or not hull.stations:
            raise ValueError("full vessel requires hull, hydrostatics, cross curves and strength stations")
        save_instance(instance, path)
        loaded = load_instance(path)
        if loaded != instance:
            raise ValueError("serialized full-hull instance failed round-trip equality")
        row.update(status="generated", sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   targets=len(instance.load_list), hydrostatic_rows=len(hull.hydrostatics),
                   cross_curves=len(hull.cross_curves), strength_stations=len(hull.stations),
                   hull_offsets_ref=hull.hull_offsets_ref,
                   source_parameter_sha256=hashlib.sha256((HERE.parents[1]/"params/fleet/feeder_500.toml").read_bytes()).hexdigest())
    except Exception as error:
        row.update(error=f"{type(error).__name__}: {error}")
    row.update(audit=asdict(audit), seconds=time.perf_counter()-start,
               peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/2**20)
    path.with_suffix(".audit.json").write_text(json.dumps(row, indent=2, sort_keys=True)+"\n")
    return row


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rungs", nargs="+", choices=("tiny", "small", "medium", "full"), default=["tiny", "small", "medium", "full"])
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rows=[]
    for rung in args.rungs:
        row=generate(rung, args.seed)
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
        (HERE/"instances/manifest.json").write_text(json.dumps(rows, indent=2, sort_keys=True)+"\n")


if __name__ == "__main__":
    main()
