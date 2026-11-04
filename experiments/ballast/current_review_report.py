"""Summarise checkpointed production-ship ballast experiments, no physics changes."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent
lines = ["# Current production ballast benchmark", "", "Production ships; schema 1.3, ballast 2.1. Five-second calls, seed 0, two consecutive calls in the same process. Single-threaded BLAS. Cold/warm labels mean first/second finisher call; cache creation and full checks are timed separately.", "", "The literal group uses the reviewer's harness.py C1–C3 unchanged except an in-memory FixedLoad.mass_kg schema adapter. The manual group additionally follows after_run.condition_context: after.json's declared ballast reduces the cargo budget, with 0.1 m mean-draught reserve; onboard ballast is then set to zero before finishing. No ship parameter overrides and no manual seed are supplied to the finisher. Saved physical vessels/conditions are reused only when newer than physical source files; vessel-cache entries are also fingerprint validated.", "", "A manual witness is judged independently by the full checker. Its physics verdict requires stability, strength, visibility and lashing to run without errors or violations. Full-check failure remains failure even if all four physics parts pass. Neither an unsuccessful local search nor a failed manual witness proves infeasibility.", ""]
for label, file in (("Literal harness", "current_review_final.json"),("Manual budget", "current_review_manual.json")):
    path = ROOT/file
    if not path.exists(): continue
    rows = json.loads(path.read_text())
    lines += [f"## {label}", "", "| Ship | Condition | First call | Second call | Warm s | Manual physics | Manual full check |", "|---|---|---|---|---:|---|---|"]
    successes=total=0
    for row in rows:
        result=next((p for p in row["phases"] if p.get("phase")=="result"),None)
        if result is None:
            lines.append(f"| {row['vessel']} | {row['condition']} | ERROR | ERROR | — | — | — |")
            continue
        calls=result["calls"]
        successes+=sum(c["status"]=="ok" for c in calls); total+=len(calls)
        witness=result.get("manual_witness")
        physics=full="—"
        if witness:
            physics=str(all((p:=witness["parts"].get(k,{})).get("ran") and not p.get("error") and not p.get("violations") for k in ("stability","strength","visibility","lashing")))
            full=str(witness["passed"])
            if witness.get("timed_out"):
                physics=full="unknown (8s timeout)"
        lines.append(f"| {row['vessel']} | {row['condition']} | {calls[0]['status']} ({calls[0]['wall_s']:.3f}s) | {calls[-1]['status']} | {calls[-1]['wall_s']:.3f} | {physics} | {full} |")
    lines += ["", f"Finisher success: {successes}/{total} calls. Complete records, checker parts, margins and witness masses: `{file}`.", ""]
lines += ["## Reproduction", "", "```sh", "UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/current_review.py --check --out experiments/ballast/current_review_final.json --watchdog 150", "UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/current_review.py --manual --check --out experiments/ballast/current_review_manual.json --watchdog 150", "UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/ballast/current_review_report.py", "```", "", "Only directly created worker processes are terminated by the supervisor's watchdog. No watchdog timeout is counted as a proof. Machine cross-check before admission: load 2.92, memory 95% free; ps denied by sandbox. Reservation: one CPU core, 1 GB, no GPU. Peak RSS unavailable under sandbox."]
(ROOT/'current_review_results.md').write_text('\n'.join(lines)+'\n')
