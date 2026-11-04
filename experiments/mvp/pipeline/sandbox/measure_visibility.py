"""Measure exact departure visibility after securing in a base-validated worker."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

from .benchmark import HERE, ROOT, admission, read_fixture
from .measure_securing import _load_reading

KNOWN = ("QZXU0001601", "0090390")


def _digest(model):
    return hashlib.sha256(json.dumps(model.model_dump(mode="json"), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def measure(source, requests_file, output):
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[variable] = "1"
    initial = admission()
    from stowage.checker import check, check_visibility, report_matches_inputs
    from stowage.checker.perf import evaluation_cache
    from stowage.model import Instance, MovePlan
    from .moves import prepare_move
    from .securing import screen_securing

    source, requests_file = Path(source), Path(requests_file)
    fixture, stored = read_fixture(source), read_fixture(requests_file)
    rows = stored["moves"]
    known = next(row for row in rows if (row["box_id"], row["target_slot"]) == KNOWN)
    legal = [row for row in rows if row["full"]["legal"]][:2]
    if len(legal) != 2 or known["full"]["legal"]:
        raise ValueError("require known visibility failure plus two stored legal requests")
    instance, plan = Instance.model_validate(fixture["instance"]), MovePlan.model_validate(fixture["plan"])
    hashes = ["experiments/mvp/pipeline/sandbox/measure_visibility.py",
              "experiments/mvp/pipeline/sandbox/securing.py",
              "experiments/mvp/pipeline/sandbox/moves.py", "experiments/mvp/pipeline/sandbox/physics.py",
              "src/stowage/checker/full.py", "src/stowage/checker/lashing.py",
              "src/stowage/checker/visibility.py", "src/stowage/checker/perf.py"]
    data = {"synthetic": True, "mode": "recorded", "number_label": "measured",
        "label": "Incremental public departure visibility cost after exact securing",
        "created_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
        "source_fixture": source.name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "requests_source": requests_file.name,
        "requests_source_sha256": hashlib.sha256(requests_file.read_bytes()).hexdigest(),
        "instance_sha256": _digest(instance), "plan_sha256": _digest(plan),
        "source_hashes": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in hashes},
        "initial_admission": initial,
        "method": "One fresh process with native thread caps 1. Canonical check(instance, original_plan) first, with default arguments. Three distinct prepared requests then call current screen_securing followed by public check_visibility on the identical requested departure ship. Each numerical call starts at fresh fleet admission. Visibility gets its own evaluation_cache, as does screen_securing. Every criterion is recomputed; no verdict/report fact is reused. Fixture loading, typed validation, move preparation and admission waits are excluded from numerical-call latency.",
        "baseline": None, "cases": [], "complete": False}

    def save():
        Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")

    load = admission()
    print(json.dumps({"phase": "canonical_base_start", "pid": os.getpid(), "admission": load}), flush=True)
    started = perf_counter()
    baseline = check(instance, plan)
    wall_s = perf_counter()-started
    bound = report_matches_inputs(baseline, instance, plan)
    required = {"moves", "rules", "stability", "strength", "visibility", "lashing", "constraints", "voyage"}
    ran = required <= baseline.parts.keys() and all(part.ran and not part.error for part in baseline.parts.values())
    data["baseline"] = {"passed": baseline.passed, "input_binding_matches": bound,
        "all_required_parts_ran": ran, "admission": load, "end_load": _load_reading(),
        "wall_s": wall_s, "report": baseline.model_dump(mode="json")}
    save()
    print(json.dumps({"phase": "canonical_base_done", "wall_s": wall_s,
        "passed": baseline.passed, "bound": bound, "all_required_parts_ran": ran}), flush=True)
    if not baseline.passed or not bound or not ran:
        raise ValueError("canonical baseline failed required gates")
    for stored_row in [known, *legal]:
        admission()
        started = perf_counter()
        prepared = prepare_move(instance, plan, stored_row["box_id"], stored_row["target_slot"])
        prepare_s = perf_counter()-started
        if not prepared.replay.legal or prepared.replay.executed != len(prepared.plan.moves):
            raise ValueError("stored request does not replay completely")
        ship_hash = _digest(prepared.ship)
        load = admission()
        securing_broken, securing = screen_securing(instance, prepared.ship)
        securing_end_load = _load_reading()
        securing_input_unchanged = ship_hash == _digest(prepared.ship)
        if not securing["passed"]:
            raise ValueError("visibility measurement requires a passing departure securing screen")
        load_visibility = admission()
        started = perf_counter()
        with evaluation_cache():
            visibility = check_visibility(instance, prepared.ship, call_index=instance.call_index)
        visibility_s = perf_counter()-started
        hard = [violation.model_dump(mode="json") for violation in visibility.violations if violation.severity == "hard"]
        passed = bool(visibility.ran and not visibility.error and not hard)
        confirmed = passed if stored_row["full"]["legal"] else any(
            row["rule_id"] == "solas.v22.visibility" and row.get("measured") is not None and
            row.get("limit") is not None and row["measured"] > row["limit"] for row in hard)
        case = {"box_id": prepared.box_id, "target_slot": prepared.target_slot,
            "stored_full_legal": stored_row["full"]["legal"], "prepare_s": prepare_s,
            "candidate_plan_sha256": _digest(prepared.plan), "ship_sha256": ship_hash,
            "securing": {"admission": load, "end_load": securing_end_load,
                         "broken": securing_broken, "screen": securing},
            "visibility": {"admission": load_visibility, "end_load": _load_reading(),
                "wall_s": visibility_s, "passed": passed, "broken": hard,
                "source": "stowage.checker.visibility.check_visibility",
                "phase": "departure", "report": visibility.model_dump(mode="json")},
            "ship_unchanged_by_securing": securing_input_unchanged,
            "ship_unchanged_by_visibility": ship_hash == _digest(prepared.ship),
            "expected_result_confirmed": confirmed}
        data["cases"].append(case)
        save()
        print(json.dumps({"phase": "candidate_done", "box_id": prepared.box_id,
            "target_slot": prepared.target_slot, "securing_s": securing["wall_s"],
            "incremental_visibility_s": visibility_s, "visibility_passed": passed,
            "confirmed": confirmed, "broken": [(row["rule_id"], row["measured"], row["limit"]) for row in hard]}), flush=True)
    data["baseline_inputs_still_match"] = report_matches_inputs(baseline, instance, plan)
    data["complete"] = len(data["cases"]) == 3 and data["baseline_inputs_still_match"] and all(
        case["expected_result_confirmed"] and case["ship_unchanged_by_securing"] and
        case["ship_unchanged_by_visibility"] for case in data["cases"])
    save()
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE/"fixtures/envelope190.json")
    parser.add_argument("--requests", type=Path, default=HERE/"fixtures/clear190-current.json")
    parser.add_argument("--output", type=Path, default=HERE/"fixtures/visibility190.json")
    args = parser.parse_args()
    measure(args.source, args.requests, args.output)


if __name__ == "__main__":
    main()
