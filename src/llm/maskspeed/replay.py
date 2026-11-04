"""Reproducible old/new legal-token guards and sequential B=1/8 host profiling.

Run ``python -m llm.maskspeed.replay --help``. The CLI forces native numerical
thread limits to one before numpy is loaded; launch with PYTHONHASHSEED=0.
Frozen implementations are imported under isolated module names, never copied
over live source. Both arms use the same immutable instance and token codec but
independent mutable engines and independent securing calculation caches.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext
import cProfile
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import time
from typing import Any

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "experiments/mvp/maskspeed"


def load_source(path: Path, name: str, *, import_rewrites=None):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass resolves its defining module here
    if import_rewrites:
        source = path.read_text()
        for old, new in import_rewrites.items():
            if source.count(old) != 1:
                raise ValueError(f"expected one isolated import rewrite in {path}: {old}")
            source = source.replace(old, new)
        exec(compile(source, str(path), "exec"), module.__dict__)
    else:
        spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Runtime:
    mask: Any
    securing: Any
    label: str
    lookahead: Any


def runtimes(baseline: Path = HERE / "baseline"):
    from llm.stowlm import mask
    from llm.securing import mask_filter
    from llm.lookahead import mask_filter as lookahead_filter
    old = Runtime(load_source(baseline / "mask.py", "llm.maskspeed._baseline_mask"),
                  load_source(baseline / "securing_mask_filter.py",
                              "llm.maskspeed._baseline_securing"), "before",
                  load_source(baseline / "lookahead_mask_filter.py",
                              "llm.maskspeed._baseline_lookahead"))
    return old, Runtime(mask, mask_filter, "after", lookahead_filter)


def make_decoder(instance, runtime, codec, *, stack=True, lookahead=0):
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    engine = make_feasibility_engine(instance, get_action_space(),
        config=FeasibilityConfig(extra={"global_screen": 0}))
    if stack:
        engine = runtime.securing.SecuringEngine(runtime.lookahead.LookaheadEngine(engine))
    return runtime.mask.MaskedDecoder(engine, codec, runtime.mask.MaskConfig(lookahead=lookahead))


def codec_for(instance):
    from llm.stowlm.format import ActionCodec, load_tokenizer
    return ActionCodec(load_tokenizer(), [box.id for box in instance.containers])


def saved_traces(root: Path = ROOT):
    """Deduplicate real model trajectories, preserving terminal dead ends."""
    seen = set()
    for path in sorted((root / "experiments/mvp/speed/out").glob("decode_sweep*.jsonl")):
        for line, raw in enumerate(path.read_text().splitlines(), 1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            map_path = (root / row["map"]).resolve()
            train_root = (root / "experiments/mvp/corpus/train/small").resolve()
            if not map_path.is_relative_to(train_root):
                raise ValueError(f"saved guard input is outside small TRAIN: {row['map']}")
            for sample, trace in enumerate(row["samples"]):
                key = (row["map"], json.dumps(trace["actions"], sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                yield dict(id=f"{path.name}:{line}:{sample}", map=row["map"],
                           actions=trace["actions"], expected_status=trace["status"],
                           source=str(path.relative_to(root)), kind="small")


def small_maps(n=6):
    folder = ROOT / "experiments/mvp/corpus/train/small"
    maps = {ship: sorted(p for p in (folder / ship / "simulated_yard").glob("seed-*.json")
                         if not p.name.endswith(".audit.json"))
            for ship in ("feeder_500", "feeder_800", "feeder_1700")}
    result = []
    for i in range(max(map(len, maps.values()))):
        for paths in maps.values():
            if i < len(paths):
                result.append(paths[i])
                if len(result) == n:
                    return result
    raise ValueError(f"only {len(result)} small training maps")


def instance_for(trace):
    if trace["kind"] == "tiny":
        from stowage.actions.samples import tiny_instance
        return tiny_instance(trace["seed"], n_load=8, layover=bool(trace["seed"] % 2))
    from stowage.io import load_instance
    return load_instance(ROOT / trace["map"])


def snapshot(decoder):
    """Plan serialization includes expansion/move results, not just actions."""
    plan = decoder.engine.to_plan().model_dump(mode="json") if decoder.status == "complete" else None
    return dict(status=decoder.status, actions=decoder.actions, tokens=list(decoder.tokens), plan=plan)


def assert_equal(before, after, *, trace_id, step):
    a, b = before.allowed_token_ids(), after.allowed_token_ids()
    if a != b:
        raise AssertionError(f"{trace_id} step {step}: legal IDs differ: {a!r} != {b!r}")
    if before.status != after.status or before.actions != after.actions:
        raise AssertionError(f"{trace_id} step {step}: state/action mismatch")
    return a


class BaselineTraceUnreplayable(ValueError):
    """A historical action stream is rejected equally by both current arms."""


def replay_trace(trace, old, new, *, stack=True):
    from stowage.actions import get_action_space
    instance = instance_for(trace)
    codec = codec_for(instance)
    before, after = (make_decoder(instance, runtime, codec, stack=stack,
                                  lookahead=trace.get("lookahead", 0)) for runtime in (old, new))
    steps = backtracks = prefetched = 0
    digest = hashlib.sha256()

    def check():
        nonlocal steps
        legal = assert_equal(before, after, trace_id=trace["id"], step=steps)
        digest.update(json.dumps(legal).encode())
        steps += 1
        return legal

    def advance(token):
        legal = check()
        if token not in legal:
            raise BaselineTraceUnreplayable(
                f"{trace['id']} step {steps}: historical token {token} rejected by both arms; "
                f"baseline legal IDs={legal!r}")
        a, b = before.advance(token), after.advance(token)
        if a != b:
            raise AssertionError(f"{trace['id']}: different committed actions")

    if trace["kind"] == "small":
        for action in trace["actions"]:
            for token in codec.encode_action(get_action_space().decode(action)):
                advance(token)
        if trace["expected_status"] == "complete":
            for token in codec.end_plan_tokens:
                advance(token)
        check()
        if before.status != trace["expected_status"]:
            raise BaselineTraceUnreplayable(
                f"{trace['id']}: historical status {trace['expected_status']} != both-arm replay {before.status}")
    else:
        rng = random.Random(trace["seed"])
        popped = False
        while True:
            legal = check()
            if not legal:
                break
            # Probe successor prefetch and a partially written action followed
            # by pop/rebranch, exercising caches restored from older prefixes.
            if steps % 11 == 0:
                before.prefetch()
                after.prefetch()
                prefetched += 1
                check()
            if not popped and before.engine.depth == 3:
                token = legal[rng.randrange(len(legal))]
                before.advance(token)
                after.advance(token)
                if before.pop_action() != after.pop_action():
                    raise AssertionError("pop action mismatch")
                backtracks += 1
                popped = True
                continue
            token = legal[rng.randrange(len(legal))]
            if before.advance(token) != after.advance(token):
                raise AssertionError("generated action mismatch")
            if steps > 2000:
                raise AssertionError("tiny trace did not terminate")
    if snapshot(before) != snapshot(after):
        raise AssertionError(f"{trace['id']}: final plans/tokens/actions/status differ")
    return dict(id=trace["id"], kind=trace["kind"], steps=steps, actions=len(before.actions),
                status=before.status, backtracks=backtracks, prefetch_probes=prefetched,
                legal_sets_sha256=digest.hexdigest(), exact=True,
                source=trace.get("source"), map=trace.get("map"), seed=trace.get("seed"),
                replacement_for=trace.get("replacement_for"))


class LayerTimer:
    """Nested CPU/wall timers; exclusive times do not double-count inner layers.

    Methods are patched only on their owning class inside this context, then
    restored. Class identity is retained for lookahead native-contract checks.
    """
    def __init__(self, clock="cpu"):
        if clock not in ("cpu", "wall"):
            raise ValueError("layer clock must be cpu or wall")
        self.clock_name = "process_time" if clock == "cpu" else "perf_counter"
        self.clock = time.process_time if clock == "cpu" else time.perf_counter
        self.stack = []
        self.exclusive = defaultdict(float)
        self.inclusive = defaultdict(float)
        self.calls = Counter()
        self.originals = []

    def patch(self, cls, names, layer):
        for name in names:
            if name not in cls.__dict__ or not callable(cls.__dict__[name]):
                continue
            original = cls.__dict__[name]
            def measured(*args, _f=original, _layer=layer, **kwargs):
                start = self.clock()
                frame = [0.0, _layer]
                self.stack.append(frame)
                try:
                    return _f(*args, **kwargs)
                finally:
                    elapsed = self.clock() - start
                    self.stack.pop()
                    self.exclusive[_layer] += elapsed - frame[0]
                    if not any(parent[1] == _layer for parent in self.stack):
                        self.inclusive[_layer] += elapsed
                    self.calls[_layer] += 1
                    if self.stack:
                        self.stack[-1][0] += elapsed
            self.originals.append((cls, name, original))
            setattr(cls, name, measured)

    @contextmanager
    def active(self, runtime):
        from stowage.feasibility._engine import IncrementalEngine
        names = ("legal_index", "legal_actions", "legal_discharges", "is_legal", "is_complete",
                 "dead_end", "copy", "push", "pop", "legal_box_ids", "legal_slots_for_box")
        self.patch(IncrementalEngine, names, "production_engine")
        self.patch(runtime.lookahead.LookaheadEngine, names, "lookahead")
        self.patch(runtime.securing.SecuringEngine, names, "securing")
        self.patch(runtime.mask.MaskedDecoder, ("allowed_token_ids", "advance", "prefetch"), "token_mask")
        try:
            yield self
        finally:
            for cls, name, original in reversed(self.originals):
                setattr(cls, name, original)


def benchmark(runtime, *, batches=(1, 8), actions=4, maps=6, profile=False,
              layer_clock="cpu", cold_codec=False):
    """Sequential CPU mirror of mask_mlx's row loop; never loads MLX/GPU.

    Report allowed-set query, advance, full per decode step, and nested layer
    attribution. Rows share the same immutable instance and screen cache as the
    production factory does; each owns mutable engine/expander state.
    """
    from stowage.io import load_instance
    records = []
    for path in small_maps(maps):
        instance = load_instance(path)
        codec = None if cold_codec else codec_for(instance)
        for batch in batches:
            # Creating factory rows via .copy() intentionally reproduces its
            # shared immutable screen/capacity calculation caches.
            timer = LayerTimer(layer_clock)
            profiler = cProfile.Profile() if profile else None
            measurements = []
            for instrumented in (False, True):
                if cold_codec:
                    codec = codec_for(instance)
                first = make_decoder(instance, runtime, codec)
                decoders = [first] + [runtime.mask.MaskedDecoder(first.engine.copy(), codec,
                            runtime.mask.MaskConfig()) for _ in range(batch - 1)]
                query_s = advance_s = 0.
                query_cpu_s = advance_cpu_s = 0.
                row_steps = batch_steps = 0
                rngs = [random.Random(17 + i) for i in range(batch)]
                start = time.perf_counter()
                cpu_start = time.process_time()
                context = timer.active(runtime) if instrumented else nullcontext()
                with context:
                    if profiler and instrumented:
                        profiler.enable()
                    while True:
                        active = [i for i, dec in enumerate(decoders)
                                  if dec.status == "running" and dec.engine.depth < actions]
                        if not active:
                            break
                        phase = time.perf_counter()
                        cpu_phase = time.process_time()
                        sets = [decoders[i].allowed_token_ids() for i in active]
                        query_cpu_s += time.process_time() - cpu_phase
                        query_s += time.perf_counter() - phase
                        phase = time.perf_counter()
                        cpu_phase = time.process_time()
                        for i, legal in zip(active, sets):
                            if legal:
                                decoders[i].advance(legal[rngs[i].randrange(len(legal))])
                        advance_cpu_s += time.process_time() - cpu_phase
                        advance_s += time.perf_counter() - phase
                        batch_steps += 1
                        row_steps += len(active)
                    if profiler and instrumented:
                        profiler.disable()
                measurements.append(dict(query_s=query_s, advance_s=advance_s,
                    query_cpu_s=query_cpu_s, advance_cpu_s=advance_cpu_s,
                    row_steps=row_steps, batch_steps=batch_steps, wall_s=time.perf_counter()-start,
                    cpu_s=time.process_time()-cpu_start,
                    actions=[d.actions for d in decoders], statuses=[d.status for d in decoders],
                    tokens=[list(d.tokens) for d in decoders]))
            plain, timed = measurements
            for key in ("actions", "row_steps", "batch_steps", "statuses", "tokens"):
                if plain[key] != timed[key]:
                    raise AssertionError(f"instrumentation changed trajectory: {key}")
            query_s, advance_s = plain["query_s"], plain["advance_s"]
            query_cpu_s, advance_cpu_s = plain["query_cpu_s"], plain["advance_cpu_s"]
            row_steps, batch_steps = plain["row_steps"], plain["batch_steps"]
            record = dict(runtime=runtime.label, map=str(path.relative_to(ROOT)), B=batch,
                requested_actions_per_row=actions, completed_actions=sum(d.engine.depth for d in decoders),
                row_steps=row_steps, batch_steps=batch_steps, wall_s=plain["wall_s"],
                cpu_s=plain["cpu_s"],
                instrumented_wall_s=timed["wall_s"],
                instrumented_cpu_s=timed["cpu_s"],
                layer_clock=timer.clock_name,
                codec_policy="cold_per_pass" if cold_codec else "shared_across_passes_and_batches",
                query_ms_per_row_step=1000 * query_s / row_steps,
                advance_ms_per_row_step=1000 * advance_s / row_steps,
                full_ms_per_row_step=1000 * (query_s + advance_s) / row_steps,
                full_ms_per_batch_step=1000 * (query_s + advance_s) / batch_steps,
                query_cpu_ms_per_row_step=1000 * query_cpu_s / row_steps,
                query_cpu_ms_per_batch_step=1000 * query_cpu_s / batch_steps,
                advance_cpu_ms_per_row_step=1000 * advance_cpu_s / row_steps,
                advance_cpu_ms_per_batch_step=1000 * advance_cpu_s / batch_steps,
                full_cpu_ms_per_row_step=1000 * (query_cpu_s + advance_cpu_s) / row_steps,
                full_cpu_ms_per_batch_step=1000 * (query_cpu_s + advance_cpu_s) / batch_steps,
                final_statuses=plain["statuses"],
                final_tokens_sha256=[hashlib.sha256(json.dumps(tokens).encode()).hexdigest()
                                     for tokens in plain["tokens"]],
                layer_exclusive_ms_per_row_step={k:1000*v/row_steps for k,v in timer.exclusive.items()},
                layer_inclusive_ms_per_row_step={k:1000*v/row_steps for k,v in timer.inclusive.items()},
                timed_calls=dict(timer.calls))
            records.append(record)
            if profiler:
                folder = HERE / "out/profiles"
                folder.mkdir(parents=True, exist_ok=True)
                profiler.dump_stats(str(folder / f"{runtime.label}-{path.parent.parent.name}-{path.stem}-b{batch}.prof"))
            print(json.dumps(record), flush=True)
    return records


def source_hashes():
    paths = [ROOT / "src/llm/stowlm/mask.py", *sorted((ROOT / "src/llm/securing").glob("*.py")),
             *sorted((ROOT / "src/llm/lookahead").glob("*.py")),
             *sorted((HERE / "baseline").glob("*.py"))]
    paths += sorted((ROOT / "src/stowage/checker").glob("*.py"))
    paths += sorted((ROOT / "src/stowage/feasibility").glob("*.py"))
    paths += [ROOT / "src/stowage/solver/securing_screen.py"]
    paths += [ROOT / "src/stowage/vessel/fleet.py", ROOT / "src/stowage/vessel/fleet_cache.py"]
    paths += [Path(__file__), *sorted((ROOT / "tests/llm/maskspeed").rglob("*.py"))]
    paths = [p for p in paths if p.exists()]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("guard", "benchmark", "all"), default="all")
    parser.add_argument("--baseline", type=Path, default=HERE / "baseline")
    parser.add_argument("--output", type=Path, default=HERE / "out/replay.json")
    parser.add_argument("--saved", type=int, default=174)
    parser.add_argument("--tiny", type=int, default=32)
    parser.add_argument("--maps", type=int, default=6)
    parser.add_argument("--actions", type=int, default=4)
    parser.add_argument("--batches", default="1,8")
    parser.add_argument("--arm", choices=("before", "after", "both"), default="both")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--layer-clock", choices=("cpu", "wall"), default="cpu")
    parser.add_argument("--cold-codec", action="store_true",
                        help="new codec for each batch and timing pass (cold static token memo)")
    args = parser.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        parser.error("launch with PYTHONHASHSEED=0; a runtime assignment cannot pin this interpreter")
    old, new = runtimes(args.baseline)
    start = time.perf_counter()
    cpu_start = time.process_time()
    result = dict(schema_version=2, cpu_only=True, workers=1, native_threads=1,
                  pid=os.getpid(),
                  pythonhashseed=os.environ["PYTHONHASHSEED"],
                  native_thread_limits={name:os.environ[name] for name in
                    ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")},
                  layer_clock="process_time" if args.layer_clock == "cpu" else "perf_counter",
                  source_before=source_hashes(), traces=[], unreplayable=[], benchmarks=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    save()  # Record the owned PID and source epoch before expensive work starts.
    if args.mode in ("guard", "all"):
        traces = list(saved_traces())[:args.saved]
        traces += [dict(id=f"tiny-seed-{seed}", kind="tiny", seed=seed, lookahead=8 if seed % 4 == 0 else 0)
                   for seed in range(args.tiny)]
        if args.shards < 1 or not 0 <= args.shard_index < args.shards:
            raise ValueError("require shards >= 1 and 0 <= shard-index < shards")
        traces = traces[args.shard_index::args.shards]
        result["shard"] = dict(index=args.shard_index, count=args.shards)
        replacements = 0
        for trace in traces:
            try:
                row = replay_trace(trace, old, new)
            except BaselineTraceUnreplayable as error:
                if trace["kind"] != "small":
                    raise
                excluded = dict(id=trace["id"], kind=trace["kind"], map=trace["map"],
                                source=trace["source"], reason=str(error), counted_as_trace=False)
                result["unreplayable"].append(excluded)
                seed = args.tiny + args.shard_index + replacements * args.shards
                replacements += 1
                traces.append(dict(id=f"tiny-seed-{seed}", kind="tiny", seed=seed,
                    lookahead=8 if seed % 4 == 0 else 0, replacement_for=trace["id"]))
                save()
                print(json.dumps(dict(baseline_unreplayable=excluded)), flush=True)
                continue
            result["traces"].append(row)
            save()
            print(json.dumps(row), flush=True)
        result["guard"] = dict(traces=len(result["traces"]), exact=True,
            baseline_unreplayable=len(result["unreplayable"]),
            kinds=dict(Counter(r["kind"] for r in result["traces"])),
            statuses=dict(Counter(r["status"] for r in result["traces"])),
            steps=sum(r["steps"] for r in result["traces"]),
            backtracks=sum(r["backtracks"] for r in result["traces"]),
            prefetch_probes=sum(r["prefetch_probes"] for r in result["traces"]))
    if args.mode in ("benchmark", "all"):
        for runtime in (old, new):
            if args.arm in (runtime.label, "both"):
                result["benchmarks"] += benchmark(runtime, maps=args.maps, actions=args.actions,
                    batches=tuple(map(int,args.batches.split(","))), profile=args.profile,
                    layer_clock=args.layer_clock, cold_codec=args.cold_codec)
                save()
    result.update(wall_s=time.perf_counter()-start, cpu_s=time.process_time()-cpu_start,
                  source_after=source_hashes())
    result["source_stable"] = result["source_before"] == result["source_after"]
    save()
    if not result["source_stable"]:
        raise AssertionError("source changed during measurement; rerun against stable sources")


if __name__ == "__main__":
    main()
