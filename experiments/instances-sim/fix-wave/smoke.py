"""Bounded production generation/JSON smoke; preserves failed specifications too."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import perf_counter

from stowage.generate import instances
from stowage.generate.cargo import CARGO_GENERATOR_VERSION
from stowage.io import load_instance, save_instance
from stowage.yard.simulator import SIMULATOR_VERSION
from stowage.vessel.fleet_conditions import CONDITION_VERSION

OUTPUT = Path(__file__).resolve().parent


def fingerprint():
    paths = sorted({*Path("src/stowage").rglob("*.py"), *Path("params").rglob("*.toml")})
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main():
    before = fingerprint()
    results = []
    started = perf_counter()
    for size in ("tiny", "small", "medium", "full"):
        config = dict(size=size, seed=1, without_dg=True, max_attempts=3)
        report = instances.GenerationReport()
        shard_start = perf_counter()
        record = {"config": config}
        try:
            instance = instances.generate_simulated_instance(**config, report=report)
        except ValueError as error:
            if not str(error).startswith("screening exhausted"):
                raise
            record.update(status="rejected", error=str(error))
        else:
            assert instance.vessel.hull is not None
            path = save_instance(instance, OUTPUT / f"{size}-seed-1.json")
            assert load_instance(path) == instance
            record.update(status="accepted", instance=path.name,
                          instance_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          n_boxes=len(instance.load_list), json_roundtrip=True,
                          full_checker_certified=False)
        record.update(audit=asdict(report), elapsed_s=perf_counter() - shard_start)
        results.append(record)
        (OUTPUT / f"{size}-seed-1.audit.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(f"{size}: {record['status']}, {record['elapsed_s']:.3f}s", flush=True)
    after = fingerprint()
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    summary = dict(generator=instances.INSTANCES_VERSION, cargo_generator=CARGO_GENERATOR_VERSION,
                   yard_simulator=SIMULATOR_VERSION, vessel_condition=CONDITION_VERSION,
                   source_before=before, source_after=after, source_drift=changed,
                   results=results, elapsed_s=perf_counter() - started,
                   accepted=sum(row["status"] == "accepted" for row in results),
                   rejected=sum(row["status"] == "rejected" for row in results),
                   full_checker_certified=False)
    (OUTPUT / "manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if changed:
        raise SystemExit(f"source drift during smoke; artifacts are diagnostic only: {changed}")


if __name__ == "__main__":
    main()
