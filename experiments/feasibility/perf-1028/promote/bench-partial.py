"""Summarize the stopped promotion benchmark without inventing after timings."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bench_helpers", HERE.parent / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def main():
    ref = json.loads((HERE / "bench-reference.json").read_text())
    stop = json.loads((HERE / "STOP_MISMATCH.json").read_text())
    sources_after = bench.pins()
    changed = {k: sources_after.get(k) for k, v in ref["sources"].items() if sources_after.get(k) != v}
    out = dict(status="stopped after equivalence failure; no promotion", recorded=datetime.now(timezone.utc).isoformat(),
               session=1686, exit_code=130, termination="Ctrl-C to own recorded PTY session",
               stopped_reference_elapsed_s=None,
               elapsed_note="Process elapsed/peak memory were not checkpointed before interruption.",
               mismatch=dict(key=stop["key"], depth=stop["details"]["depth"],
                             missing_count=len(stop["details"]["missing_index"]),
                             extra_count=len(stop["details"]["extra_index"])),
               environment=ref["environment"], sources_before=ref["sources"],
               sources_after=sources_after, changed_existing_sources=changed, blocks={})
    lines = ["# Promotion benchmark stopped: candidate failed equivalence", "",
             "No promotion and no twelve-prefix candidate timing. The independent gate found 50 reference actions missing",
             "from the candidate at medium C2-on measurement depth 12 (slot 0110182); see STOP_MISMATCH.json.",
             "The reference timing process was stopped through its own recorded PTY session 1686 (Ctrl-C, exit 130).", "",
             "The table below contains reference-only results. Full C2-on has 11 completed samples; its next evaluation was interrupted.",
             "These values cannot establish candidate performance or complete equivalence.", "",
             "| Rung / C2 | Completed states | Reference median / p95 ms | Recorded native / cold / warm | Base load steps | Reference base projection median / p95 s |",
             "|---|---:|---:|---:|---:|---:|"]
    completed_work = 0.
    for key, rec in ref["blocks"].items():
        vals = [s["index_ms"] for s in rec["states"]]
        p50, p95 = np.percentile(vals, [50, 95])
        chunks = [rec["construct_lp"], *[s["lp"] for s in rec["states"]],
                  *[s.get("push_lp", {}) for s in rec["states"]]]
        calls = {k: int(sum(c.get(k, 0) for c in chunks)) for k in ("native", "cold", "warm")}
        steps = rec["boxes"]
        row = dict(states=len(vals), complete_block="index_ms_p50_p95" in rec,
                   p50_p95_ms=[p50, p95], recorded_calls=calls, base_steps=steps,
                   base_projection_s=[steps * p50 / 1000, steps * p95 / 1000])
        out["blocks"][key] = row
        call_text = " / ".join(str(calls[k]) for k in ("native", "cold", "warm"))
        label = str(len(vals)) + (" (partial)" if not row["complete_block"] else "")
        lines.append(f"| {key} | {label} | {p50:.3f} / {p95:.3f} | {call_text} | {steps} | {steps*p50/1000:.3f} / {steps*p95/1000:.3f} |")
        completed_work += rec["construct_s"] + sum((s["index_ms"] + s.get("materialize_ms", 0) + s.get("push_ms", 0))/1000 for s in rec["states"])
    out["completed_recorded_work_s"] = completed_work
    lines += ["", "LP counts include construction, completed masks and completed pushes. Interrupted work is excluded, so full C2-on counts are lower bounds.",
              "Base projection = initial load-list size × sampled step time; extra restow/discharge steps are excluded.",
              "Same saved four instances, seed 1704, one native/BLAS thread, fresh processes, cold legal_index per prefix.", "",
              "## Pilot and compute", "",
              "| Job | Estimate | Recorded actual | Outcome |", "|---|---|---|---|"]
    for filename, name in (("bench-pilot-reference.json", "reference one-prefix pilot"),
                           ("bench-pilot-candidate.json", "candidate one-prefix pilot")):
        pilot = json.loads((HERE / filename).read_text())
        lines.append(f"| {name} | <=45s, 1 CPU, <2 GB, no GPU | {pilot['wall_s']:.3f}s, peak {pilot['maxrss_bytes_macos']/2**30:.3f} GiB | 8/8 root masks agree; C2 on all rungs; stable source pins |")
    lines += [f"| reference twelve-prefix attempt | <=120s, 1 CPU, <2 GB, no GPU | interrupted; {completed_work:.3f}s of completed timed work | exit 130; final elapsed/peak were not checkpointed |",
              "| candidate twelve-prefix attempt | <=120s | not started | gate failure |", "",
              f"Changed existing source hashes between reference start and post-stop audit: {len(changed)}.",
              "All benchmark sessions are stopped. No feasibility source was edited or promoted."]
    bench.write(HERE / "bench-partial.json", out)
    (HERE / "bench-partial-report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
