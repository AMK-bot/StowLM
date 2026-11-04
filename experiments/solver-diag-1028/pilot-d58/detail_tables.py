"""Pure JSON supplementary pilot tables; never runs planners or physics."""
from __future__ import annotations
import collections
import hashlib
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    rows = [json.loads(p.read_text()) for p in (HERE / "runs").glob("*/*/done.json")
            if (p.parent / "result.json").exists()]
    cells = collections.defaultdict(list)
    for row in rows:
        spec = row["requested_spec"]
        cells[row["solver"], spec["size"], spec["generator"]].append(row)
    lines = ["| solver | rung | generator | finished | complete | ballast set | checker pass | shippable | mask pass | full chain | solve median/max s |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for (solver, rung, generator), rs in sorted(cells.items()):
        n = len(rs)
        counts = [sum(r.get("completion") == "complete" for r in rs)]
        counts += [sum(r.get(key) is True for r in rs) for key in
                   ("ballast_found", "checker_passed", "shippable", "mask_replay_passed", "clean_complete_pass")]
        times = [r["solve_s"] for r in rs if r.get("solve_s") is not None]
        timing = f"{statistics.median(times):.2f}/{max(times):.2f}" if times else "—"
        lines.append(f"| {solver} | {rung} | {generator} | {n} | " +
                     " | ".join(f"{count}/{n}" for count in counts) + f" | {timing} |")
    (HERE / "table-by-generator.md").write_text("\n".join(lines) + "\n")
    failures = []
    for row in rows:
        if row.get("clean_complete_pass"):
            continue
        failures.append({key: row.get(key) for key in
                         ("solver", "index", "requested_spec", "completion", "solver_status", "final_status",
                          "solve_s", "budget_s", "cap_s", "cap_expired", "interrupted_stage", "rules",
                          "states", "part_errors", "ballast_status", "mask_replay", "source_drift")})
    (HERE / "failure-states.json").write_text(json.dumps(failures, indent=2) + "\n")
    state_rules = collections.defaultdict(lambda: collections.defaultdict(set))
    for row in failures:
        label = f"{row['solver']}/{row['index']:03d}"
        for state, rules in (row.get("states") or {}).items():
            for rule in rules:
                state_rules[rule][state].add(label)
        for state, error in (row.get("part_errors") or {}).items():
            state_rules["checker part unavailable"][state].add(label)
    state_lines = ["Incomplete-prefix diagnostics only. These are not completed-stow rejection proofs.", "",
                   "| rule ID | exact checker part / D57 state key | attempts |", "|---|---|---|"]
    for rule, states in sorted(state_rules.items()):
        for state, attempts in sorted(states.items()):
            state_lines.append(f"| `{rule}` | `{state}` | {', '.join(sorted(attempts))} |")
    (HERE / "failure-states.md").write_text("\n".join(state_lines) + "\n")
    epochs = collections.defaultdict(list)
    for row in rows:
        epochs[row.get("source_sha256", "unknown")].append([row["solver"], row["index"]])
    epoch_rows = []
    for epoch, runs in sorted(epochs.items()):
        solver, index = runs[0]
        path = HERE / "runs" / solver / f"{index:03d}" / "source-start.json"
        manifest = json.loads(path.read_text())
        feasibility = {k: v for k, v in manifest.items() if "/feasibility/" in k}
        epoch_rows.append(dict(source_sha256=epoch, runs=runs, count=len(runs),
                               manifest=str(path.relative_to(HERE)),
                               feasibility_sha256=hashlib.sha256(json.dumps(feasibility, sort_keys=True).encode()).hexdigest()))
    (HERE / "source-epochs.json").write_text(json.dumps(epoch_rows, indent=2) + "\n")
    exact = [{key: row.get(key) for key in
              ("index", "requested_spec", "optimality_status", "clean_complete_pass", "checked_objective",
               "lower_bound_36", "gap_to_lb36_absolute", "gap_to_lb36_relative", "solver_metrics")}
             for row in rows if row["solver"] == "exact"]
    (HERE / "exact-certificates.json").write_text(json.dumps(exact, indent=2) + "\n")
    print(json.dumps(dict(finished=len(rows), failures=len(failures), epochs=len(epoch_rows))))


if __name__ == "__main__":
    main()
