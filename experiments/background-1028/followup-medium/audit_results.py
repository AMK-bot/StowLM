"""Audit persisted raw draws without constructing ships or rerunning physics."""
from collections import Counter
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=HERE)
    args = parser.parse_args()
    directory = args.directory
    paths = [directory / "final-random.jsonl", directory / "final-simulated.jsonl"]
    rows = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
    keys = [(row["generator"], row["vessel"], row["rung"], row["seed"]) for row in rows]
    assert len(keys) == len(set(keys)) == 156
    cells = Counter(key[:3] for key in keys)
    assert len(cells) == 18
    assert all(count == (6 if key[2] == "medium" else 10) for key, count in cells.items())
    assert all(row["screens"] == {"C1": True, "C2": False, "background_finisher_full_checker": True} for row in rows)
    assert all(len(row["gate_calls"]) == 1 for row in rows)
    calls = [row["gate_calls"][0] for row in rows]
    audits = [call["utilisation"]["mass_sizing_audit"] for call in calls]
    assert all(audit["full_booking_initial_mass_legal"] for audit in audits)
    assert all(not audit["booking_tank_capacity_violation_ids"] for audit in audits)
    assert all(audit["fixed_consumable_tanks_unchanged"] for audit in audits)
    assert all(audit["booking_reference_sizing_status"] == "sized" for audit in audits)
    assert all(audit["booking_reference_ballast_sizing"]["within_displacement_limit"] for audit in audits)
    assert all(audit["booking_reference_ballast_sizing"]["transverse_moment_balanced"] for audit in audits)
    assert all(not audit["booking_reference_ballast_sizing"]["actual_plan_certificate"] for audit in audits)
    assert all(abs(audit["expected_actual_booking_difference_kg"]) < 1e-5
               for row in rows if row["accepted"] for audit in [row["utilisation"]["mass_sizing_audit"]])
    epoch_keys = sorted({row[field] for row in rows for field in ("source_start", "source_end")})
    manifests = {key: json.loads((HERE / "sources" / f"{key}.json").read_text()) for key in epoch_keys}
    first = rows[0]["source_start"]
    original = manifests[first]["sources"]
    changed_files = sorted({path for manifest in manifests.values() for path in original.keys() | manifest["sources"].keys()
                            if original.get(path) != manifest["sources"].get(path)})
    failures = [{"draw": (row["generator"], row["vessel"], row["rung"], row["seed"]),
                 "source_start": row["source_start"], "source_end": row["source_end"],
                 "finisher_status": call["finisher_status"], "reasons": call["reasons"]}
                for row in rows for call in row["gate_calls"] if not call["accepted"]]
    design = [phase for row in rows for phase in row.get("design_lashing", {}).values()]
    summary = {
        "audit_utc": datetime.now(timezone.utc).isoformat(), "raw_draws": len(rows),
        "cells": len(cells), "all_mass_reference_and_tank_audits_passed": True,
        "background_passes": sum(call["accepted"] for call in calls),
        "pipeline_emitted": sum(row["accepted"] for row in rows),
        "source_start_epoch_counts": dict(Counter(row["source_start"] for row in rows)),
        "mid_draw_source_changes": sum(row["source_start"] != row["source_end"] for row in rows),
        "changed_source_files_during_grid": changed_files,
        "source_epoch_limitation": ("Hashes describe files on disk, not hot-reloaded Python modules. Long-lived worker imports persist across concurrent owner edits. "
            + ("Failed draws remain in this grid; any fresh-process replay is separate." if failures else
               "No failed background rows in this grid. The pre-endpoint failure remains preserved separately and is covered by the fixed-endpoint regression test.")),
        "failed_backgrounds": failures, "design_securing_phase_checks": len(design),
        "design_securing_phase_passes": sum(phase["passed"] for phase in design),
        "raw_gate_total_s": sum(call["elapsed_s"] for call in calls),
        "raw_generation_total_s": sum(row["wall_s"] for row in rows),
        "raw_diagnostics_total_s": sum(row["diagnostics_s"] for row in rows),
        "runtime_self_only": {name: rows_for_worker[-1]["runtime"] for name in ("random", "simulated")
                              for rows_for_worker in [[row for row in rows if row["generator"] == name]]},
    }
    (directory / "final-audit.json").write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n")
    (directory / "final-draws.jsonl").write_text("".join(json.dumps(row, sort_keys=True)+"\n" for row in rows))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
