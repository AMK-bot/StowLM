"""Summarize D132 owned receipts; preserve refused admissions and the incident."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
AREAS = ('checker', 'masks', 'heat', 'samples', 'exit-tests', 'revision2/masks', 'assembly')


def main() -> None:
    jobs = []
    seen = set()
    for area in AREAS:
        candidates = set((HERE / area).glob('ledger-*.json')) | set((HERE / area).glob('*reservation*.json'))
        for path in sorted(candidates):
            if ".launch-claim." in path.name:
                continue
            value = json.loads(path.read_text())
            if "job" not in value or "estimate" not in value:
                continue
            process = value.get("process_receipt")
            actual = value.get("actual")
            if process:
                seen.add(str((ROOT / process).resolve()))
            jobs.append({"area": area, "job": value["job"],
                         "status": value["status"], "estimate": value["estimate"],
                         "actual": actual, "reservation": str(path.relative_to(ROOT)),
                         "process_receipt": process})
    unbound = []
    for area in AREAS:
        for path in sorted((HERE / area).glob("*/process.json")):
            if str(path.resolve()) in seen:
                continue
            value = json.loads(path.read_text())
            unbound.append({"receipt": str(path.relative_to(ROOT)),
                            "owned_pid": value.get("owned_pid"),
                            "reaped": value.get("reaped"),
                            "exit_code": value.get("exit_code"),
                            "wall_s": value.get("wall_s"),
                            "cpu_s": value.get("cpu_s"),
                            "source_drift": value.get("source_drift"),
                            "note": "No finished ledger receipt binding found; consult the admission incident/history rather than infer admission."})
    finished = [row for row in jobs if row["status"] == "finished"]
    running = [row for row in jobs if row["status"] == "reserved"]
    value = {"jobs": jobs, "unbound_process_receipts": unbound,
             "running_reservations": running,
             "finished_reserved_wall_s": sum(row["actual"]["wall_s"] for row in finished),
             "finished_reserved_cpu_s": sum(row["actual"]["cpu_s"] for row in finished),
             "unbound_reaped_wall_s": sum(row["wall_s"] or 0 for row in unbound if row["reaped"]),
             "unbound_reaped_cpu_s": sum(row["cpu_s"] or 0 for row in unbound if row["reaped"]),
             "method": "Use reservation estimates, not the older generic launcher's hardcoded wall estimate. Wall sums are worker sums, not campaign elapsed time; maximum child RSS is not simultaneous-process peak."}
    (HERE / "COMPUTE.json").write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"jobs": len(jobs), "finished": len(finished), "running": len(running),
                      "unbound_receipts": len(unbound)}))


if __name__ == "__main__":
    main()
