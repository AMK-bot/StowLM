"""Bounded paired stability comparison: six backgrounds × three D57 states."""
from collections import Counter
import json
from pathlib import Path
import struct

HERE = Path(__file__).resolve().parent
rows = [json.loads(line) for line in (HERE.parent / "final-draws.jsonl").read_text().splitlines()]
summary = json.loads((HERE / "margin-summary.json").read_text())
coefficients = dict(zip(("area_0_30_mrad", "area_0_40_mrad", "area_30_40_mrad",
                        "gz_at_or_above_30_m", "gz_max_m", "area_0_flood_mrad"),
                       (.009, .016, .006, .033, .042, .029)))
counts = Counter()
pairs = []


def label(key):
    return key.startswith(("note:", "condition.id:", "condition.phase:",
                           "condition.environment_id:", "condition.density_id:", "condition.locode:"))


def execution_diagnostic(key):
    return key.startswith(("liquid_", "geometry_")) or key in {
        "free_surface_method", "geometry_table_used", "flooding_openings_declared"}


for case in summary["results"]:
    baseline = next(row for row in rows if row["generator"] == "random"
                    and (row["vessel"], row["rung"], row["seed"]) ==
                    (case["vessel"], case["rung"], case["seed"]))
    report = json.loads((HERE / case["report"]).read_text())
    for name, part in report["parts"].items():
        if not name.startswith("condition:") or not name.endswith(":stability"):
            continue
        current = dict(part["metrics"])
        phase = next(phase for phase in ("departure", "mid_voyage", "arrival")
                     if f"condition.phase: {phase}" in current)
        old = baseline["gate_calls"][0]["states"][phase]
        current["b23_ratio"] = min(current[key]*current["containership_form_factor"]/coefficient
                                   for key, coefficient in coefficients.items())
        keys = sorted(key for key in old.keys() & current.keys() if not label(key))
        differences = []
        for key in keys:
            a, b = float(old[key]), float(current[key])
            kind = "execution_diagnostic" if execution_diagnostic(key) else "physical_or_context"
            counts[f"{kind}_compared"] += 1
            identical = struct.pack("!d", a) == struct.pack("!d", b)
            counts[f"{kind}_bitwise_identical"] += int(identical)
            if not identical:
                differences.append(dict(metric=key, matrix=a, supplementary=b,
                    absolute_error=abs(a-b), relative_error=abs(a-b)/max(abs(a), abs(b), 1e-300),
                    classification=kind))
        pairs.append(dict(vessel=case["vessel"], rung=case["rung"], seed=case["seed"], phase=phase,
            common_numeric_metrics=len(keys), differences=differences,
            added_metrics=sorted(key for key in current.keys()-old.keys() if not label(key)),
            removed_metrics=sorted(key for key in old.keys()-current.keys() if not label(key))))

physical_differences = [dict(pair={key: pair[key] for key in ("vessel", "rung", "seed", "phase")}, **difference)
                        for pair in pairs for difference in pair["differences"]
                        if difference["classification"] == "physical_or_context"]
output = dict(matrix_source=summary["matrix_source"], supplementary_source=summary["source_start"],
    scope="Only the six selected accepted random-yard backgrounds, three D57 states each; not a global equivalence claim.",
    states_compared=len(pairs), counts=dict(counts), physical_differences=physical_differences,
    all_physical_or_context_metrics_bitwise_identical=not physical_differences,
    maximum_absolute_physical_difference=max((d["absolute_error"] for d in physical_differences), default=0.),
    pairs=pairs)
(HERE / "paired-stability.json").write_text(json.dumps(output, indent=2, sort_keys=True)+"\n")
print(json.dumps({key: value for key, value in output.items() if key != "pairs"}, indent=2))
