"""Wait at most 45 seconds and reserve only this executive's single CPU lane."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("d130_admission", HERE / "admission.py")
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--estimate-s", required=True, type=float)
    args = parser.parse_args()
    stop = time.monotonic() + 44
    while time.monotonic() < stop:
        with contextlib.redirect_stdout(io.StringIO()):
            admission.main()
        record = json.loads((HERE / "admission.json").read_text())
        if record["fits"]:
            result = subprocess.run([sys.executable, str(HERE / "ledger.py"), "reserve",
                                     "--job", args.job, "--estimate-s", str(args.estimate_s)],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                print(result.stdout.strip(), flush=True)
                return 0
        time.sleep(1)
    print(json.dumps(dict(status="still_queued", last_observation=record)), flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
