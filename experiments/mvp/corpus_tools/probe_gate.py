"""One-core, bounded public-producer measurements for the switch-OFF corpus.

Run one case per process so random-v2's scoped producer overrides never overlap.
Only probe measurements are emitted; this does not emit corpus instances.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
from time import perf_counter
import traceback

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"


class ProbeTimeout(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=("simulated_yard", "random_yard"))
    parser.add_argument("rung", choices=("tiny", "small", "medium", "full"))
    parser.add_argument("ship", choices=("feeder_500", "feeder_800", "feeder_1700"))
    parser.add_argument("--seed", type=int, default=900_000_001)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from stowage.generate.instances import GenerationReport, generate_simulated_instance
    from stowage.generate.instances_v2_random import generate_random_v2
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.versions import component_versions

    assert rule_freeze_v1_enabled() is False, "probe requires the frozen demo OFF path"
    report = GenerationReport()
    source_files = sorted(Path("src/stowage/generate").glob("*.py")) + [
        Path("src/stowage/versions.py"), Path("src/stowage/rules/catalogue.py")]
    record = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "spec": {"family": args.family, "rung": args.rung, "ship": args.ship,
                 "seed": args.seed, "max_attempts": args.max_attempts,
                 "timeout_s": args.timeout, "n_boxes": None,
                 "port": "laem_chabang_like", "noise": 0.8},
        "rule_freeze_v1_enabled": False,
        "component_versions": component_versions(),
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
        "resource_limits": {"native_threads": 1, "gpu": False},
    }
    def timed_out(_signal, _frame):
        raise ProbeTimeout(f"probe exceeded {args.timeout} s")
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(args.timeout)
    started = perf_counter()
    try:
        producer = generate_simulated_instance if args.family == "simulated_yard" else generate_random_v2
        instance = producer(args.rung, seed=args.seed, noise=0.8, vessel_name=args.ship,
                            port_name="laem_chabang_like", report=report,
                            max_attempts=args.max_attempts, n_boxes=None,
                            tiny_dg_enabled=True)
        target = set(instance.load_list)
        cargo = [c for c in instance.containers if c.id in target]
        record["emitted"] = {"id": instance.id, "load_boxes": len(instance.load_list),
            "total_boxes": len(instance.containers), "cells": len(instance.vessel.cells),
            "teu": sum(c.length_ft / 20 for c in cargo),
            "dg_boxes": sum(c.dg is not None for c in cargo),
            "reefer_boxes": sum(c.reefer for c in cargo),
            "schema_version": instance.schema_version, "family": instance.family,
            "provenance_versions": instance.provenance.versions}
        record["success"] = True
    except Exception as error:
        record.update(success=False, error_type=type(error).__name__, error=str(error),
                      traceback=traceback.format_exc())
    finally:
        signal.alarm(0)
        record["elapsed_s"] = perf_counter() - started
        # macOS ru_maxrss is bytes (this probe runs on the project's Mac).
        record["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        record["report"] = asdict(report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: record[key] for key in ("success", "elapsed_s", "peak_rss_bytes")} | {
        "output": str(args.output), "error": record.get("error"),
        "background_checks": report.background_checks,
        "background_seconds": report.background_seconds, "attempts": report.attempts}))
    return 0 if record["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
