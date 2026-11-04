"""Current long-run table from checkpoints; no solver execution."""
import datetime
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    selection = json.loads((HERE / "notes/selection.json").read_text())["selected"]
    table = ["| case | ship | generator | solver | state | actual solve s | complete cargo | full checker | shippable | mask | first cargo / checker event s |",
             "|---:|---|---|---|---|---:|---|---|---|---|---|"]
    rows = []
    for case in selection:
        for solver in ("scalable", "twostep"):
            directory = HERE / "runs" / solver / f"{case['index']:03d}"
            path = directory / "result.json"
            row = json.loads(path.read_text()) if path.exists() else {}
            done = (directory / "done.json").exists()
            if done:
                row = json.loads((directory / "done.json").read_text())
            events = [json.loads(line) for line in (directory / "first-events.jsonl").read_text().splitlines()] if (directory / "first-events.jsonl").exists() else []
            complete_events = [event["planner_elapsed_s"] for event in events if event["kind"] in ("complete_construction_returned", "cargo_only_prestow_returned")]
            checked_events = [event["planner_elapsed_s"] for event in events if event["kind"] == "full_checker_pass"]
            state = row.get("solver_status", row.get("stage", "queued"))
            if not done and (directory / "pid.json").exists():
                pid = json.loads((directory / "pid.json").read_text())
                state += f" ({(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(pid['started_utc'])).total_seconds():.0f}s worker elapsed)"
            timing = "—" if row.get("solve_s") is None else f"{row['solve_s']:.2f}"
            status = lambda key: "—" if row.get(key) is None else "pass" if row[key] else "fail"
            cargo = row.get("completion", "—") if done else "pending"
            first = lambda values: "—" if not values else f"{min(values):.2f}"
            table.append(f"| {case['index']:03d} | {case['spec']['vessel']} | {case['spec']['generator']} | {solver} | {state} | {timing} | {cargo} | {status('checker_passed')} | {status('shippable') if done else 'pending'} | {status('mask_replay_passed')} | {first(complete_events)} / {first(checked_events)} |")
            rows.append(dict(index=case["index"], solver=solver, done=done, state=state, result=row, events=events))
    (HERE / "long-table.md").write_text("\n".join(table) + "\n")
    (HERE / "long-outcomes.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    summary = dict(planned=12, completed=sum(r["done"] for r in rows),
                   complete_cargo=sum(r["done"] and r["result"].get("completion") == "complete" for r in rows),
                   full_chain_pass=sum(r["done"] and bool(r["result"].get("clean_complete_pass")) for r in rows),
                   source_drift=sum(bool(r["result"].get("source_drift")) for r in rows))
    (HERE / "long-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
