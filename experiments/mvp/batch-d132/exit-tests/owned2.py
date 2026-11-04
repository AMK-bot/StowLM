"""Record one native-single-thread owned pytest/CLI worker without signal injection."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]


def sources(runtime):
    from runtime import source_pins
    return source_pins(runtime)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--reservation", type=Path, required=True,
                        help="Fresh existing successful own ledger reservation; refusal never launches a worker")
    parser.add_argument("--limit", type=float, default=180)
    parser.add_argument("--ram-gb", type=float, default=2)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    reservation = json.loads(args.reservation.read_text())
    if reservation.get('status') != 'reserved' or reservation.get('estimate', {}).get('cores') != 2:
        raise RuntimeError('A successful one-core reservation is required before spawning')
    ledger = (ROOT/'experiments/fleet/ledger.md').read_text()
    own = [line for line in ledger.splitlines() if line.startswith('| mvp-construct/D132-exit |')]
    if len(own) != 1 or float(own[0].split('|')[3].strip()) != 2:
        raise RuntimeError('Own live one-core ledger row is absent; no worker launched')
    if own[0].split('|')[2].strip() != 'D132 '+reservation['job']:
        raise RuntimeError('Reservation does not name the current own job; no worker launched')
    age = (datetime.now(timezone.utc)-datetime.fromisoformat(reservation['admission']['observed_utc'])).total_seconds()
    if not reservation['admission']['fits'] or not 0 <= age <= 90:
        raise RuntimeError('Fresh successful admission is required at launch; no worker launched')
    claim = args.reservation.with_suffix('.launch-claim.json')
    # One reservation cannot accidentally start two numeric workers.
    with claim.open('x') as stream:
        json.dump({'launcher_pid':os.getpid(),'job':reservation['job'],
                   'created_utc':datetime.now(timezone.utc).isoformat()},stream)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("explicit Python arguments required, e.g. -- -m pytest ...")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.update({key: "1" for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS")})
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", UV_OFFLINE="1",
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    record = {"started_utc": datetime.now(timezone.utc).isoformat(), "argv": ["nice", "-n", "10", sys.executable, *command], "cwd": str(args.runtime),
              "estimate": {"cores": 2, "peak_ram_gb": args.ram_gb, "wall_s": 60},
              "external_watchdog_s": args.limit, "sources_before": sources(args.runtime),
              "gpu": False, "native_threads": 1, "reaped": False}
    record['native_environment'] = {key: environment.get(key) for key in (
        'OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS',
        'NUMEXPR_NUM_THREADS','RAYON_NUM_THREADS','PYTHONHASHSEED','STOWAGE_VESSEL_SINGLEFLIGHT')}
    receipt = out / "process.json"
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    with (out / "stdout.txt").open("w") as stdout, (out / "stderr.txt").open("w") as stderr:
        process = subprocess.Popen(record["argv"], cwd=args.runtime, env=environment, stdout=stdout, stderr=stderr)
        record["owned_pid"] = process.pid
        receipt.write_text(json.dumps(record, indent=2) + "\n")
        try:
            code = process.wait(timeout=args.limit)
        except subprocess.TimeoutExpired:
            record["harness_timeout"] = True
            process.terminate()
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    record.update(exit_code=code, reaped=True, wall_s=time.perf_counter() - started,
                  cpu_s=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                  peak_waited_child_rss_bytes=after.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                  ended_utc=datetime.now(timezone.utc).isoformat(), sources_after=sources(args.runtime))
    record["source_drift"] = record["sources_before"] != record["sources_after"]
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("owned_pid", "exit_code", "reaped", "wall_s", "cpu_s", "peak_waited_child_rss_bytes", "source_drift")}))
    return 0 if code == 0 and not record["source_drift"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
