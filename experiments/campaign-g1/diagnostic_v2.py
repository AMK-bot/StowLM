"""Probe v2 generation/bindings while keeping regeneration admission strict.

The normal regen CLI is attempted first. --probe independently exercises the
generator helpers if qualification blocks that CLI; it never creates an admitted
regen manifest or evidence. Physics and D58 acceptance are never bypassed.
"""
from __future__ import annotations

from eval.snapshot_io import read_snapshot

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True


def environment():
    result = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        result[key] = "1"
    return result


def snapshot(output):
    from freeze import atomic_json, check_manifest, create_manifest
    target = output / "diagnostic-source"
    stamp = target / "snapshot.json"
    if not stamp.exists():
        started = time.perf_counter()
        target.mkdir(parents=True, exist_ok=True)
        for relative in ("src", "params", "data", "pyproject.toml", "uv.lock",
                         "experiments/stability", "experiments/linear-model",
                         "experiments/splits/v2", "experiments/physics-review/design",
                         "models/glimmer-30b-4bit/tokenizer.json"):
            source, destination = ROOT / relative, target / relative
            if not source.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_dir():
                shutil.rmtree(destination)
            elif destination.exists():
                destination.unlink()
            if sys.platform == "darwin":
                subprocess.run(["/bin/cp", "-cR", str(source), str(destination)], check=True)
            elif source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        atomic_json(stamp, {**create_manifest(target, diagnostic=True),
            "copy_wall_s": time.perf_counter()-started,
            "purpose": "diagnostic helper probe, not admitted regeneration"})
    check_manifest(stamp, diagnostic=True)
    return target


