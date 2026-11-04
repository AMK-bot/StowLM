"""Run the exact production convergence runner and bound three-state diagnostics.

No legacy builder or voyage evidence is substituted. The original runner now
attaches production voyage declarations and pins its inputs. Its historical
replay remains the default; --current-manual selects the restored current manual.
The original full reports remain in their output files; this wrapper adds only
small condition-ID/phase/fraction/rule-ID summaries, including every report part.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"


def summarize(output):
    source = json.loads((output / "summary.json").read_text())
    rows = []
    for result in source["rows"]:
        parts = result["check"].get("report", {}).get("parts", {})
        conditions = {}
        for key, part in parts.items():
            if not key.startswith("condition:"):
                continue
            condition_id, name = key[len("condition:"):].rsplit(":", 1)
            metrics = part.get("metrics", {})
            phase = next((key.split(": ", 1)[1] for key in metrics if key.startswith("condition.phase: ")), None)
            remaining_fraction = next((value for metric, value in metrics.items()
                if metric in ("condition.remaining_fraction", "condition.r", "remaining_fraction")), None)
            condition = conditions.setdefault(condition_id, dict(
                id=condition_id, phase=phase, remaining_fraction=remaining_fraction, parts={}))
            condition["parts"][name] = dict(ran=part["ran"], error=part.get("error"),
                hard_rule_ids=sorted({v["rule_id"] for v in part.get("violations", []) if v["severity"] == "hard"}))
        for condition in conditions.values():
            condition["passed"] = all(part["ran"] and not part["hard_rule_ids"]
                for part in condition["parts"].values())
        rows.append(dict(ship=result["ship"], loading_recipe=result["condition"],
            passed=result["check"]["passed"], status=result["check"]["status"],
            conditions=list(conditions.values()),
            failed_aggregate_parts={name: dict(ran=part["ran"], error=part.get("error"),
                hard_rule_ids=sorted({v["rule_id"] for v in part.get("violations", []) if v["severity"] == "hard"}))
                for name, part in parts.items() if not name.startswith("condition:") and (
                    not part["ran"] or any(v["severity"] == "hard" for v in part.get("violations", [])))},
            voyage_notes=[key for key in parts.get("voyage", {}).get("metrics", {}) if key.startswith("note:")]))
    result = dict(total=len(rows), passed=sum(row["passed"] is True for row in rows),
        completed=sum(row["status"] == "completed" for row in rows),
        replay="current-manual" if "--current-manual" in sys.argv else "historical",
        elapsed_s=source.get("elapsed_s"), rows=rows)
    (output / "d57-condition-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    source = ROOT / "experiments/physics-review/rerun/fix-t2-vessel/recheck.py"
    spec = importlib.util.spec_from_file_location("d57_exact_convergence", source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.main()
    if "--out" not in sys.argv:
        raise ValueError("D57 evidence requires an explicit fresh --out directory")
    summarize(Path(sys.argv[sys.argv.index("--out") + 1]))


if __name__ == "__main__":
    main()
