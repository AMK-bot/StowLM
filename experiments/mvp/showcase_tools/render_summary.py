"""Render measured showcase outcomes and explicitly deferred current inputs."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / "experiments/mvp/runs"


def main():
    bounds = json.loads((RUNS / "showcase-solver/lower-bounds.json").read_text())["rows"]
    lines = ["# Showcase references (task 6.8)", "",
             "Synthetic showcases; rule-freeze OFF; C2 OFF (D79). The full checker gates departure approval.",
             "Current small/medium inputs are awaiting showcase v2; no solver time is spent on them.", "",
             "| Showcase | Targets | Method | Status | Loaded | Rehandles | Restow lifts | Hatch lifts now / later | Weighted bound | Solver seconds | Checker |",
             "|---|---:|---|---|---:|---:|---:|---|---:|---:|---|"]
    details = []
    for bound in sorted(bounds, key=lambda r: (r["showcase"].endswith("full"), r["targets"])):
        for method in ("solver", "twostep"):
            path = RUNS / f"showcase-{method}/instances" / bound["instance_id"] / "outcome.json"
            if not path.exists():
                status = ("running frozen hero" if method == "solver" else "deferred: restart requests solver hero") if bound["showcase"].endswith("full") else "deferred: showcase v2"
                lines.append(f"| {bound['showcase']} | {bound['targets']} | {method} | {status} | — | — | — | — | {bound['bound']['weighted']:g} | — | not run |")
                continue
            outcome = json.loads(path.read_text())
            count = outcome.get("count") or {}
            future = sum(f.get("hatch_cover_lifts", 0) for f in count.get("future", []))
            future_restows = sum(f.get("restow_lifts", 0) for f in count.get("future", []))
            seconds = outcome.get("planning_seconds")
            seconds = f"{seconds:.3f}" if seconds is not None else "—"
            verdict = "PASS" if outcome.get("departure_approved") else "FAIL" if outcome.get("checker_passed") is False else "not run"
            lines.append(f"| {bound['showcase']} | {bound['targets']} | {method} | {outcome['status']} | {outcome['loaded_required']} | {count.get('relocations', '—')} | {count.get('restow_lifts', '—')} (+{future_restows} later) | {count.get('hatch_cover_lifts', '—')} / {future} | {bound['bound']['weighted']:g} | {seconds} | {verdict} |")
            details += [f"- `{path.relative_to(ROOT)}`: {outcome.get('message') or outcome['status']}"]
            first = outcome.get("first_hard_violation")
            if first:
                details += [f"  First hard rule: `{first['rule_id']}` — {first['message']}"]
    lines += ["", "Prefix counts describe only executed work and are not comparable to complete-plan bounds.",
              "The weighted bound includes projected later-port work; current and future hatch lifts are shown separately.", "", *details, "",
              "APPROXIMATION: C2 is disabled per D79; full checker acceptance is unchanged. Physics timelines use the UI's measured keyframe policy, not every-lift approval.",
              "Limitation: sandbox denied nice priority changes and ps; all numerical threads are capped at one.", ""]
    animation = RUNS / "showcase-hero-animation/instances/random.full.9000400.0.7/outcome.json"
    execution = RUNS / "showcase-execution-1700/execution.json"
    if animation.exists():
        a = json.loads(animation.read_text())
        c = a.get("count") or {}
        lines += ["## Animation alternative", "",
                  f"`{animation.relative_to(ROOT)}`: **{a['status']}**, {a['loaded_required']}/{a['required_loads']} required boxes aboard; {a['executed_moves']} lifts, {c.get('relocations')} rehandles, {c.get('restow_lifts')} current restow lifts and {c.get('hatch_cover_lifts')} current hatch lifts. Departure approved: **{a['departure_approved']}**.",
                  "This earlier, most-loaded checkpoint has fewer repeated restows than the longest action path. Neither is a complete passing plan.",
                  "Its exact-matching UI cache has 76 measured keyframes, with 7 explicit geometry errors. No physics values are filled in for unavailable frames. Cache: `experiments/mvp/runs/showcase-hero-animation/timeline-cache/dc2470d50f9015ec.physics.json`.", ""]
    if execution.exists():
        e = json.loads(execution.read_text())
        lines += ["## Execution evidence", "",
                  "`experiments/mvp/runs/showcase-execution-1700/` retains checkpoints, full reports, physical-prefix replay, resource measurements and per-phase source hashes. The previous nested execution path remains a symlink; the UI avoids recursively scanning the archive.",
                  f"Solver worker: {e['phases'][0]['wall_time_s']:.3f} s, hard timeout; ballast-finalization worker: {e['phases'][1]['wall_time_s']:.3f} s (partial result unchanged); full-check worker: {e['phases'][2]['wall_time_s']:.3f} s. One core, no GPU. Plan-worker peak RSS is unavailable after the hard stop; checker-worker RSS is recorded separately, not substituted.",
                  f"{len(e['source_changes_during_run'])} source paths changed concurrently. Snapshots and per-phase hashes are retained; this is not a frozen-epoch benchmark.",
                  "Exported planning time is the measured total worker attempt; `prefix_discovery_wall_time_s` separately records when the selected checkpoint was found.", ""]
        fixed = RUNS / "showcase-execution-1700/post-layout-fix.resources.json"
        if fixed.exists():
            f = json.loads(fixed.read_text())
            lines += [f"Packaging memory estimate missed: 4 GB reserved initially, 17.511 GB observed while the UI importer scanned linked numerical-table JSON. Numerical tables now live outside UI discovery. Repeated imports of both corrected run folders took {f['wall_time_s']:.3f} s with {f['max_rss_bytes']/1e6:.3f} MB peak RSS. This packaging measurement is separate from solver RAM.", ""]
    (RUNS / "showcase-summary.md").write_text("\n".join(lines))
    print(RUNS / "showcase-summary.md")


if __name__ == "__main__":
    main()
