"""Pinned, sequential old/new subprocess gate; exact prefix events are archived.

Unlike replay.py's in-process diagnostic this imports only one selected arm in
each worker. Both workers are alive, but the coordinator submits work to only
one at a time. Run with PYTHONHASHSEED=0 and native thread limits of one.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import traceback

from . import replay


class GuardInvalidated(RuntimeError):
    """An input/source epoch changed; no behavioral difference is implied."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def manifest():
    hashes = replay.source_hashes()
    # Test/harness changes do not affect runtime semantics; record this harness.
    hashes = {p: h for p, h in hashes.items() if not p.startswith("tests/")}
    for path in (Path(__file__), replay.ROOT / "data/raw/kcs/kcs.fix"):
        hashes[str(path.relative_to(replay.ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def baseline_verified():
    pinned = json.loads((replay.HERE / "out/guard-pinned0.json").read_text())["source_before"]
    result = {}
    for name in ("mask.py", "securing_mask_filter.py", "lookahead_mask_filter.py"):
        path = replay.HERE / "baseline" / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        key = str(path.relative_to(replay.ROOT))
        if digest != pinned[key]:
            raise AssertionError(f"baseline hash mismatch: {key}")
        result[key] = digest
    return result


def runtime(arm):
    if arm == "before":
        base = replay.HERE / "baseline"
        return replay.Runtime(
            replay.load_source(base / "mask.py", "llm.maskspeed._guard_old_mask"),
            replay.load_source(base / "securing_mask_filter.py", "llm.maskspeed._guard_old_securing"),
            arm,
            replay.load_source(base / "lookahead_mask_filter.py", "llm.maskspeed._guard_old_lookahead"),
        )
    from llm.stowlm import mask
    from llm.securing import mask_filter as securing
    from llm.lookahead import mask_filter as lookahead
    return replay.Runtime(mask, securing, arm, lookahead)


def execute_trace(trace, selected, emit):
    from stowage.actions import get_action_space
    space = get_action_space()
    instance = replay.instance_for(trace)
    codec = replay.codec_for(instance)
    decoder = replay.make_decoder(instance, selected, codec, lookahead=trace.get("lookahead", 0))
    rng = random.Random(trace["seed"])
    steps = partial_pops = committed_pops = engine_pops = prefetches = 0
    digest = hashlib.sha256()

    def event(row):
        digest.update(canonical(row).encode())
        emit(row)

    def query(label="decode"):
        nonlocal steps
        legal = decoder.allowed_token_ids()
        event(dict(type="prefix", label=label, step=steps, legal=list(legal),
                   status=decoder.status, actions=[space.encode(a) for a in decoder.actions],
                   tokens=list(decoder.tokens), depth=decoder.engine.depth))
        steps += 1
        return legal

    def advance(token):
        result = decoder.advance(token)
        event(dict(type="advance", token=token,
                   committed=space.encode(result) if result is not None else None))

    if trace.get("actions") is not None:
        for action in trace["actions"]:
            for token in codec.encode_action(space.decode(action)):
                legal = query()
                if token not in legal:
                    event(dict(type="historical_rejection", token=token))
                    return dict(status="historical_unreplayable", steps=steps,
                                transcript_sha256=digest.hexdigest())
                advance(token)
        if trace["expected_status"] == "complete":
            for token in codec.end_plan_tokens:
                legal = query()
                if token not in legal:
                    event(dict(type="historical_rejection", token=token))
                    return dict(status="historical_unreplayable", steps=steps,
                                transcript_sha256=digest.hexdigest())
                advance(token)
        query()
        if decoder.status != trace["expected_status"]:
            return dict(status="historical_unreplayable", steps=steps,
                        transcript_sha256=digest.hexdigest())
    else:
        while True:
            legal = query()
            if not legal:
                break
            if steps % 11 == 0:
                decoder.prefetch()
                prefetches += 1
                query("prefetched")
            if trace.get("backtrack", True) and decoder.engine.depth == 3 and not partial_pops:
                # Exercise both partially written and fully committed pop paths.
                advance(legal[rng.randrange(len(legal))])
                query("partial-before-pop")
                event(dict(type="pop_partial", action=space.encode(decoder.pop_action())))
                partial_pops += 1
                query("partial-after-pop")
                event(dict(type="pop_committed", action=space.encode(decoder.pop_action())))
                committed_pops += 1
                query("committed-after-pop")
                # Decoder backtracking restores saved engine copies. Separately
                # exercise the wrappers' own mutable push/pop implementations.
                clone = decoder.engine.copy()
                actions = clone.legal_actions()
                if actions:
                    action = sorted(actions, key=lambda a: canonical(space.encode(a)))[0]
                    def engine_event(label):
                        event(dict(type="engine_probe", label=label,
                                   legal=sorted((space.encode(a) for a in clone.legal_actions()), key=canonical),
                                   actions=[space.encode(a) for a in clone.actions()]))
                    engine_event("before-push")
                    clone.push(action)
                    engine_event("after-push")
                    clone.pop()
                    engine_event("after-pop")
                    engine_pops += 1
                continue
            advance(legal[rng.randrange(len(legal))])
            if steps > 5000:
                raise AssertionError(f"unterminated trajectory: {trace['id']}")
    final = dict(type="final", status=decoder.status,
                 actions=[space.encode(a) for a in decoder.actions], tokens=list(decoder.tokens),
                 plan=decoder.engine.to_plan().model_dump(mode="json") if decoder.status == "complete" else None)
    event(final)
    return dict(status=decoder.status, steps=steps, actions=len(decoder.actions),
                partial_pops=partial_pops, committed_pops=committed_pops, engine_pops=engine_pops,
                prefetches=prefetches, transcript_sha256=digest.hexdigest())


def worker(arm):
    baseline_verified()
    selected = runtime(arm)
    def emit(row):
        print(canonical(row), flush=True)
    emit(dict(type="ready", pid=os.getpid(), arm=arm, hashes=manifest()))
    for raw in sys.stdin:
        trace = json.loads(raw)
        try:
            result = execute_trace(trace, selected, emit)
            emit(dict(type="done", id=trace["id"], result=result))
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            emit(dict(type="error", id=trace["id"], error=f"{type(error).__name__}: {error}"))
            return


def corpus_maps(split, size, n):
    root = replay.ROOT / "experiments/mvp/corpus" / split / size
    pools = [sorted(p for p in (root / ship / "simulated_yard").glob("seed-*.json")
                    if not p.name.endswith(".audit.json"))
             for ship in ("feeder_500", "feeder_800", "feeder_1700")]
    return [p for i in range(max(map(len, pools))) for pool in pools if i < len(pool) for p in [pool[i]]][:n]


def traces_for(args):
    traces = [dict(id=f"fixture-tiny-{seed}", kind="tiny", seed=seed,
                   lookahead=8 if seed % 4 == 0 else 0, split="fixture")
              for seed in range(args.tiny)]
    for split in ("train", "val"):
        for size in ("tiny", "small"):
            for i, path in enumerate(corpus_maps(split, size, args.maps)):
                traces.append(dict(id=f"{split}-{size}-{i}", kind="small", size=size,
                                   map=str(path.relative_to(replay.ROOT)), seed=2718 + i,
                                   split=split, lookahead=0, backtrack=size == "tiny"))
    # Historical complete and genuine dead-end model traces, not synthetic stops.
    saved = list(replay.saved_traces())
    for status in ("complete", "dead_end"):
        chosen = [t for t in saved if t["expected_status"] == status][:args.saved_per_status]
        for i, trace in enumerate(chosen):
            traces.append(dict(trace, seed=i, split="train", size="small", backtrack=False))
    return traces


def compare_event(expected, actual, trace_id, index):
    if expected != actual:
        raise AssertionError(f"{trace_id} event {index} differs: {canonical(expected)} != {canonical(actual)}")


def coordinator(args):
    started = time.monotonic()
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    before_manifest = manifest()
    traces = traces_for(args)
    input_manifest = {trace["map"]: hashlib.sha256((replay.ROOT / trace["map"]).read_bytes()).hexdigest()
                      for trace in traces if trace.get("map")}
    result = dict(schema_version=1, baseline_hashes=baseline_verified(), source_before=before_manifest,
                  input_manifest=input_manifest,
                  cpu_only=True, workers_cpu_active_max=1, hashseed="0", traces=[], excluded=[],
                  differences=0, exact=False, requested_traces=len(traces), pids={}, worker_ready={})
    workers, logs = {}, []
    def save():
        result["elapsed_s"] = time.monotonic() - started
        output.write_text(json.dumps(result, indent=2) + "\n")
    try:
        for arm in ("before", "after"):
            log = output.with_name(output.stem + f"-{arm}.stderr.log").open("w")
            logs.append(log)
            process = subprocess.Popen([sys.executable, "-m", "llm.maskspeed.crossprocess_guard", "--worker", arm],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                text=True, bufsize=1, env=dict(os.environ, PYTHONHASHSEED="0"))
            workers[arm] = process
            result["pids"][arm] = process.pid
            save()
            raw = process.stdout.readline()
            ready = json.loads(raw)
            if ready.get("type") != "ready":
                raise AssertionError(f"worker not ready: {ready}")
            if before_manifest != ready["hashes"]:
                result["source_delta"] = {path: dict(before=before_manifest.get(path), after=ready["hashes"].get(path))
                                          for path in before_manifest.keys() | ready["hashes"].keys()
                                          if before_manifest.get(path) != ready["hashes"].get(path)}
                raise GuardInvalidated("worker-source epoch differs")
            result["worker_ready"][arm] = ready
        transcript_path = output.with_suffix(".transcripts.jsonl")
        with transcript_path.open("w") as transcript:
            for trace in traces:
                baseline_events = []
                summaries = {}
                for arm in ("before", "after"):
                    if trace.get("map"):
                        current_input = hashlib.sha256((replay.ROOT / trace["map"]).read_bytes()).hexdigest()
                        if current_input != input_manifest[trace["map"]]:
                            raise GuardInvalidated(f"input map changed: {trace['map']}")
                    process = workers[arm]
                    process.stdin.write(canonical(trace) + "\n")
                    process.stdin.flush()
                    index = 0
                    while True:
                        raw = process.stdout.readline()
                        if not raw:
                            raise RuntimeError(f"{arm} worker exited (see stderr): {process.poll()}")
                        row = json.loads(raw)
                        transcript.write(canonical(dict(trace_id=trace["id"], arm=arm, event=row)) + "\n")
                        transcript.flush()
                        if row["type"] == "error":
                            raise RuntimeError(f"{arm} worker: {row}")
                        if arm == "before":
                            baseline_events.append(row)
                        else:
                            if index >= len(baseline_events):
                                raise AssertionError(f"{trace['id']}: additional new-arm event")
                            compare_event(baseline_events[index], row, trace["id"], index)
                        index += 1
                        if row["type"] == "done":
                            summaries[arm] = row["result"]
                            break
                    if arm == "after" and index != len(baseline_events):
                        raise AssertionError(f"{trace['id']}: missing new-arm events")
                row = dict(trace, **summaries["before"], exact=True)
                if row["status"] == "historical_unreplayable":
                    result["excluded"].append(row)
                else:
                    result["traces"].append(row)
                save()
                print(canonical(dict(id=row["id"], status=row["status"], steps=row["steps"],
                                     count=len(result["traces"]), elapsed_s=result["elapsed_s"])), flush=True)
        result["source_after"] = manifest()
        result["input_manifest_after"] = {path: hashlib.sha256((replay.ROOT / path).read_bytes()).hexdigest()
                                          for path in input_manifest}
        if result["input_manifest_after"] != input_manifest:
            raise GuardInvalidated("input map changed during guard")
        result["source_stable"] = result["source_after"] == before_manifest
        if not result["source_stable"]:
            result["source_delta"] = {path: dict(before=before_manifest.get(path), after=result["source_after"].get(path))
                                      for path in before_manifest.keys() | result["source_after"].keys()
                                      if before_manifest.get(path) != result["source_after"].get(path)}
            raise GuardInvalidated("runtime source/asset changed during guard")
        result["summary"] = dict(traces=len(result["traces"]),
            prefixes=sum(t["steps"] for t in result["traces"]),
            statuses=dict(Counter(t["status"] for t in result["traces"])),
            composition=dict(Counter(f"{t['split']}/{t.get('size', 'tiny')}" for t in result["traces"])),
            partial_pops=sum(t["partial_pops"] for t in result["traces"]),
            committed_pops=sum(t["committed_pops"] for t in result["traces"]),
            engine_pops=sum(t["engine_pops"] for t in result["traces"]),
            prefetches=sum(t["prefetches"] for t in result["traces"]))
        result["exact"] = True
    except GuardInvalidated as error:
        result["invalidated"] = True
        result["error"] = str(error)
        raise
    except AssertionError as error:
        result["differences"] = 1
        result["error"] = str(error)
        raise
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        save()
        for process in workers.values():
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()  # only this coordinator's recorded child
                    process.wait(timeout=10)
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("before", "after"))
    parser.add_argument("--tiny", type=int, default=200)
    parser.add_argument("--maps", type=int, default=3)
    parser.add_argument("--saved-per-status", type=int, default=1)
    parser.add_argument("--output", type=Path, default=replay.HERE / "out/guard2.json")
    args = parser.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        parser.error("launch interpreter with PYTHONHASHSEED=0")
    if args.worker:
        worker(args.worker)
    else:
        coordinator(args)


if __name__ == "__main__":
    main()
