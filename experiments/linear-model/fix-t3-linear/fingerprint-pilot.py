"""Two-state current-fingerprint timing only; never production evidence."""
import argparse
import importlib.util
import json
from pathlib import Path
import resource
import time

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--ship", default="feeder_500", choices=("feeder_500", "feeder_1000", "feeder_1700"))
parser.add_argument("--output", type=Path, default=HERE/"fingerprint-pilot")
args = parser.parse_args()
output = args.output.resolve()
if not output.is_relative_to(HERE) or output == HERE:
    parser.error("output must stay in a dedicated experiment directory")
if (output/args.ship/"raw.json").exists():
    parser.error("pilot must freshly measure; choose an unused output directory")
spec = importlib.util.spec_from_file_location("fingerprint_driver", HERE.parent/"epoch_requalification/driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
started = time.monotonic()
family, instance, errors, diagnostics, inputs = driver.prepare(args.ship, output, samples=2)
if driver.snapshot(args.ship, family.parameters) != inputs:
    raise ValueError("pilot numerical epoch changed")
record = {"ship": args.ship, "accuracy_qualification": False, "samples": 2,
          "wall_s": time.monotonic()-started,
          "raw_calibration_s": json.loads((output/args.ship/"raw.json").read_text())["seconds"],
          "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
          "rss_platform": "darwin", "attempts": diagnostics["attempts"],
          "inputs": inputs, "response_identity": diagnostics["response_calibration_identity"]}
driver.write(output/f"pilot-{args.ship}.json", record)
print(json.dumps(record), flush=True)
