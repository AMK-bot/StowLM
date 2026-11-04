"""Compare numerical margin content without conflating changed epoch labels."""
import json
from pathlib import Path

import numpy as np
from stowage.linear.margin_policy import optimistic_envelope

RUN = Path(__file__).resolve().parent
HISTORICAL = RUN.parent.parent/"fix-t3-linear/epoch-claude"


def read(path):
    return json.loads(path.read_text())


before = read(RUN/"baseline-bank.json")
after = read(RUN/"promotion/candidate-bank.json")
result = {}
for ship in ("feeder_500", "feeder_1000", "feeder_1700"):
    old_raw, new_raw = read(HISTORICAL/ship/"raw.json"), read(RUN/"ordinary"/ship/"raw.json")
    old_entry = before["entries"][old_raw["diagnostics"]["response_calibration_identity"]]
    new_entry = after["entries"][new_raw["diagnostics"]["response_calibration_identity"]]
    differences = {}
    for field in ("physical_unsafe", "reference_transfer"):
        deltas = [np.asarray(new_entry[field][side][metric])-np.asarray(value)
            for side, quantities in old_entry[field].items() for metric, value in quantities.items()]
        differences[field] = dict(bit_identical=all(np.array_equal(delta, np.zeros_like(delta)) for delta in deltas),
            max_abs_difference=max(float(np.max(np.abs(delta))) for delta in deltas))
    for field in ("physical_optimistic", "reference_transfer"):
        old, new = old_entry["necessary"][field], new_entry["necessary"][field]
        deltas = [np.asarray(new[side][metric])-np.asarray(value)
            for side, quantities in old.items() for metric, value in quantities.items()]
        differences["necessary/"+field] = dict(
            bit_identical=all(np.array_equal(delta, np.zeros_like(delta)) for delta in deltas),
            max_abs_difference=max(float(np.max(np.abs(delta))) for delta in deltas))
    for direction in ("conservative", "necessary"):
        all_reserves = []
        for entry, raw in ((old_entry, old_raw), (new_entry, new_raw)):
            outer = direction == "necessary"
            record = entry["necessary"] if outer else entry
            physical = record["physical_optimistic" if outer else "physical_unsafe"]
            fresh = raw["diagnostics"]["signed_errors"]
            if outer:
                fresh = optimistic_envelope(fresh, len(entry["station_x_m"]))
            values = {f"{side}/{metric}": 1.1*(np.maximum(fresh[side][metric], value)
                +np.asarray(record["reference_transfer"][side][metric]))
                for side, quantities in physical.items() for metric, value in quantities.items()}
            boundary = record["boundary_list"]
            measured = boundary["measured_optimistic_kgm" if outer else "measured_unsafe_kgm"]
            values.update({f"{side}/list_moment_kgm": np.asarray(1.1*(measured[side]
                +boundary["reference_transfer_kgm"][side])) for side in measured})
            all_reserves.append(values)
        deltas = [all_reserves[1][key]-value for key, value in all_reserves[0].items()]
        differences[direction+"/default_operational_reserves"] = dict(
            bit_identical=all(np.array_equal(delta, np.zeros_like(delta)) for delta in deltas),
            max_abs_difference=max(float(np.max(np.abs(delta))) for delta in deltas),
            minimum_change=min(float(np.min(delta)) for delta in deltas))
    result[ship] = differences
(RUN/"numerical-margin-comparison.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps(result))
