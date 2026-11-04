"""Measure mask latency on solver partials; never certify a partial plan.

APPROXIMATION: if bounded search found no complete medium plan, replay its
actual partial action prefix for diagnostic latency only. Full checker failure
is retained. No EOS is submitted and no full-plan success is claimed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time

import run_smoke as smoke


def main():
    from stowage.checker import check
    from stowage.io import load_instance, load_plan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert not rule_freeze_v1_enabled()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--limit-s", type=float, default=180)
    parser.add_argument("--compare-c2", action="store_true",
        help="Explicitly attempt C2-on diagnostics; default OFF under D79")
    args = parser.parse_args()
    prior_outputs = [args.directory / "partial-invocation.json",
                     args.directory / "partial-replay-results.json"]
    for ship in smoke.SHIPS:
        prior_outputs.extend(args.directory / f"medium-{ship}-0" / name
            for name in ("partial_replay.json", "partial_checker.json"))
    if any(path.exists() for path in prior_outputs):
        parser.error("Existing partial replay evidence is immutable; use a fresh campaign")
    rows = []
    smoke.save(args.directory / "partial-invocation.json", dict(argv=__import__("sys").argv,
        driver_sha256=smoke.DRIVER_SHA256,
        partial_driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_files=smoke.source_files(), c2_reason=smoke.C2_REASON,
        compare_c2=args.compare_c2))
    for ship in smoke.SHIPS:
        directory = args.directory / f"medium-{ship}-0"
        row = dict(case=directory.name, diagnostic_partial=True, full_plan_passed=False,
                   source_sha256_start=smoke.source_hash(), stages={})
        files_before = smoke.source_files()
        try:
            input_path, plan_path = directory / "instance.json", directory / "raw_plan.json"
            instance, plan = load_instance(input_path), load_plan(plan_path)
            row.update(input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
                       raw_plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest())
            start = time.perf_counter()
            try:
                with smoke.deadline(args.limit_s):
                    report = check(instance, plan)
                smoke.save(directory / "partial_checker.json", report.model_dump(mode="json"))
                row["stages"]["checker"] = dict(passed=report.passed,
                    violations=[v.model_dump(mode="json") for v in report.violations],
                    first_failing_rule=next((v.rule_id for v in report.violations if v.severity == "hard"), None))
            except Exception as exc:
                row["stages"]["checker"] = dict(passed=False,error=f"{type(exc).__name__}: {exc}")
            row["stages"]["checker"]["wall_s"] = time.perf_counter()-start
            for c2 in ((False, True) if args.compare_c2 else (False,)):
                data = dict(passed=False)
                row["stages"]["mask_c2_"+("on" if c2 else "off")] = data
                start = time.perf_counter()
                try:
                    with smoke.deadline(args.limit_s):
                        smoke.token_replay(instance, plan, c2, data, require_complete=False)
                except Exception as exc:
                    data["error"] = f"{type(exc).__name__}: {exc}"
                data["wall_s"] = time.perf_counter()-start
                smoke.summarize_replay(data)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["source_sha256_end"] = smoke.source_hash()
        files_after = smoke.source_files()
        row["changed_source_files"] = sorted(p for p in files_before.keys() | files_after.keys()
            if files_before.get(p) != files_after.get(p))
        row["current_tree_evidence_valid"] = not any(smoke.relevant_to_smoke(p) for p in row["changed_source_files"])
        smoke.save(directory / "partial_replay.json", row)
        rows.append(row)
        smoke.save(args.directory / "partial-replay-results.json", rows)
        print(json.dumps(dict(case=row["case"], error=row.get("error"),
            stages={k:{kk:vv for kk,vv in v.items() if kk in ("passed","prefix_passed","error","wall_s","token_ms_median","token_ms_p95")} for k,v in row["stages"].items()})), flush=True)


if __name__ == "__main__":
    main()
