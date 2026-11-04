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

ROOT = Path(__file__).resolve().parents[3]


def sources():
    paths = [path for directory in ("src/stowage/checker", "src/stowage/vessel", "src/stowage/model")
             for path in sorted((ROOT / directory).glob("*.py"))]
    paths.extend(ROOT / relative for relative in (
        "src/stowage/params.py", "src/stowage/rules/catalogue.toml",
        "params/fleet/feeder_500.toml", "params/fleet/feeder_800.toml", "params/fleet/feeder_1700.toml",
        "params/cargo/feeder_export.toml", "params/ports/laem_chabang_like.toml",
        "params/ports/small_feeder.toml", "data/raw/kcs/kcs.fix"))
    return {path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=float, default=180)
    parser.add_argument("--ram-gb", type=float, default=2)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("explicit Python arguments required, e.g. -- -m pytest ...")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.update({key: "1" for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS")})
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", UV_OFFLINE="1",
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    record = {"started_utc": datetime.now(timezone.utc).isoformat(), "argv": [sys.executable, *command],
              "estimate": {"cores": 1, "peak_ram_gb": args.ram_gb, "wall_s": 60},
              "external_watchdog_s": args.limit, "sources_before": sources(),
              "gpu": False, "native_threads": 1, "reaped": False}
    receipt = out / "process.json"
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    with (out / "stdout.txt").open("w") as stdout, (out / "stderr.txt").open("w") as stderr:
        process = subprocess.Popen(record["argv"], cwd=ROOT, env=environment, stdout=stdout, stderr=stderr)
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
                  ended_utc=datetime.now(timezone.utc).isoformat(), sources_after=sources())
    record["source_drift"] = record["sources_before"] != record["sources_after"]
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("owned_pid", "exit_code", "reaped", "wall_s", "cpu_s", "peak_waited_child_rss_bytes", "source_drift")}))
    return 0 if code == 0 and not record["source_drift"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
