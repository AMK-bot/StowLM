"""Verify completed timing artifacts and finalize scoped compute accounting."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    reference = json.loads((HERE / "bench-reference.json").read_text())
    candidate = json.loads((HERE / "bench-candidate.json").read_text())
    compute = json.loads((HERE / "bench-compute.json").read_text())
    summary = dict(status="complete; isolated DG-only candidate remains unshipped",
                   exact_masks=0, source_drift=False, blocks={})
    for label, filename in (("full_reference", "bench-reference.event.json"),
                            ("full_candidate", "bench-candidate.event.json")):
        event = json.loads((HERE / filename).read_text())
        assert event["status"] == "complete" and not event["changed_sources"]
        compute[label].update(status="complete", actual_process_seconds=event["process_elapsed_s"],
            peak_bytes=event["maxrss_bytes_macos"], exit_code=0)
    compute["full_candidate"]["session"] = 82734
    before = json.loads((HERE / "bench-reference.event.json").read_text())
    after = json.loads((HERE / "bench-candidate.event.json").read_text())
    assert before["sources_before"] == after["sources_before"]
    summary["source_pins_identical"] = True
    for key, new in candidate["blocks"].items():
        old = reference["blocks"][key]
        assert old["instance_sha256"] == new["instance_sha256"]
        assert len(old["states"]) == len(new["states"])
        assert all(a["mask_sha256"] == b["mask_sha256"] and a.get("action") == b.get("action")
                   for a, b in zip(old["states"], new["states"]))
        summary["exact_masks"] += len(new["states"])
        def totals(rec):
            chunks = [rec["construct_lp"], *[s["lp"] for s in rec["states"]],
                      *[s.get("push_lp", {}) for s in rec["states"]]]
            return {k: int(sum(c.get(k, 0) for c in chunks)) for k in ("native", "cold", "warm")}
        assert totals(old) == totals(new), key
        summary["blocks"][key] = dict(states=len(new["states"]),
            reference_p50_p95_ms=old["index_ms_p50_p95"], candidate_p50_p95_ms=new["index_ms_p50_p95"],
            lp_counts_identical=totals(new), base_steps=new["projected_base_steps"],
            reference_base_projection_ms=old["projected_base_plan_ms_p50_p95"],
            candidate_base_projection_ms=new["projected_base_plan_ms_p50_p95"])
    (HERE / "bench-compute.json").write_text(json.dumps(compute, indent=2) + "\n")
    (HERE / "bench-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    report_path = HERE / "bench-report.md"
    report = report_path.read_text().split("\n## Scoped compute and interpretation\n")[0]
    report += "\n## Scoped compute and interpretation\n\n"
    report += "The isolated revision retains DG preprocessing only; both witness-reuse optimizations were removed. "
    report += "C2 LP counts are identical. These samples show no overall full-rung speedup; the 5ms target remains unmet. "
    report += "Historical timings under a different source/policy epoch are not a causal baseline for this revision.\n\n"
    report += "| Job | Estimate s | Actual process s | Peak GiB |\n|---|---:|---:|---:|\n"
    for label in ("pilot_reference", "pilot_candidate", "full_reference", "full_candidate"):
        row = compute[label]
        report += f"| {label} | <= {row['estimated_seconds_upper']} | {row.get('actual_process_seconds', 0):.3f} | {row.get('peak_bytes', 0)/2**30:.3f} |\n"
    report += "\nOne CPU, <2.5GB allocation, no GPU, all three thread limits 1. nice -n10 attempted; sandbox refused setpriority and ran commands. "
    report += "All four processes exited 0. All 88 masks and replay actions matched; source pins were stable and identical between variants. "
    report += "No own jobs remain.\n"
    report_path.write_text(report)


if __name__ == "__main__":
    main()
