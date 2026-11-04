"""Run the exact convergence replay with explicit D50 next-call declarations.

ASSUMPTION: the historical synthetic current dock water density also applies at
the next call, under a distinct declared environment/density identity. All real
loading recipes, 97% departure consumables and ballast witnesses are unchanged.
This is an adapted replay, not a claim that legacy missing evidence was valid.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

from d50_benchmark import declare_voyage

ROOT = Path(__file__).resolve().parents[2]
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"


def main():
    source = ROOT / "experiments/physics-review/rerun/fix-t2-vessel/recheck.py"
    spec = importlib.util.spec_from_file_location("d50_original_convergence", source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    import harness2

    original = harness2.build

    def build(*args, **kwargs):
        instance, info = original(*args, **kwargs)
        return declare_voyage(instance), {**info,
            "d50_assumption": "same declared water density at next synthetic call; default 10% group retention",
            "d50_adapter": str(Path(__file__).relative_to(ROOT))}

    harness2.build = build
    runner.main()
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else source.parent / "results"
    summarize(out)


def summarize(out):
    """Bounded diagnostics; the original runner retains each full report once."""
    summary = json.loads((out / "summary.json").read_text())
    rows = []
    for result in summary["rows"]:
        report = result["check"].get("report", {})
        parts = report.get("parts", {})
        rows.append(dict(ship=result["ship"], loading_recipe=result["condition"],
            passed=result["check"]["passed"], status=result["check"]["status"],
            failed_parts={name: dict(ran=part["ran"], error=part.get("error"),
                hard_rule_ids=sorted({v["rule_id"] for v in part.get("violations", []) if v["severity"] == "hard"}))
                for name, part in parts.items()
                if not part["ran"] or any(v["severity"] == "hard" for v in part.get("violations", []))},
            endpoint_parts={name: dict(
                condition_id=name[len("condition:"):].rsplit(":", 1)[0],
                phase=next((key.split(": ", 1)[1] for key in part.get("metrics", {}) if key.startswith("condition.phase: ")), None),
                ran=part["ran"], error=part.get("error"),
                hard_rule_ids=sorted({v["rule_id"] for v in part.get("violations", []) if v["severity"] == "hard"}))
                for name, part in parts.items() if name.startswith("condition:")},
            voyage_notes=[key for key in parts.get("voyage", {}).get("metrics", {}) if key.startswith("note:")]))
    (out / "per-condition.json").write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
