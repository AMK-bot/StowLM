"""Measure first candidate securing calls after canonical base validation.

One fresh process; no securing call precedes check(instance, original_plan).
Every timed call has fresh fleet admission and native thread caps of one.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

from .benchmark import HERE, admission, read_fixture
from .measure_securing import KNOWN, _load_reading


def measure(source, requests_file, output):
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[variable] = "1"
    initial = admission()
    from stowage.checker import check, report_matches_inputs
    from stowage.model import Instance, MovePlan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from .moves import prepare_move
    from .securing import screen_securing

    source, requests_file = Path(source), Path(requests_file)
    fixture = read_fixture(source)
    stored = read_fixture(requests_file)
    rows = stored["moves"]
    known = next(row for row in rows if (row["box_id"], row["target_slot"]) == KNOWN)
    legal = [row for row in rows if row["full"]["legal"]][:2]
    if len(legal) != 2 or known["full"]["legal"]:
        raise ValueError("need known failure and two stored legal requests")
    instance = Instance.model_validate(fixture["instance"])
    plan = MovePlan.model_validate(fixture["plan"])
    requests = [{"box_id": row["box_id"], "target_slot": row["target_slot"],
                 "stored_full_legal": row["full"]["legal"]} for row in [known, *legal]]
    source_hashes = {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                     for name in ("securing.py", "measure_securing_basewarm.py", "moves.py", "physics.py")}
    data = {"synthetic": True, "mode": "recorded", "number_label": "measured",
        "label": "First candidate securing calls after canonical base validation",
        "created_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
        "source_fixture": source.name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "requests_source": requests_file.name,
        "requests_source_sha256": hashlib.sha256(requests_file.read_bytes()).hexdigest(),
        "source_hashes": source_hashes, "initial_admission": initial,
        "method": "Fresh process, native thread caps 1. Canonical check(instance, original_plan) runs first with default arguments and no preceding securing call. Then the first securing screen for each of three distinct requests runs in that same process, with fresh per-call admission and fresh evaluation_cache. No report/verdict is reused to evaluate a candidate. Typed model loading and preparation are outside candidate-call latency.",
        "baseline": None, "cases": [], "complete": False}
    load = admission()
    print(json.dumps({"phase": "canonical_base_check_start", "pid": os.getpid(), "admission": load}), flush=True)
    started = perf_counter()
    report = check(instance, plan)
    wall_s = perf_counter()-started
    baseline_matches = report_matches_inputs(report, instance, plan)
    required = {"moves", "rules", "stability", "strength", "visibility", "lashing", "constraints", "voyage"}
    all_ran = required <= set(report.parts) and all(part.ran and not part.error for part in report.parts.values())
    data["baseline"] = {"passed": report.passed, "wall_s": wall_s, "admission": load,
        "end_load": _load_reading(), "input_binding_matches": baseline_matches,
        "input_bindings": [value for value in report.provenance.parents
                           if value.startswith(("checker.instance.sha256:", "checker.plan.sha256:"))],
        "rule_freeze_v1_active": rule_freeze_v1_enabled(),
        "all_required_parts_ran": all_ran,
        "parts": {name: {"ran": part.ran, "error": part.error,
                         "hard_violations": sum(v.severity == "hard" for v in part.violations)}
                  for name, part in report.parts.items()}, "report": report.model_dump(mode="json")}
    Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"phase": "canonical_base_check_done", "wall_s": wall_s,
        "passed": report.passed, "input_binding_matches": baseline_matches,
        "all_required_parts_ran": all_ran}), flush=True)
    if not report.passed or not baseline_matches or not all_ran:
        raise ValueError("canonical baseline did not pass every required gate")
    for request in requests:
        admission()
        started = perf_counter()
        prepared = prepare_move(instance, plan, request["box_id"], request["target_slot"])
        prepare_s = perf_counter()-started
        if not prepared.replay.legal or prepared.replay.executed != len(prepared.plan.moves):
            raise ValueError("stored request does not replay completely")
        load = admission()
        broken, screen = screen_securing(instance, prepared.ship)
        canonical_failure = any(row["rule_id"] == "iacs.member_irs.container_securing" and
            row.get("measured") is not None and row.get("limit") == 1. and row["measured"] > 1.
            for row in broken)
        confirmed = screen["passed"] if request["stored_full_legal"] else canonical_failure
        case = {**request, "prepare_s": prepare_s, "admission": load, "end_load": _load_reading(),
                "wall_s": screen["wall_s"], "broken": broken, "screen": screen,
                "expected_result_confirmed": confirmed, "first_call_for_this_request": True}
        data["cases"].append(case)
        Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
        print(json.dumps({"phase": "candidate_done", "box_id": request["box_id"],
            "target_slot": request["target_slot"], "wall_s": screen["wall_s"],
            "confirmed": confirmed, "max_fitting_ratio": screen["max_fitting_ratio"]}), flush=True)
    data["inputs_still_match_baseline"] = report_matches_inputs(report, instance, plan)
    data["complete"] = len(data["cases"]) == 3 and data["inputs_still_match_baseline"] and all(
        case["expected_result_confirmed"] for case in data["cases"])
    Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE/"fixtures/envelope190.json")
    parser.add_argument("--requests", type=Path, default=HERE/"fixtures/clear190.json")
    parser.add_argument("--output", type=Path, default=HERE/"fixtures/securing190_basewarm.json")
    args = parser.parse_args()
    measure(args.source, args.requests, args.output)


if __name__ == "__main__":
    main()
