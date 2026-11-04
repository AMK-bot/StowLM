"""Prepare one current tiny epoch for a diagnostic gold run, never evidence.

Calls the production v2 regeneration helpers directly, so this does not claim
that the central qualification preflight passed. Background acceptance, physics
and instance identity checks are unchanged. Every write stays under gold-plans.
"""
from __future__ import annotations

import argparse
import json
from eval.snapshot_io import read_snapshot
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
G1 = ROOT / "experiments/campaign-g1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(G1))
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "diagnostic-1028")
    parser.add_argument("--generation-s", type=float, default=60.)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from freeze import atomic_json, check_manifest, create_manifest
    import regen
    if args.worker:
        # Reuse path redirection only; the upstream adapter normally fences its
        # own campaign-g1 directory. No source or central cache is edited here.
        import cache_adapter
        cache_adapter.HERE = HERE
        job = read_snapshot(args.worker).json()
        result = regen._worker(job)
        atomic_json(args.worker.with_name("result.json"), result)
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
        return 0
    output = args.output.resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        parser.error("output must be a dedicated campaign subdirectory")
    if not 0 < args.generation_s <= 120 or not 1 <= args.max_attempts <= 20:
        parser.error("generation-s must be (0,120], max-attempts 1..20")
    output.mkdir(parents=True, exist_ok=True)
    freeze_path = output / "freeze.json"
    if not freeze_path.exists():
        atomic_json(freeze_path, create_manifest(diagnostic=True))
    frozen = check_manifest(freeze_path, diagnostic=True, allow_drift=True)
    epoch = frozen["epoch_id"]
    release = regen.build_release(epoch, diagnostic=True)
    entry = next(row for row in release["entries"] if row["split"] == "train"
                 and row["spec"]["rung"] == "tiny" and row["spec"]["generator"] == "simulated"
                 and row["spec"]["vessel"] == "feeder_500"
                 and row["spec"]["port"] == "laem_chabang_like")
    job = dict(kind="release", root=str(regen.instance_root(output / "release", epoch)),
               config=regen._release_config(entry, args.max_attempts), allocated=entry,
               cache_output=str(output / "cache"))
    job["id"] = "diagnostic-tiny-" + regen._digest(job)[:20]
    folder = output / "generation"
    folder.mkdir(exist_ok=True)
    marker = folder / "done.json"
    started = time.perf_counter()
    record = regen.load_marker(marker, job, epoch, True)
    resumed = record is not None
    if record is None:
        atomic_json(folder / "job.json", job)
        with (folder / "worker.log").open("w") as stream:
            child = subprocess.Popen([sys.executable, __file__, "--worker", str(folder / "job.json")],
                                     cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            atomic_json(folder / "pid.json", dict(pid=child.pid, owner_pid=os.getpid()))
            try:
                code = child.wait(timeout=args.generation_s)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                atomic_json(output / "result.json", dict(diagnostic=True, label="diagnostic, not evidence",
                    accepted=0, attempted=1, status="generation_timeout", limit_s=args.generation_s,
                    wall_s=time.perf_counter()-started, child_pid=child.pid))
                return 1
        if code:
            raise RuntimeError(f"diagnostic generator failed ({code}); see {folder / 'worker.log'}")
        record = read_snapshot(folder / "result.json").json()
        regen._commit(record, marker, epoch, True)
    index = regen._write_indices([record], release, output / "release", epoch, True, frozen)
    from eval.splits import load_inventory
    from eval.splits_epoch import validate_epoch_instance, validate_epoch_manifest
    root = Path(job["root"])
    validate_epoch_manifest(root, index, load_inventory(), allow_diagnostic=True, check_source=False)
    for group, value in index["groups"].items():
        for row in value["shards"]:
            validate_epoch_instance(root / group / row["instance"], row)
    result = dict(diagnostic=True, label="diagnostic, not evidence", admitted=False,
                  source_preflight_complete=False, freeze_manifest=str(freeze_path),
                  instance_root=str(root), accepted=index["count"], rejected=index["rejected"],
                  attempted=1, resumed=resumed, wall_s=time.perf_counter()-started,
                  generation_wall_s=record["elapsed_s"], peak_rss_gb=record["peak_rss_gb"])
    atomic_json(output / "result.json", result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
