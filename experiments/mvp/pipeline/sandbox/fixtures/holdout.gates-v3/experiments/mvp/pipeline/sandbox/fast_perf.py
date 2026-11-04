"""Low-load measurements of the unchanged v3 fast path and exact part reuse.

Only sandbox call references are temporarily wrapped for observational timing.
Canonical checker/provider globals and all returned facts remain unchanged.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import cProfile
from datetime import datetime, timezone
from functools import wraps
import hashlib
import importlib
import json
import os
from pathlib import Path
import pstats
import statistics
from time import perf_counter, sleep

from .benchmark import HERE, ROOT, admission, read_fixture
from .measure_securing import _load_reading


def digest(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def low_load_admission(max_load=20.):
    while True:
        state = admission()
        if state["load1"] <= max_load:
            return state
        sleep(10)


@contextmanager
def observe_calls():
    """Measure sandbox entry points; restore them even when a call fails."""
    package = "experiments.mvp.pipeline.sandbox."
    targets = [("moves", "prepare_move"), ("moves", "_counts"),
               ("physics", "fast_check"), ("physics", "check_static_rules"),
               ("securing", "screen_securing"), ("securing", "check_lashing"),
               ("visibility", "screen_visibility"), ("visibility", "check_visibility")]
    saved, times, results = [], {}, {}
    def wrapper(name, original):
        @wraps(original)
        def timed(*args, **kwargs):
            started = perf_counter()
            try:
                result = original(*args, **kwargs)
                results[name] = result
                return result
            finally:
                row = times.setdefault(name, {"calls": 0, "wall_s": 0.})
                row["calls"] += 1
                row["wall_s"] += perf_counter()-started
        return timed
    try:
        for module_name, attribute in targets:
            module = importlib.import_module(package+module_name)
            original = getattr(module, attribute)
            saved.append((module, attribute, original))
            setattr(module, attribute, wrapper(module_name+"."+attribute, original))
        yield times, results
    finally:
        for module, attribute, original in reversed(saved):
            setattr(module, attribute, original)


def public_parts(instance, ship, *, shared=False, functions=None):
    """Experimental equality/timing probe, never a plan approval or a cache.

    Public rules, securing and visibility run in the same order and always run
    afresh. ``shared`` only changes the disposable numerical-workspace scope.
    No global provider is replaced and no condition/report is cached here.
    """
    from stowage.checker import check_static_rules, check_lashing, check_visibility
    from stowage.checker.perf import evaluation_cache
    functions = functions or [("rules", check_static_rules), ("lashing", check_lashing),
                              ("visibility", check_visibility)]
    reports, times = {}, {}
    with evaluation_cache() if shared else nullcontext():
        for name, function in functions:
            started = perf_counter()
            with nullcontext() if shared else evaluation_cache():
                reports[name] = function(instance, ship, call_index=instance.call_index)
            times[name] = perf_counter()-started
    return reports, times


def _profile_rows(profiler, sort_field, count=45):
    rows = []
    for (filename, line, function), (primitive, calls, self_s, cumulative_s, _) in pstats.Stats(profiler).stats.items():
        rows.append({"file": filename, "line": line, "function": function,
                     "primitive_calls": primitive, "calls": calls,
                     "self_s": self_s, "cumulative_s": cumulative_s})
    return sorted(rows, key=lambda row: row[sort_field], reverse=True)[:count]


def record(source, requests_file, output, *, count=3, max_load=20.):
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[name] = "1"
    print(f"Waiting for fresh low-load admission <= {max_load}", flush=True)
    first = low_load_admission(max_load)
    from stowage.checker import check, report_matches_inputs
    from stowage.model import Instance, MovePlan
    from . import moves
    source, requests_file, output = Path(source), Path(requests_file), Path(output)
    fixture, stored = read_fixture(source), read_fixture(requests_file)
    requests, seen = [], set()
    for row in stored["moves"]:
        key = (row["box_id"], row["target_slot"])
        if row["full"]["legal"] and key not in seen:
            requests.append(key)
            seen.add(key)
        if len(requests) == 2*count:
            break
    if len(requests) < 2*count:
        raise ValueError("not enough distinct prior development requests")
    instance, plan = Instance.model_validate(fixture["instance"]), MovePlan.model_validate(fixture["plan"])
    source_paths = ["moves.py", "physics.py", "securing.py", "visibility.py", "fast_perf.py"]
    data = {"synthetic": True, "mode": "recorded", "number_label": "measured",
        "created_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
        "source_fixture": source.name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "instance_sha256": digest(instance), "plan_sha256": digest(plan),
        "requests_source": requests_file.name, "requests_source_sha256": hashlib.sha256(requests_file.read_bytes()).hexdigest(),
        "source_hashes": {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in source_paths},
        "initial_admission": first, "max_admission_load1": max_load,
        "selection": "First six distinct full-legal requests from the original development clear190 recording, fixed before profiling. No holdout inputs/outcomes accessed.",
        "method": "Fresh process, native thread caps 1, canonical base validation first. Distinct requests run current v3 fast_screen, first half without cProfile and second half with cProfile. Lightweight observational wrappers time sandbox endpoints and capture unchanged returned checker PartReports. Parsing/validation/startup excluded; prepare/replay included. Each call starts at fresh admission <= stated low-load threshold. Profile wall includes cProfile overhead and is not a latency estimate.",
        "baseline": None, "unprofiled": [], "profiled": [], "complete": False}
    def save():
        output.write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
    load = low_load_admission(max_load)
    started = perf_counter()
    baseline = check(instance, plan)
    data["baseline"] = {"wall_s": perf_counter()-started, "admission": load,
        "end_load": _load_reading(), "passed": baseline.passed,
        "bound": report_matches_inputs(baseline, instance, plan),
        "parts": {name: {"ran": part.ran, "error": part.error} for name, part in baseline.parts.items()}}
    save()
    print(json.dumps({"phase": "baseline", **data["baseline"]}), flush=True)
    if not baseline.passed or not data["baseline"]["bound"]:
        raise ValueError("base plan failed canonical validation")
    for index, (box, target) in enumerate(requests):
        profiled = index >= count
        load = low_load_admission(max_load)
        profiler = cProfile.Profile() if profiled else None
        with observe_calls() as (components, captured):
            started = perf_counter()
            if profiler:
                profiler.enable()
            verdict = moves.fast_screen(instance, plan, box, target)
            if profiler:
                profiler.disable()
            wall_s = perf_counter()-started
        ended = _load_reading()
        reports = {name: value.model_dump(mode="json") for name, value in captured.items()
                   if name in {"physics.check_static_rules", "securing.check_lashing", "visibility.check_visibility"}}
        prepared = captured.get("moves.prepare_move")
        row = {"box_id": box, "target_slot": target, "admission": load, "end_load": ended,
               "wall_s": wall_s, "components": components, "verdict": verdict.model_dump(mode="json"),
               "part_reports": reports, "part_hashes": {name: digest(value) for name, value in reports.items()},
               "candidate_ship_sha256": digest(prepared.ship) if prepared else None}
        if profiler:
            profile_path = output.with_name(f"perf_fast190_{index-count}.prof")
            profiler.dump_stats(str(profile_path))
            row.update(profile_file=profile_path.name,
                       by_cumulative=_profile_rows(profiler, "cumulative_s"),
                       by_self=_profile_rows(profiler, "self_s"))
        data["profiled" if profiled else "unprofiled"].append(row)
        save()
        print(json.dumps({"phase": "profiled" if profiled else "unprofiled", "request": [box,target],
                          "wall_s": wall_s, "legal": verdict.legal, "components": components}), flush=True)
    data["baseline_inputs_still_match"] = report_matches_inputs(baseline, instance, plan)
    data["complete"] = len(data["unprofiled"]) == count and len(data["profiled"]) == count and data["baseline_inputs_still_match"]
    data["unprofiled_summary"] = {"median_s": statistics.median(row["wall_s"] for row in data["unprofiled"]),
        "min_s": min(row["wall_s"] for row in data["unprofiled"]), "max_s": max(row["wall_s"] for row in data["unprofiled"])}
    save()
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE/"fixtures/envelope190.json")
    parser.add_argument("--requests", type=Path, default=HERE/"fixtures/clear190.json")
    parser.add_argument("--output", type=Path, default=HERE/"fixtures/perf_fast190.json")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--max-load", type=float, default=20.)
    args = parser.parse_args()
    record(args.source, args.requests, args.output, count=args.count, max_load=args.max_load)


if __name__ == "__main__":
    main()
