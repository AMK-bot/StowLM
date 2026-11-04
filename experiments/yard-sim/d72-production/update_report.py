"""Refresh an explicitly incomplete progress report without editing live runner.

Final scientific rows are the main results.jsonl. During execution, deduplicate
prefetched actual measurements by spec; cached transports never add seed draws.
"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    rows = {}
    main_count = 0
    for name in ("prefetch.jsonl", "results.jsonl"):
        path = HERE / name
        values = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        if name == "results.jsonl":
            main_count = len(values)
        for row in values:
            key = json.dumps(row["spec"], sort_keys=True)
            if key in rows and row["status"] == rows[key]["status"] == "measured":
                for field in ("source_before", "paired_input_sha256", "simulation_seed", "arms"):
                    assert rows[key][field] == row[field], (key, field, "cached result collision")
            rows[key] = row
    spec = importlib.util.spec_from_file_location("d72_production_report", HERE / "run_measurement.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.report(list(rows.values()))
    if main_count < 768:
        path = HERE / "report.md"
        path.write_text(f"> **IN PROGRESS:** {len(rows)}/768 distinct seed specifications computed; "
            f"{main_count}/768 delivered to authoritative `results.jsonl`. This progress view includes "
            "deduplicated `prefetch.jsonl` measurements. Missing/refused cells remain explicit; "
            "this is not final production precision or qualified yield.\n\n" + path.read_text())
    state = {"updated_utc": datetime.now(timezone.utc).isoformat(), "unique_cases": len(rows),
             "main_records": main_count, "target_records": 768,
             "measured": sum(r["status"] == "measured" for r in rows.values())}
    (HERE / "progress.json").write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps(state))


if __name__ == "__main__":
    main()
