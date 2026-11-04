"""Expose freshly captured exact holdouts in the D49 audit checkpoint schema.

No equilibrium is recomputed and no absent quantity is reconstructed. This
accepts only the new exact-capture marker and all seven saved exact quantities;
historical populations without the measured GM/displacement fail closed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input-root", type=Path, default=HERE/"epoch-fingerprint")
parser.add_argument("--output", type=Path, default=HERE/"fingerprint-holdout-audit")
args = parser.parse_args()
for path in (args.input_root.resolve(), args.output.resolve()):
    if not path.is_relative_to(HERE) or path == HERE:
        parser.error("input and output must be dedicated directories in this experiment")
spec = importlib.util.spec_from_file_location("fingerprint_saved_driver", HERE.parent/"epoch_requalification/driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def pin(path):
    return {"path": str(path.resolve().relative_to(ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

from stowage.linear.physics import _prepared_params_for
for ship in ("feeder_500", "feeder_1000", "feeder_1700"):
    for level in ("light", "medium", "full"):
        source_path = args.input_root/ship/f"holdout-{level}.json"
        source_bytes = source_path.read_bytes()
        source = json.loads(source_bytes)
        source_pin = {"path": str(source_path.resolve().relative_to(ROOT)),
                      "sha256": hashlib.sha256(source_bytes).hexdigest()}
        marker = source.get("sample_state_exact_source", {})
        if marker.get("format") != 1 or marker.get("driver_sha256") != pin(Path(driver.__file__))["sha256"]:
            raise ValueError("holdout lacks current freshly captured exact-field source")
        states = source["list_row_samples"]
        if source["phase"] != "holdout" or source["samples"] != len(states) or len(states) != 64:
            raise ValueError("expected unchanged independent 64-state holdout")
        instance, _ = driver.cargo_instance(ship, level)
        _, parameters = _prepared_params_for(instance)
        current = driver.snapshot(ship, parameters)
        if (source["physical_inputs_start"] != current or source["physical_inputs_end"] != current
                or source["physical_inputs_unchanged"] is not True):
            raise ValueError("fresh exact holdout epoch differs from current numerical inputs")
        observations = []
        for index, state in enumerate(states):
            exact = {name: state["exact_"+name] for name in (
                "gm_m", "displacement_kg", "list_rad", "trim_m", "draught_m", "sf_n", "bm_nm")}
            if not all(np.isfinite(value).all() for value in exact.values()):
                raise ValueError("nonfinite saved exact measurement")
            observations.append({"index": index, "sample_sha256": digest(state), "exact": exact})
        if driver.snapshot(ship, parameters) != current or pin(source_path) != source_pin:
            raise ValueError("saved measurement or numerical inputs changed during materialization")
        record = {"format": 1, "ship": ship, "level": level, "expected_samples": 64,
                  "input_binding": {"source": source_pin, "physical_inputs": current,
                                    "states_sha256": digest(states)},
                  "physical_inputs_end": current, "complete": True, "states": observations,
                  "replay_performed": False, "origin": marker,
                  "purpose": "view of freshly captured exact measurements; no second replay or reconstructed quantity"}
        output = args.output/"replay"/ship/f"{level}.json"
        if output.exists():
            raise ValueError("preserve existing checkpoint; choose an unused output directory")
        driver.write(output, record)
        print(json.dumps({"ship": ship, "level": level, "states": len(observations), "replay_performed": False}), flush=True)
