"""Checkpointed full-trajectory timing using the reviewed replay benchmark.

Run one arm per process after the exact replay guard passes, then summarize.
This module changes no production class or existing replay implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["RAYON_NUM_THREADS"] = "1"

from llm.maskspeed import replay


def manifest(input_maps=()):
    hashes = replay.source_hashes()
    paths = [Path(__file__), replay.ROOT / "data/raw/kcs/kcs.fix",
             replay.ROOT / "models/glimmer-30b-4bit/tokenizer.json", *input_maps]
    paths += sorted((replay.ROOT / "params").rglob("*.toml"))
    for path in paths:
        hashes[str(path.relative_to(replay.ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def epoch(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def drift(before, after):
    return {name: dict(before=before.get(name), after=after.get(name))
            for name in sorted(before.keys() | after.keys())
            if before.get(name) != after.get(name)}


def summarize(paths, output):
    runs = [json.loads(path.read_text()) for path in paths]
    if any(not run.get("complete") or not run.get("source_stable") for run in runs):
        raise AssertionError("only completed stable timing runs qualify")
    if any(run["source_before"] != runs[0]["source_before"] for run in runs[1:]):
        raise AssertionError("timing source epochs differ")
    rows = [row for run in runs for row in run["benchmarks"]]
    arms = {}
    for row in rows:
        key = (row["map"], row["B"])
        if key in arms.setdefault(row["runtime"], {}):
            raise AssertionError(f"duplicate timing row: {row['runtime']} {key}")
        arms[row["runtime"]][key] = row
    if set(arms) != {"before", "after"} or arms["before"].keys() != arms["after"].keys():
        raise AssertionError("old/new map and batch coverage differs")
    for key, before in arms["before"].items():
        after = arms["after"][key]
        for field in ("row_steps", "batch_steps", "completed_actions", "final_statuses",
                      "final_tokens_sha256"):
            if before[field] != after[field]:
                raise AssertionError(f"old/new timing trajectory mismatch: {key} {field}")
        if "running" in before["final_statuses"]:
            raise AssertionError(f"truncated trajectory: {key}")
    aggregates = []
    for arm in ("before", "after"):
        for batch in sorted({row["B"] for row in rows}):
            selected = [row for row in rows if row["runtime"] == arm and row["B"] == batch]
            steps = sum(row["row_steps"] for row in selected)
            batches = sum(row["batch_steps"] for row in selected)
            aggregate = dict(runtime=arm, B=batch, maps=len(selected), row_steps=steps,
                             batch_steps=batches)
            for field in ("query_cpu_ms_per_row_step", "advance_cpu_ms_per_row_step",
                          "full_cpu_ms_per_row_step", "full_ms_per_row_step"):
                aggregate[field] = sum(row[field] * row["row_steps"] for row in selected) / steps
            aggregate["full_cpu_ms_per_batch_step"] = aggregate["full_cpu_ms_per_row_step"] * steps / batches
            aggregate["full_ms_per_batch_step"] = aggregate["full_ms_per_row_step"] * steps / batches
            aggregate["layer_exclusive_cpu_ms_per_row_step"] = {
                layer: sum(row["layer_exclusive_ms_per_row_step"].get(layer, 0) * row["row_steps"]
                           for row in selected) / steps
                for layer in ("production_engine", "lookahead", "securing", "token_mask")}
            aggregate["layer_exclusive_cpu_ms_per_batch_step"] = {
                layer: value * steps / batches
                for layer, value in aggregate["layer_exclusive_cpu_ms_per_row_step"].items()}
            aggregate["instrumented_raw_cpu_ratio"] = (
                sum(row["instrumented_cpu_s"] for row in selected) / sum(row["cpu_s"] for row in selected))
            aggregates.append(aggregate)
    result = dict(exact_trajectories=True, source_stable=True, source_before=runs[0]["source_before"],
                  source_epoch_sha256=epoch(runs[0]["source_before"]),
                  source_files=[str(path) for path in paths], aggregates=aggregates, benchmarks=rows)
    save(output, result)
    print(json.dumps(aggregates, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("before", "after"))
    parser.add_argument("--actions", type=int, default=1000)
    parser.add_argument("--maps", type=int, default=6)
    parser.add_argument("--batches", default="1,8")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summarize", nargs=2, type=Path)
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize, args.output)
        return
    if not args.arm or os.environ.get("PYTHONHASHSEED") != "0":
        parser.error("benchmark requires --arm and process-start PYTHONHASHSEED=0")
    before, after = replay.runtimes()
    runtime = before if args.arm == "before" else after
    maps = replay.small_maps(args.maps)
    source_before = manifest(maps)
    start, cpu_start = time.perf_counter(), time.process_time()
    result = dict(schema_version=1, arm=args.arm, complete=False, cpu_only=True, workers=1,
                  native_threads=1, pythonhashseed=os.environ["PYTHONHASHSEED"], pid=os.getpid(),
                  thread_limits={name: os.environ[name] for name in
                                 ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                  "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
                                  "TOKENIZERS_PARALLELISM", "RAYON_NUM_THREADS")},
                  maps=[str(path.relative_to(replay.ROOT)) for path in maps],
                  batches=list(map(int, args.batches.split(","))),
                  source_before=source_before, source_epoch_sha256=epoch(source_before), benchmarks=[])
    save(args.output, result)
    # Existing benchmark selects its maps through this function. Restrict each
    # call to one selected map and batch, retaining existing instrumentation and
    # cold codec behavior, so every map/batch result is durably checkpointed.
    original_small_maps = replay.small_maps
    try:
        for path in maps:
            replay.small_maps = lambda _n, _path=path: [_path]
            for batch in map(int, args.batches.split(",")):
                result["benchmarks"] += replay.benchmark(
                    runtime, batches=(batch,), actions=args.actions, maps=1,
                    layer_clock="cpu", cold_codec=True)
                result["source_after"] = manifest(maps)
                result["source_drift"] = drift(source_before, result["source_after"])
                result.update(source_stable=not result["source_drift"],
                              wall_s=time.perf_counter() - start,
                              cpu_s=time.process_time() - cpu_start)
                save(args.output, result)
                if result["source_drift"]:
                    raise AssertionError("source/input drift at timing checkpoint: "
                                         + json.dumps(result["source_drift"], sort_keys=True))
    finally:
        replay.small_maps = original_small_maps
    result.update(wall_s=time.perf_counter() - start, cpu_s=time.process_time() - cpu_start,
                  source_after=manifest(maps))
    result["source_stable"] = result["source_before"] == result["source_after"]
    result["source_drift"] = drift(source_before, result["source_after"])
    result["complete"] = result["source_stable"]
    save(args.output, result)
    if not result["source_stable"]:
        raise AssertionError("source or runtime asset changed during timing")


if __name__ == "__main__":
    main()