def worker(args):
    from freeze import atomic_json
    def interrupted(*_):
        raise TimeoutError("owned diagnostic worker interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        import regen
        job = read_snapshot(args.worker_job).json()
        record = regen._worker(job)
        atomic_json(args.worker_output, record)
        return 0
    except Exception as exc:
        atomic_json(args.worker_output, dict(error=f"{type(exc).__name__}: {exc}",
            diagnostic=True, admitted=False))
        return 1
    finally:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=HERE / "diagnostic-v2-freeze.json")
    parser.add_argument("--probe", action="store_true", help="independent diagnostic helpers; never waives regen preflight")
    parser.add_argument("--worker-job", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--timeout-s", type=float, default=90.)
    parser.add_argument("--tranche", help="probe the cumulative tranche interfaces")
    parser.add_argument("--train-per-rung")
    parser.add_argument("--diagnostic-eval-per-rung", type=int, default=0)
    parser.add_argument("--max-attempts", type=int, default=1)
    args = parser.parse_args(argv)
    if args.worker_job:
        return worker(args)
    if bool(args.tranche) != bool(args.train_per_rung):
        parser.error("--tranche and --train-per-rung must be supplied together")
    from freeze import atomic_json, create_manifest, load_manifest
    manifest_path = args.manifest.resolve()
    if not manifest_path.is_relative_to(HERE):
        parser.error("manifest must be inside campaign-g1")
    if not manifest_path.exists():
        atomic_json(manifest_path, create_manifest(diagnostic=True))
    frozen = load_manifest(manifest_path)
    if frozen.get("diagnostic") is not True:
        parser.error("diagnostic epochs only")
    output = HERE / frozen["epoch_id"] / ("tranche-probe" if args.tranche else "v2-probe")
    output.mkdir(parents=True, exist_ok=True)
    env = environment()
    command = [sys.executable, str(HERE / "regen.py"), "--manifest", str(manifest_path),
        "--diagnostic", "--allow-drift", "--workers", "1", "--max-attempts", str(args.max_attempts), "--count", "2"]
    tranche_args = (["--tranche", args.tranche, "--train-per-rung", args.train_per_rung,
        "--diagnostic-eval-per-rung", str(args.diagnostic_eval_per_rung)] if args.tranche else [])
    command.extend(tranche_args)
    before = time.perf_counter()
    with (output / "strict-regen.log").open("w") as log:
        gate = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    atomic_json(output / "preflight.json", dict(diagnostic=True, label="diagnostic, not evidence",
        admitted=gate.returncode == 0, returncode=gate.returncode, wall_s=time.perf_counter()-before))
    if gate.returncode == 0:
        print("Qualified diagnostic regeneration completed", flush=True)
        return 0
    if not args.probe:
        print(f"Regeneration correctly refused admission; see {output / 'strict-regen.log'}", flush=True)
        return gate.returncode
    print("Regeneration refused admission. Running independent diagnostic generator helpers, not an admitted regen.", flush=True)
    copied = snapshot(output)
    env["PYTHONPATH"] = str(copied / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["CAMPAIGN_CACHE_ROOT"] = str(output / "cache")
    from pin_transport import PIN_PATH, PIN_SHA256, pin_environment
    env = pin_environment(frozen["numerical_caches"], output, env)
    # Reexec the coordinator with snapshot imports; no live-source guard changes.
    if os.environ.get("CAMPAIGN_V2_PROBE_COORDINATOR") != str(output):
        env["CAMPAIGN_V2_PROBE_COORDINATOR"] = str(output)
        command = [sys.executable, __file__, "--manifest", str(manifest_path), "--probe", "--timeout-s", str(args.timeout_s),
            "--max-attempts", str(args.max_attempts), *tranche_args]
        return subprocess.run(command, cwd=ROOT, env=env).returncode
    import regen
    from eval.splits import load_inventory
    from eval.splits_epoch import validate_epoch_manifest, validate_epoch_instance
    epoch = frozen["epoch_id"]
    base = output / "release/experiments"
    release = regen.build_release(epoch, True, 2)
    counts, entries = None, None
    if args.tranche:
        from tranches import parse_train_counts, select_entries
        counts = parse_train_counts(args.train_per_rung)
        entries = select_entries(release, counts, args.diagnostic_eval_per_rung, diagnostic=True)
    jobs = regen.make_jobs(release, base, True, 2, args.max_attempts, epoch, entries=entries)
    for job in jobs:
        job["cache_output"] = env["CAMPAIGN_CACHE_ROOT"]
        job["numerical_cache_pin_path"] = env[PIN_PATH]
        job["numerical_cache_pin_sha256"] = env[PIN_SHA256]
    tranches = regen.bind_tranche(output, args.tranche or "legacy", jobs, train_per_rung=counts,
        diagnostic_eval_per_rung=args.diagnostic_eval_per_rung if args.tranche else None)
    records = regen.load_completed(output, epoch, True)
    errors, new_jobs = [], []
    resumed = 0
    def checkpoint():
        return regen._write_indices(records, release, base, epoch, True, frozen, tranches=tranches)
    checkpoint()
    started = time.perf_counter()
    for job in jobs:
        folder = output / "jobs" / job["id"]
        folder.mkdir(parents=True, exist_ok=True)
        marker = output / "done" / f"{job['id']}.json"
        if regen.marker_valid(marker, job, epoch, True):
            resumed += 1
            continue
        atomic_json(folder / "job.json", job)
        with (folder / "worker.log").open("w") as log:
            child = subprocess.Popen([sys.executable, __file__, "--worker-job", str(folder / "job.json"),
                "--worker-output", str(folder / "result.json")], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            atomic_json(folder / "pid.json", dict(pid=child.pid, owner_pid=os.getpid(), started=datetime.now(timezone.utc).isoformat()))
            try:
                code = child.wait(timeout=args.timeout_s)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    code = child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    code = child.wait()
        record = read_snapshot((folder / "result.json")).json() if (folder / "result.json").exists() else {}
        if code or "result" not in record:
            errors.append(dict(job=job["id"], returncode=code, result=record))
            continue
        regen._commit(record, marker, epoch, True)
        records.append(record)
        new_jobs.append(job["id"])
        checkpoint()
        calibration = regen._calibration(records, output / "calibration.json", epoch, True)
        regen.measured_schedule(calibration, jobs, records, workers=1)
        print(json.dumps(dict(job=job["id"], status=record["result"].get("status"), wall_s=record["elapsed_s"])), flush=True)
    root = regen.instance_root(base, epoch)
    index = checkpoint()
    validate_epoch_manifest(root, index, load_inventory(regen.V2_MANIFEST), allow_diagnostic=True, check_source=False)
    for key, group in index["groups"].items():
        for row in group["shards"]:
            validate_epoch_instance(root / key / row["instance"], row)
    regen._calibration(records, output / "calibration.json", epoch, True)
    report = dict(label="diagnostic, not evidence; helper probe only", diagnostic=True, admitted=False,
        complete=False, epoch_id=epoch, instance_root=str(root), accepted=index["count"],
        rejected=index["rejected"], attempted=len(jobs), errors=errors, wall_s=time.perf_counter()-started,
        split_entries_sha256=release["entries_sha256"], source_preflight_complete=False,
        current_tranche=args.tranche or "legacy", tranches=index["tranches"],
        resumed=resumed, new_jobs=new_jobs, cumulative_completed=len(records),
        not_generated=len(index["not_generated_spec_keys"]))
    atomic_json(output / "result.json", report)
    atomic_json(output / f"result-{args.tranche or 'legacy'}-{len(new_jobs)}-new.json", report)
    print(json.dumps(report), flush=True)
    return 1 if errors else 0


if __name__ == "__mp_main__" and os.environ.get("CAMPAIGN_CACHE_ROOT"):
    from cache_adapter import redirect_cache, enforce_scope
    redirect_cache(Path(os.environ["CAMPAIGN_CACHE_ROOT"]))
    enforce_scope()

if __name__ == "__main__":
    raise SystemExit(main())
