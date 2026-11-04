"""One-command, single-core diagnostic of regeneration and all G1 stages."""
from __future__ import annotations

from eval.snapshot_io import read_snapshot

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=HERE / "diagnostic-freeze.json")
    args = parser.parse_args(argv)
    args.manifest = args.manifest.resolve()
    if not args.manifest.is_relative_to(HERE):
        parser.error("diagnostic manifest must be inside campaign-g1")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[key] = "1"
    if not args.manifest.exists():
        subprocess.run([sys.executable, str(HERE / "freeze.py"), "--diagnostic",
                        "--manifest", str(args.manifest)], cwd=ROOT, env=env, check=True)
    frozen = read_snapshot(args.manifest).json()
    if frozen.get("diagnostic") is not True:
        parser.error("this command accepts diagnostic epochs only")
    output = HERE / frozen["epoch_id"]
    output.mkdir(exist_ok=True)
    snapshot = output / "diagnostic-source"
    stamp = snapshot / "snapshot.json"
    if not stamp.exists():
        snapshot.mkdir(exist_ok=True)
        started = time.perf_counter()
        for relative in ("src", "params", "data", "pyproject.toml", "uv.lock",
                         "experiments/stability", "experiments/linear-model",
                         "experiments/physics-review/design",
                         "models/glimmer-30b-4bit/tokenizer.json"):
            source, target = ROOT / relative, snapshot / relative
            if not source.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if sys.platform == "darwin":
                subprocess.run(["/bin/cp", "-cR", str(source), str(target)], check=True)
            elif source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        # The diagnostic snapshot itself is immutable once this stamp exists.
        # It avoids per-generator internal drift aborts while the live tree moves.
        from freeze import create_manifest, atomic_json
        captured = create_manifest(snapshot, diagnostic=True)
        atomic_json(stamp, {**captured, "copy_wall_s": time.perf_counter()-started,
            "purpose": "diagnostic imports only; never evidence for the active source"})
    from freeze import check_manifest
    check_manifest(stamp, diagnostic=True)
    env["PYTHONPATH"] = str(snapshot / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["CAMPAIGN_DIAGNOSTIC_SNAPSHOT"] = str(snapshot)
    jobs = [
        ("regen", [str(HERE / "regen.py"), "--manifest", str(args.manifest),
            "--diagnostic", "--allow-drift", "--workers", "1", "--count", "2"]),
        ("campaign", [str(HERE / "campaign.py"), "--freeze-manifest", str(args.manifest),
            "--diagnostic", "--allow-drift", "--count", "2", "--rungs", "tiny",
            "--workers", "1", "--search-s", "2", "--oracle-s", "2",
            "--ballast-s", "2", "--check-s", "3", "--timeout-s", "45"]),
    ]
    records = []
    for name, command in jobs:
        print(f"diagnostic, not evidence: {name}; one CPU, no GPU; checkpoints resume", flush=True)
        started = time.perf_counter()
        with (output / f"diagnostic-{name}.log").open("w") as log:
            process = subprocess.Popen([sys.executable, *command], cwd=ROOT, env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
            (output / "diagnostic-pid.json").write_text(json.dumps(dict(pid=process.pid,
                owner_pid=os.getpid(), job=name, started=datetime.now(timezone.utc).isoformat())) + "\n")
            try:
                code = process.wait()
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                process.wait()
                raise
        records.append(dict(stage=name, returncode=code, wall_s=time.perf_counter()-started))
        (output / "diagnostic-run.json").write_text(json.dumps(dict(label="diagnostic, not evidence",
            diagnostic=True, epoch_id=frozen["epoch_id"], jobs=records), indent=2) + "\n")
        if code:
            print(f"{name} stopped ({code}); see {output / f'diagnostic-{name}.log'}", file=sys.stderr)
            return code
    print(f"diagnostic, not evidence: completed; {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
