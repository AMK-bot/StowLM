"""Admitted standalone cold/second-call costs on three stored 190-box drags.

Each request runs in a fresh spawned process, with native thread caps of one.
Source parsing/model validation/preparation are outside securing-call latency.
No full plan checker, planner, network or paid model is called.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import statistics
from time import perf_counter

from .benchmark import HERE, ROOT, admission, read_fixture


KNOWN = ("QZXU0000605", "0100688")


def _load_reading():
    try:
        return json.loads((ROOT/"experiments/fleet/core-state.json").read_text())
    except (OSError, ValueError):
        return None


def _measure_case(payload):
    source, request = payload
    initial = admission()
    # Native libraries are imported only after admission and after thread caps
    # were inherited by this fresh spawned process.
    from stowage.model import Instance, MovePlan
    from .moves import prepare_move
    from .securing import screen_securing
    fixture = read_fixture(source)
    instance, plan = Instance.model_validate(fixture["instance"]), MovePlan.model_validate(fixture["plan"])
    started = perf_counter()
    prepared = prepare_move(instance, plan, request["box_id"], request["target_slot"])
    prepare_s = perf_counter()-started
    if not prepared.replay.legal or prepared.replay.executed != len(prepared.plan.moves):
        raise ValueError("stored request no longer replays completely")
    calls = []
    for temperature in ("cold", "warm"):
        load = admission()
        broken, screen = screen_securing(instance, prepared.ship)
        calls.append({"temperature": temperature, "admission": load, "end_load": _load_reading(),
                      "wall_s": screen["wall_s"], "broken": broken, "screen": screen})
    expected_clear = request["stored_full_legal"]
    canonical_failure = all(any(row["rule_id"] == "iacs.member_irs.container_securing" and
        row.get("measured") is not None and row.get("limit") == 1. and row["measured"] > 1.
        for row in call["broken"]) for call in calls)
    return {**request, "pid": os.getpid(), "initial_admission": initial,
            "prepare_s": prepare_s, "replay_legal": True, "calls": calls,
            "expected_result_confirmed": (all(call["screen"]["passed"] for call in calls)
                if expected_clear else canonical_failure),
            "note": "Stored full verdict labels the request; it was not rerun by this recorder."}


def measure(source, requests_file, output):
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[name] = "1"
    print("Waiting for fresh fleet admission before source loading and measurement", flush=True)
    initial = admission()
    stored = read_fixture(requests_file)
    rows = stored["moves"]
    known = next(row for row in rows if (row["box_id"], row["target_slot"]) == KNOWN)
    legal = [row for row in rows if row["full"]["legal"]][:2]
    if len(legal) != 2 or known["full"]["legal"]:
        raise ValueError("require known failing request plus two stored legal requests")
    requests = [{"box_id": row["box_id"], "target_slot": row["target_slot"],
                 "kind": "known_fitting_failure" if index == 0 else "stored_legal",
                 "stored_full_legal": row["full"]["legal"]}
                for index, row in enumerate([known, *legal])]
    source = Path(source)
    data = {"synthetic": True, "mode": "recorded", "number_label": "measured",
        "label": "Measured standalone departure securing checks",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_fixture": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "requests_source": Path(requests_file).name,
        "requests_source_sha256": hashlib.sha256(Path(requests_file).read_bytes()).hexdigest(),
        "initial_admission": initial,
        "source_hashes": {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                          for name in ("securing.py", "measure_securing.py")},
        "method": "Three requests, serial fresh spawned processes, native thread caps 1; cold = first securing call after typed source validation and move preparation, warm = second identical call in the same process. Every call waits for fresh fleet admission. Preparation, JSON parsing/model validation and process startup are excluded from call latency. No full checker rerun.",
        "cases": [], "complete": False}
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                             max_tasks_per_child=1) as pool:
        for request in requests:
            result = pool.submit(_measure_case, (str(source), request)).result()
            data["cases"].append(result)
            print(json.dumps({"box_id": result["box_id"], "target_slot": result["target_slot"],
                "confirmed": result["expected_result_confirmed"],
                "cost_s": {call["temperature"]: call["wall_s"] for call in result["calls"]},
                "fitting_ratio": [call["screen"]["max_fitting_ratio"] for call in result["calls"]]}), flush=True)
            Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
    data["complete"] = len(data["cases"]) == 3 and all(case["expected_result_confirmed"] for case in data["cases"])
    data["summary"] = {}
    for temperature in ("cold", "warm"):
        values = [call["wall_s"] for case in data["cases"] for call in case["calls"]
                  if call["temperature"] == temperature]
        data["summary"][temperature] = {"calls": len(values), "seconds": values,
            "median_s": statistics.median(values), "min_s": min(values), "max_s": max(values)}
    Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE/"fixtures/envelope190.json")
    parser.add_argument("--requests", type=Path, default=HERE/"fixtures/clear190.json")
    parser.add_argument("--output", type=Path, default=HERE/"fixtures/securing190.json")
    args = parser.parse_args()
    measure(args.source, args.requests, args.output)


if __name__ == "__main__":
    main()
