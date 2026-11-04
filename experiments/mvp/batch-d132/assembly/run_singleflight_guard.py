"""One active worker CPU, two processes, synthetic cache-miss guard."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = Path("/private/tmp/stowlm-d132-assembly-20261030/baseline")
CANDIDATE = Path("/private/tmp/stowlm-d132-assembly-20261030/candidate")


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def main():
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    report = {"estimate": {"maximum_worker_cores": 1, "peak_ram_mb": 600,
                           "gpu": False, "wall_s": 20},
              "worker_admission": "Shared CPU-lane flock serializes imports/cache/JSON; released only during blocking waits",
              "process_payload": "tiny deterministic mock; full real Vessel JSON identity covered separately by unit guards",
              "rounds": [], "owned_pids": []}
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[key] = "1"
    all_digests = []
    with tempfile.TemporaryDirectory(prefix="stowlm-singleflight-") as scratch:
        scratch = Path(scratch)
        for label, candidate, enabled in (("baseline", False, False),
                                           ("candidate-off", True, False),
                                           ("candidate-on", True, True)):
            directory = scratch / label
            directory.mkdir()
            processes = []
            round_start = time.monotonic()
            try:
                for _ in range(2):
                    command = [sys.executable, str(HERE / "singleflight_worker.py"), str(directory)]
                    runtime = CANDIDATE if candidate else ROOT
                    process = subprocess.Popen(command, env={**environment,
                        "STOWAGE_VESSEL_SINGLEFLIGHT": "1" if enabled else "0", "D132_RUNTIME_ROOT": str(runtime), "PYTHONPATH": str(runtime / "src") + ":" + str(runtime)},
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    report["owned_pids"].append(process.pid)
                    processes.append(process)
                deadline = time.monotonic() + 20
                while True:
                    builds = records(directory / "builds.jsonl")
                    attempts = records(directory / "lock-attempts.jsonl")
                    if ((not enabled and len(builds) == 2)
                            or (enabled and len(builds) == 1 and len(attempts) == 2)):
                        break
                    if any(process.poll() is not None for process in processes):
                        raise AssertionError("Worker exited before both cold misses were observed")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Two-worker rendezvous timed out")
                    time.sleep(0.01)
                (directory / "release-builders").touch()
                outputs = []
                for process in processes:
                    stdout, stderr = process.communicate(timeout=20)
                    assert process.returncode == 0, stderr
                    value = json.loads(stdout)
                    assert value["json_sha256"] == value["expected_json_sha256"]
                    outputs.append(value)
                    all_digests.append(value["json_sha256"])
                builds = records(directory / "builds.jsonl")
                assert len(builds) == (1 if enabled else 2)
                report["rounds"].append({"label": label, "build_count": len(builds),
                    "lock_attempts": len(records(directory / "lock-attempts.jsonl")),
                    "wall_s": time.monotonic() - round_start,
                    "workers": outputs, "all_reaped": all(p.returncode is not None for p in processes)})
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()  # Only this controller's recorded children.
                        process.communicate(timeout=10)
        directory = scratch / "dead-owner"
        directory.mkdir()
        for flag, expected in (("--die-with-lock", 7), ("--acquire-after-death", 0)):
            process = subprocess.Popen([sys.executable, str(HERE / "singleflight_worker.py"), str(directory), flag],
                env={**environment, "D132_RUNTIME_ROOT": str(CANDIDATE), "PYTHONPATH": str(CANDIDATE / "src")}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            report["owned_pids"].append(process.pid)
            try:
                stdout, stderr = process.communicate(timeout=10)
                assert process.returncode == expected, stderr
                if expected == 0:
                    report["abrupt_owner_exit_releases_lock"] = json.loads(stdout)["acquired_after_abrupt_owner_exit"]
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=10)
    assert len(set(all_digests)) == 1
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    report.update(passed=True, wall_s=time.monotonic() - started,
        waited_children_cpu_s=(after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime),
        waited_children_maxrss_raw=after.ru_maxrss,
        maxrss_units="bytes on macOS; KiB on Linux; per-child waited resource, not simultaneous peak",
        platform=sys.platform, identical_json_sha256=all_digests[0],
        baseline_cache_source_sha256=sha256((ROOT / "src/stowage/vessel/fleet_cache.py").read_bytes()).hexdigest())
    report["candidate_cache_source_sha256"] = sha256((CANDIDATE / "src/stowage/vessel/fleet_cache.py").read_bytes()).hexdigest()
    (HERE / "singleflight-process-guard.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
