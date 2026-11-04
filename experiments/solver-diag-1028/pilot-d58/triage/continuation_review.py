"""Read-only independent attribution of repair-enabled pilot continuations."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PILOT = HERE.parent


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def changes(left, right):
    return sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))


def source_assessment(row, raw_end, start, end):
    def valid(mapping):
        return (isinstance(mapping, dict) and bool(mapping) and all(
            isinstance(key, str) and isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
            for key, value in mapping.items()))
    missing = [name for name, mapping in (("raw_end", raw_end), ("start", start), ("end", end))
               if not valid(mapping)]
    since_raw = changes(raw_end, start) if valid(raw_end) and valid(start) else None
    during = changes(start, end) if valid(start) and valid(end) else None
    agrees = (since_raw == row.get("source_changes_since_raw") and during == row.get("source_drift")) if not missing else None
    return dict(missing_source_maps=missing,
                independently_checked_source_changes_since_raw=since_raw,
                independently_checked_source_drift=during, source_metadata_agrees=agrees)


def classify_continuation(row, *, finished, sources):
    if not finished:
        return "in_progress", "campaign owner"
    if sources["missing_source_maps"]:
        return "source_unavailable", "campaign owner"
    if sources["source_metadata_agrees"] is not True:
        return "source_metadata_mismatch", "campaign owner"
    if sources["independently_checked_source_changes_since_raw"] or sources["independently_checked_source_drift"]:
        return "source_transition", "campaign owner"
    if row.get("clean_complete_pass"):
        return "repair_rescued", "none"
    error = " ".join(str(row.get(key) or "") for key in ("error", "dispatch_error"))
    truncated = bool(row.get("cap_expired") or row.get("campaign_deadline") or any(
        token in error.lower() for token in ("instancedeadline", "whole-worker cap expired", "owned continuation exceeded cap")))
    if truncated:
        short_cap = isinstance(row.get("cap_s"), (int, float)) and row["cap_s"] < 150 - 1e-6
        return ("campaign_deadline" if short_cap or row.get("campaign_deadline") else "harness_budget"), "campaign owner"
    if row.get("completion") != "complete":
        return "repair_search_yield", "solver/repair owner"
    if row.get("checker_passed") and row.get("mask_replay_passed") is False:
        return "mask_replay", "feasibility/action representation owners"
    if row.get("checker_passed") and not row.get("shippable"):
        return "shipping_status_or_provenance", "solver/planner protocol owners"
    return "completed_repair_unresolved", "solver/ballast/checker owners"


def self_test():
    digest = "a" * 64
    maps = {"src/stowage/solver/scalable.py": digest}
    stable = dict(source_changes_since_raw=[], source_drift=[], completion="incomplete")
    # A completed but unauditable record cannot imply stable-source failure.
    missing = source_assessment(stable, maps, maps, {})
    assert missing["missing_source_maps"] == ["end"] and missing["source_metadata_agrees"] is None
    assert classify_continuation(stable, finished=True, sources=missing)[0] == "source_unavailable"
    # A shortened campaign cap and a normal outer harness cap have distinct owners/evidence.
    audited = source_assessment(stable, maps, maps, maps)
    assert audited["source_metadata_agrees"] is True
    short = {**stable, "cap_s": 8, "error": "InstanceDeadline: production continuation whole-worker cap expired"}
    assert classify_continuation(short, finished=True, sources=audited)[0] == "campaign_deadline"
    normal = {**short, "cap_s": 150}
    assert classify_continuation(normal, finished=True, sources=audited)[0] == "harness_budget"
    # Metadata is checked against maps rather than accepted on assertion.
    false_claim = {**stable, "source_changes_since_raw": ["invented.py"]}
    mismatch = source_assessment(false_claim, maps, maps, maps)
    assert mismatch["source_metadata_agrees"] is False
    assert classify_continuation(false_claim, finished=True, sources=mismatch)[0] == "source_metadata_mismatch"
    assert classify_continuation(stable, finished=True, sources=audited)[0] == "repair_search_yield"
    print("3 continuation classification/source-audit self-check groups passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    from attribute import classify as classify_raw
    rows = []
    for path in sorted((PILOT / "production-continuation").glob("*/*/result.json")):
        row = load(path)
        raw = Path(row["raw_run"])
        start, end = load(path.parent / "source-start.json"), load(path.parent / "source-end.json")
        raw_end = load(raw / "source-end.json")
        sources = source_assessment(row, raw_end, start, end)
        metrics = row.get("pipeline_metrics", {})
        finished = bool(load(path.parent / "done.json"))
        category, owner = classify_continuation(row, finished=finished, sources=sources)
        raw_row = load(raw / "result.json")
        raw_primary = classify_raw(raw_row, finished=bool(load(raw / "done.json")))
        rows.append(dict(solver=path.parent.parent.name, index=path.parent.name,
            category=category, owner=owner, finished=finished,
            raw_primary_attribution=raw_primary,
            final_status=row.get("final_status"), repaired=row.get("repaired"),
            repair_succeeded=metrics.get("repair_succeeded"),
            repair_attempts=metrics.get("repair_attempts"),
            repair_note=row.get("repair_note"), final_message=row.get("final_message"),
            completion=row.get("completion"), checker_passed=row.get("checker_passed"),
            shippable=row.get("shippable"), mask_replay_passed=row.get("mask_replay_passed"),
            ballast_status=row.get("ballast_status"),
            continuation_budget_s=row.get("continuation_budget_s"),
            cap_s=row.get("cap_s"), error=row.get("error"), dispatch_error=row.get("dispatch_error"),
            stage=row.get("stage"), interrupted_stage=row.get("interrupted_stage"),
            continuation_wall_s=row.get("continuation_wall_s"),
            pipeline_timing=row.get("pipeline_timing"),
            **sources,
            raw_rules=raw_row.get("rules", []),
            final_rules=row.get("rules", []), final_states=row.get("states", {}),
            global_infeasibility_proved=False, ballast_miss_demonstrated=False))
    (HERE / "continuation-attribution.jsonl").write_text("".join(
        json.dumps(row, sort_keys=True) + "\n" for row in rows))
    complete = [row for row in rows if row["finished"]]
    lines = ["# Production fallback assessment", "",
        "These continuations restore each exact recorded raw cargo result and run the public "
        "`run_planner(..., allow_repair=True)`. They do not rerun the original solver. "
        "Continuation budgets and elapsed times are additional to the original solve, "
        "and `repaired=True` means attempted fallback; success requires `repair_succeeded` "
        "plus the full checker, shipping gate and mask replay.", "",
        "| Solver/request | Additional phase limit (s) | Extra wall (s) | Repair attempts | Outcome | Source changes raw→continuation / during |",
        "|---|---:|---:|---:|---|---|"]
    for row in complete:
        wall = row.get("continuation_wall_s")
        wall_label = f"{wall:.3f}" if isinstance(wall, (int, float)) else "unavailable"
        source_labels = ["unavailable" if row[name] is None else str(len(row[name]))
                         for name in ("independently_checked_source_changes_since_raw", "independently_checked_source_drift")]
        lines.append(f"| {row['solver']}/{row['index']} | {row.get('continuation_budget_s')} | "
            f"{wall_label} | {row.get('repair_attempts')} | {row['category']} | "
            f"{' / '.join(source_labels)} |")
    lines.extend(["", "No incomplete-prefix lashing/loading/B2.3 diagnostic is treated as the "
        "primary cause. If fallback returns no complete plan, its primary limit is repair "
        "construction/search, owned by the solver/repair owner. No successful ballast witness "
        "or global physical infeasibility proof follows from these outcomes. Failed repair "
        "can return `plan=None`; a null checker result then means there was no final candidate "
        "to check, not that an accepted plan was lost or that the checker failed.", "",
        "Source-file maps are independently compared between the raw worker's end, the "
        "continuation's start and its end. Changed-source continuations are quarantined. "
        "Finished records missing a source map are labelled source_unavailable, and metadata "
        "disagreements are reported separately. Outer cap interruptions are campaign_deadline "
        "when the campaign shortened the normal 150-second continuation cap, otherwise "
        "harness_budget; neither supplies a completed fallback yield measurement. Raw solver "
        "primary attribution is retained separately. "
        "Current attribution records retain both the recorded and independently verified "
        "relationship. A 30-second additional repair measurement is not a 300/600-second "
        "rerun of the original solver, nor a proof that a longer repair can never succeed.", ""])
    (HERE / "production-fallback.md").write_text("\n".join(lines))
    print(json.dumps(dict(completed=len(complete), categories=dict(Counter(row["category"] for row in rows))), sort_keys=True))


if __name__ == "__main__":
    main()
