"""Summarise stored three-state metrics; do not rerun any ship physics.

GM and steady-wind heel margins below are reconstructed from reported numbers
using the exact unchanged statutory/manual limits. Strength and visibility
margins were not persisted by the production gate and cannot be reconstructed
from these rows. Their full-checker pass is evidence of legality, not a measured
quantitative margin in this artifact.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PHASES = ("departure", "mid_voyage", "arrival")
METRICS = ("gm_m", "gm_solid_m", "kg_m", "fsc_m", "displacement_kg", "b23_ratio",
           "weather_ratio", "draught_mean_m", "list_rad", "trim_m")


def distribution(values):
    finite = [value for value in values if value is not None and math.isfinite(value)]
    return {"n": len(finite), "min": min(finite), "median": statistics.median(finite),
            "max": max(finite)} if finite else None


def quantities(metrics, hull):
    values = {key: metrics.get(key) for key in METRICS}
    # Generators set no instance rule overrides. The loading GM floor is the
    # stronger of IS Code A2.2.4's0.15m and the supplied vessel-manual minimum.
    values["derived_loading_gm_margin_m"] = metrics["gm_m"] - max(.15, hull["min_gm_m"])
    values["derived_lashing_gm_cap_margin_m"] = hull["max_gm_lashing_m"] - metrics["gm_m"]
    values["derived_b23_arrival_target_margin_ratio"] = metrics["b23_ratio"] - 1.05
    # IS Code A2.3.1.2: steady wind heel is <=16deg or80% of deck immersion.
    values["derived_weather_steady_heel_margin_rad"] = (
        min(math.radians(16), .8*metrics["weather_deck_edge_rad"]) - metrics["weather_phi0_rad"])
    values["derived_weather_steady_heel_margin_deg"] = math.degrees(values["derived_weather_steady_heel_margin_rad"])
    values["derived_weather_energy_ratio_margin"] = metrics["weather_ratio"] - 1.
    # A2.3.5 applicability: these are necessary scalar clearances, not a
    # substitute for the checker's roots/curve-coverage conjunction.
    draught = metrics["draught_mean_m"]
    corrected_kg_ratio = (metrics["kg_m"] + metrics["fsc_m"]) / draught
    values["derived_weather_beam_draught_margin"] = 3.5 - hull["beam_m"] / draught
    values["derived_weather_kg_ratio_lower_margin"] = corrected_kg_ratio - .7
    values["derived_weather_kg_ratio_upper_margin"] = 1.5 - corrected_kg_ratio
    values["derived_weather_roll_period_margin_s"] = 20. - metrics["weather_roll_period_s"]
    return values


def phase_distributions(records, hull):
    result = {}
    for phase in PHASES:
        samples = [quantities(record["states"][phase], hull) for record in records
                   if phase in record.get("states", {})]
        result[phase] = {key: distribution([sample.get(key) for sample in samples])
                         for key in sorted({key for sample in samples for key in sample})}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=Path, default=HERE / "final-draws.jsonl")
    parser.add_argument("--out", type=Path, default=HERE / "final-state-margins.json")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.draws.read_text().splitlines()]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["vessel"], row["rung"], row["generator"])].append(row)
    cells = []
    global_accepted_tiny_small = defaultdict(list)
    design_all = defaultdict(list)
    for (vessel, rung, generator), cell in sorted(grouped.items()):
        parameters = tomllib.loads((ROOT / "params/fleet" / f"{vessel}.toml").read_text())
        design_gm = next(phase["metrics"]["max_gm_lashing_m"] for row in cell
                         for phase in row.get("design_lashing", {}).values()
                         if "max_gm_lashing_m" in phase["metrics"])
        hull = {**parameters["hull"],
                "min_gm_m": parameters["operating_limits"]["min_gm_m"],
                "max_gm_lashing_m": design_gm}
        calls = [row["gate_calls"][0] for row in cell if row.get("gate_calls")]
        accepted = [call for call in calls if call["accepted"]]
        designs = [phase for row in cell for phase in row.get("design_lashing", {}).values()]
        design_ratios = {key: distribution([phase["metrics"].get(key) for phase in designs])
                         for key in sorted({key for phase in designs for key in phase["metrics"]
                                            if key.startswith("max_") and key.endswith("_ratio")})}
        cells.append({"vessel": vessel, "rung": rung, "generator": generator,
                      "raw_draws": len(cell), "checked_backgrounds": len(calls),
                      "accepted_backgrounds": len(accepted),
                      "all_checked": phase_distributions(calls, hull),
                      "accepted_backgrounds_only": phase_distributions(accepted, hull),
                      "design_securing_ratios_all_raw": design_ratios,
                      "design_securing_phase_passes": sum(phase["passed"] for phase in designs),
                      "design_securing_phase_checks": len(designs)})
        for key, summary in design_ratios.items():
            if summary:
                design_all[key].append(summary["max"])
        if rung in {"tiny", "small"}:
            for call in accepted:
                for phase in PHASES:
                    for key, value in quantities(call["states"][phase], hull).items():
                        global_accepted_tiny_small[key].append(value)
    output = {"method": __doc__, "source_keys": sorted({row["source_start"] for row in rows}),
              "strength_visibility_quantitative_margins": "Not persisted; not measured here. Full-checker accepted backgrounds passed these parts.",
              "accepted_tiny_small_all_3states": {key: distribution(values)
                  for key, values in sorted(global_accepted_tiny_small.items())},
              "design_securing_worst_ratio_all_raw": {key: max(values) for key, values in sorted(design_all.items())},
              "cells": cells}
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True)+"\n")
    print(json.dumps({key: value for key, value in output.items() if key != "cells"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
