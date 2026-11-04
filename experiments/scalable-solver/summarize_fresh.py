"""Summarize the preselected D30 train/validation cohort, including failures.

Construction-only objectives are descriptive; a valid gap requires a full
checker pass. Throughput charges every attempted one-core pipeline, including
timeouts and missing-data rejection, rather than only successful calls.
"""
from __future__ import annotations

from collections import Counter
import argparse
import json
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent
COHORT = {
    "tiny": ["fresh-d30-final-tiny"],
    "small": ["fresh-d30-v245-small"],
    "medium": ["fresh-d30-v245-medium"],
    "full": ["fresh-d30-v245-full-s0", "fresh-d30-v245-full-s1"],
    "dense": ["fresh-d30-v245-dense-s0"],
}


def summarize(cohort=None, expected_count=30) -> dict:
    report = {}
    for rung, folders in (COHORT if cohort is None else cohort).items():
        paths = sorted(p for folder in folders for p in (ROOT / folder / "results").glob("*.result.json"))
        rows = [json.loads(p.read_text()) for p in paths]
        assert len(rows) == expected_count, (rung, len(rows))
        assert len({r["identity"]["input_sha256"] for r in rows}) == expected_count
        assert all(r["identity"]["split"] in {"train", "val"} for r in rows)
        measurements = [r["measurement"] for r in rows]
        complete = [m for m in measurements if m["completed"]]
        seconds = sum(m["timing_s"]["total"] for m in measurements)
        unsupported = sum(m["status"] == "unsupported_data" for m in measurements)
        lifts = {}
        for key in ("loads", "discharges", "relocations", "restow_lifts", "hatch_cover_lifts", "weighted_nonproductive"):
            values = [m["lifts"][key] for m in complete if key in m.get("lifts", {})]
            lifts[key] = mean(values) if values else None
        projected = [sum(f["restow_lifts"] for f in m["lifts"].get("future", [])) for m in complete]
        lifts["projected_restow_lifts"] = mean(projected) if projected else None
        covers = [sum(f["hatch_cover_lifts"] for f in m["lifts"].get("future", [])) for m in complete]
        lifts["projected_hatch_cover_lifts"] = mean(covers) if covers else None
        bounds = [m["lower_bound"]["weighted"] for m in complete if m.get("lower_bound")]
        proxy_gaps = [m["solver_metrics"]["gap"] for m in complete if m.get("solver_metrics", {}).get("gap") is not None]
        certified_gaps = [m["gap"] for m in measurements if m["gap_valid"]]
        report[rung] = {
            "n": len(rows), "boxes": sorted({r["boxes"] for r in rows}),
            "completed": len(complete), "unsupported_data": unsupported,
            "supported_completion": [len(complete), len(rows) - unsupported],
            "statuses": dict(Counter(m["status"] for m in measurements)),
            "pipeline_core_seconds": seconds, "mean_pipeline_s": seconds / len(rows),
            "mean_search_s": mean(m["timing_s"].get("search", 0) for m in measurements),
            "solver_versions": sorted({r["identity"].get("solver_version", "not_recorded") for r in rows}),
            "mean_completed_d11_l3": lifts["weighted_nonproductive"],
            "mean_completed_lower_bound": mean(bounds) if bounds else None,
            "d11_l1": "unverified unless checked", "d11_l2_completed_shutouts": 0 if complete else None,
            "mean_completed_lifts": lifts,
            "mean_completed_uncertified_proxy_gap": mean(proxy_gaps) if proxy_gaps else None,
            "mean_certified_gap": mean(certified_gaps) if certified_gaps else None,
            "checker_statuses": dict(Counter(m["checker_status"] for m in measurements)),
            "checker_passed": sum(m["checker_passed"] for m in measurements),
            "approved": sum(m["approved"] for m in measurements),
            "checker_passing_plans_per_core_hour": 3600 * sum(m["checker_passed"] for m in measurements) / seconds,
            "approved_plans_per_core_hour": 3600 * sum(m["approved"] for m in measurements) / seconds,
            "mask_statuses": dict(Counter(m["mask"]["status"] for m in measurements)),
            "diagnostic_c2_off": all(m["diagnostic_c2_off"] for m in measurements),
            "result_files": [str(p.relative_to(ROOT)) for p in paths],
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-prefix")
    parser.add_argument("--rungs", nargs="+", choices=tuple(COHORT), default=list(COHORT))
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--report-name", default="fresh-d30-report.json")
    args = parser.parse_args()
    if args.count < 1 or Path(args.report_name).name != args.report_name:
        parser.error("positive count and a report basename required")
    if args.cohort_prefix and Path(args.cohort_prefix).name != args.cohort_prefix:
        parser.error("cohort prefix must be a basename")
    cohort = {r: [f"{args.cohort_prefix}-{r}"] for r in args.rungs} if args.cohort_prefix else None
    result = summarize(cohort, args.count)
    (ROOT / args.report_name).write_text(json.dumps(result, indent=2) + "\n")
    for rung, row in result.items():
        print(rung, json.dumps({k: v for k, v in row.items() if k != "result_files"}))
