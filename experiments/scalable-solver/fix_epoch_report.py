"""Rebuild bounded fix-wave evidence without running a solver or opening test inputs."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    rows = []
    for profile in ("tiny", "small", "medium", "full"):
        root = HERE / f"fix-epoch-shard-{profile}"
        admission = json.loads((root / "admission.json").read_text())
        results = list((root / "results").glob("*.json"))
        if len(results) != 1:
            raise ValueError(f"expected one checkpoint for {profile}")
        result = json.loads(results[0].read_text())
        generation = result["generation"]
        measured = result["variants"].get("default", {})
        source_transition = measured.get("source_transition", False)
        approved = bool(measured.get("approved") and not source_transition)
        rows.append(dict(profile=profile, source_sha256=admission["source_sha256"],
            physics_sha256=admission["physics"]["sha256"], split=result["split"]["split"],
            request=result["spec"], input_sha256=generation.get("input_sha256"),
            generation_status=generation["status"], generation_seconds=generation["wall_s"],
            generation_failure=generation.get("error"), boxes=generation.get("boxes"),
            attempted=bool(measured), complete=measured.get("completed", False),
            full_checker_pass=measured.get("checker_passed", False),
            mask_pass=measured.get("replay", {}).get("status") == "passed",
            verified_actions=measured.get("replay", {}).get("verified_actions"),
            partial_loaded=measured.get("solver_metrics", {}).get("partial_loaded"),
            ballast_status=measured.get("ballast_status", "not_reached"),
            source_transition=source_transition, approved=approved,
            approved_objective=measured.get("objective") if approved else None,
            approved_gap_to_lb=measured.get("gap_to_lb") if approved else None,
            pipeline_seconds=measured.get("timings_s", {}).get("pipeline"),
            search_seconds=measured.get("timings_s", {}).get("search"),
            exact_status=measured.get("solver_metrics", {}).get("exact_root_status", "not_run"),
            independent_exact_optimum=None, checkpoint=str(results[0].relative_to(HERE))))
    report = dict(schema="fix-t4-scalable-bounded-evidence-v1", requests=len(rows),
        diagnostic_c2_off=False, rows=rows, approved=sum(r["approved"] for r in rows),
        stable_attempts=sum(r["attempted"] and not r["source_transition"] for r in rows),
        mixed_source_attempts=sum(r["source_transition"] for r in rows),
        small_comparison=dict(old_approved=1, old_attempted=30, current_approved=0,
            current_attempted=1, qualification="Different current generator/rule epoch and 15s vs old120s search; descriptive only, not matched or population evidence"),
        independent_exact_comparison="No fresh independently proved production optimum; unavailable, never inferred from a timeout",
        time_per_approved_plan_seconds=None,
        qualification="Four preselected train/val request shards. Generation failures remain in the request denominator. Mixed-source observations are exploratory and never approved. Complete mask replay alone is not gold.")
    (HERE / "fix-epoch-report.json").write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    lines = ["# Bounded current-source pilot — fix-t4-scalable", "",
        report["qualification"], "",
        "| Rung | Boxes | Generation s | Complete | Full checker | Complete mask | Ballast | Pipeline s | Approved objective / gap | Source |",
        "|---|---:|---:|---:|---:|---:|---|---:|---|---|"]
    for row in rows:
        sec = row["pipeline_seconds"]
        source = "mixed / quarantined" if row["source_transition"] else "stable"
        if not row["attempted"]:
            source = "generation rejected"
        lines.append(f"| {row['profile']} | {row['boxes'] if row['boxes'] is not None else '—'} | {row['generation_seconds']:.2f} | {int(row['complete'])} | {int(row['full_checker_pass'])} | {int(row['mask_pass'])} | {row['ballast_status']} | {sec:.2f} | — | {source} |" if sec is not None else
            f"| {row['profile']} | — | {row['generation_seconds']:.2f} | 0 | 0 | 0 | not reached | — | — | {source} |")
    lines.extend(["", "All four requests were selected before generation using the frozen train/val split; no test inputs were opened. All admitted shards started with the same source and physics pins. Tiny changed source during measurement and is quarantined; small/medium retained stable pins.", "",
        "Small: **0/1 approved**, versus the historical **1/30**. This is descriptive only: current generators/rules and a 15 s budget differ from the old 120 s pilot. Small loaded 3/34 and medium2/121 before timeout; those prefixes fail complete replay and are not completed plans. There is no finite observed time per approved plan because none passed.", "",
        "Tiny:4/4 loaded and all6 index/decode actions replayed, but ballast returned `not_found`, full checker rejected securing, and a source transition excludes the observation from stable statistics. Its internal exact window timed out: no optimum/gap-to-optimum claim. The two earlier longer tiny attempts are separate mixed-source retries, not two additional independent inputs or approved plans.", "",
        "Full: three generator attempts exhausted necessary screening (on-deck/IMDG/physical occupancy; C1a/C1g/C1h). No full search ran and no infeasibility proof follows. This does not revisit the old 600 s full-search result.", "",
        "No independently proved current production exact optimum exists in this bounded run. Approved objective/lower-bound gaps remain unavailable for every rung. Do not reuse the older C2-off 16.7% comparison as production evidence.", "",
        "Resume each shard only with identical admitted source/physics/config: rerun its original `fix_epoch_pilot.py` command and output path. A transition requires a new output directory; completed checkpoints are never relabelled. `fix_epoch_report.py` rebuilds this read-only report.", ""])
    (HERE / "fix-epoch-report.md").write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
