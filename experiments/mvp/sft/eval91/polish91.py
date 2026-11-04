"""9.1 "StowLM + 15 s solver polish (hybrid)" row (separately labelled; never pooled with StowLM's own plans).

For each instance where StowLM's own best-of-4 plan passed the checker, warm-start the D97 gold LNS from that plan
(``stowage.solver.gold_warmstart.improve_checked``, 15 s): it keeps every box-to-slot assignment, improves yard order
only, and admits a candidate only after mask replay, securing screen and the full checker. The polished plan is checked
again here; the row's objective is never worse than StowLM's (the incumbent is retained when nothing improves).
Usage: uv run python experiments/mvp/sft/eval91/polish91.py <out_dir> [seconds]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics as st
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def source_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cache_matches(row, result_path, seconds):
    """Legacy cache rows lack budget/source binding and must be recomputed."""
    return (row.get("requested_polish_s") == seconds and row.get("own_report_passed") is True
            and row.get("source_result_sha256") == source_sha256(result_path))


def main() -> None:
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.solver.gold_warmstart import improve_checked

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("seconds", type=float, nargs="?", default=15.0)
    parser.add_argument("--sealed", action="store_true", help="suppress all result values on stdout")
    args = parser.parse_args()
    out, seconds = args.out_dir, args.seconds
    summary = json.loads((out / "summary.json").read_text())
    inputs = {}
    for run in ("stowlm", "sampled"):
        for rec in (out / run).glob("instances/*.json"):
            r = json.loads(rec.read_text())
            inputs[r["instance_id"]] = (ROOT / r["input"], r["rung"])
    target = out / "polish"
    target.mkdir(exist_ok=True)
    rows = []
    for iid, result_path in sorted(summary["chosen"].items()):
        cached = target / f"{iid}.json"
        if cached.exists():
            row = json.loads(cached.read_text())
            if cache_matches(row, result_path, seconds):
                rows.append(row)
                continue
        path, rung = inputs[iid]
        instance = load_instance(path)
        own = PlannerResult.model_validate(json.loads(Path(result_path).read_text()))
        own_report = check(instance, own.plan)
        t = time.perf_counter()
        polished = improve_checked(instance, own, time_limit_s=seconds)
        report = check(instance, polished.plan) if polished.plan is not None else None
        before = own_report.count.weighted_nonproductive if own_report.passed else None
        after = report.count.weighted_nonproductive if report is not None and report.passed else None
        kept = after is None or before is None or after > before  # never worse than StowLM's own plan
        row = {"instance_id": iid, "rung": rung, "stowlm_lifts": before, "own_report_passed": own_report.passed,
               "polished_lifts": before if kept else after, "improved": not kept and after < before,
               "requested_polish_s": seconds, "source_result_sha256": source_sha256(result_path),
               "polish_s": round(time.perf_counter() - t, 2), "message": polished.message}
        cached.write_text(json.dumps(row, indent=1))
        rows.append(row)
        if not args.sealed:
            print(json.dumps(row), flush=True)
    by = {}
    for rung in ("tiny", "small"):
        sel = [r for r in rows if r["rung"] == rung and r["polished_lifts"] is not None]
        by[rung] = {"n": len(sel), "improved": sum(r["improved"] for r in sel),
                    "stowlm_mean": st.mean(r["stowlm_lifts"] for r in sel) if sel else None,
                    "polished_mean": st.mean(r["polished_lifts"] for r in sel) if sel else None,
                    "polish_s_mean": st.mean(r["polish_s"] for r in sel) if sel else None}
    (out / "polish_summary.json").write_text(json.dumps({"label": f"StowLM + {seconds:g} s solver polish (hybrid)", **by}, indent=1))
    print("polish91 wrote polish records and polish_summary.json; errors=0" if args.sealed else json.dumps(by, indent=1))


if __name__ == "__main__":
    main()
