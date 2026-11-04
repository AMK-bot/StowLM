"""Read-only pinned-source pre-stow profiler; writes only beside this script.

Timers and cProfile perturb execution speed. Counts describe this diagnostic
search run, not an uninstrumented throughput or optimality certificate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import cProfile
from dataclasses import asdict
import functools
import hashlib
import json
import os
from pathlib import Path
import pstats
import resource
import signal
import sys
import time

HERE = Path(__file__).resolve().parent
PILOT = HERE.parents[1]
ROOT = HERE.parents[4]


class ProfileDeadline(BaseException):
    pass


def save(path, value):
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, default=str, sort_keys=True, indent=2) + "\n")
    temp.replace(path)


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for base in ("src/stowage", "params") for path in sorted((root / base).rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=HERE / "measurement")
    parser.add_argument("--budget", type=float, default=140)
    parser.add_argument("--cap", type=float, default=150)
    args = parser.parse_args(argv)
    args.snapshot = args.snapshot.resolve()
    args.input = args.input.resolve()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(HERE):
        parser.error("all output must stay inside diag2/profile")
    if not (args.snapshot / "src/stowage/solver/twostep.py").is_file():
        parser.error("snapshot must contain src/stowage/solver/twostep.py")
    args.output.mkdir(parents=True, exist_ok=True)
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.snapshot / "src"))
    sys.path.insert(0, str(PILOT.parent))
    sys.path.insert(0, str(HERE.parent))
    started = time.monotonic()
    pinned = hashes(args.snapshot)
    live = hashes(ROOT)
    save(args.output / "source-start.json", pinned)
    save(args.output / "live-source-start.json", live)
    counters = Counter()
    by_depth = Counter()
    legal_sizes = defaultdict(lambda: {"calls": 0, "sum": 0, "max": 0})
    times = defaultdict(lambda: {"calls": 0, "inclusive_s": 0., "exclusive_wrapped_s": 0.})
    timer_stack = []
    dimensions = {}
    root_engines = []
    row = dict(status="setup", budget_s=args.budget, cap_s=args.cap,
               input=str(args.input), input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
               snapshot=str(args.snapshot), diagnostic=True,
               search="cargo-only explicit depth-first search; no terminal CP-SAT invocation",
               instrumentation="cProfile + nested method timers; execution perturbed",
               timing_exclusivity="exclusive_wrapped_s subtracts nested instrumented calls only; cProfile tt is full Python-call exclusive")
    last_checkpoint = started

    def checkpoint(**values):
        nonlocal last_checkpoint
        row.update(values)
        row.update(wall_s=time.monotonic() - started, counters=dict(counters),
                   child_pushes_by_depth=dict(by_depth), legal_sizes=dict(legal_sizes),
                   method_timing=dict(times), dimensions=dimensions)
        if root_engines:
            screen = root_engines[0]._global
            row["global_lp_diagnostics"] = (None if screen is None else {
                name: getattr(screen, name, None)
                for name in ("lp_calls", "lp_time_s", "lp_indeterminate")})
        save(args.output / "result.json", row)
        last_checkpoint = time.monotonic()

    def wrap(owner, name, tag=None, after=None):
        original = getattr(owner, name)
        label = tag or f"{owner.__module__}.{owner.__name__}.{name}"

        @functools.wraps(original)
        def timed(*positional, **keywords):
            begin = time.monotonic()
            entry = [begin, 0.]
            timer_stack.append(entry)
            outcome = None
            try:
                outcome = original(*positional, **keywords)
                if after is not None:
                    after(positional, outcome)
                return outcome
            finally:
                elapsed = time.monotonic() - begin
                timer_stack.pop()
                if timer_stack:
                    timer_stack[-1][1] += elapsed
                item = times[label]
                item["calls"] += 1
                item["inclusive_s"] += elapsed
                item["exclusive_wrapped_s"] += max(0., elapsed - entry[1])
                if time.monotonic() - last_checkpoint > 5:
                    checkpoint()

        setattr(owner, name, timed)

    def engine_ready(positional, outcome):
        engine = positional[0]
        root_engines.append(engine)
        table = engine.T
        dimensions.update(boxes=len(table.box_ids), slots=len(table.slot_ids), cells=len(table.cell_ids),
                          stacks=len(table.stack_ids), box_slot_pairs=len(table.box_ids) * len(table.slot_ids),
                          static_mask_shape=list(table.static.shape),
                          global_screen=engine.diagnostics.get("global_screen"),
                          canonical_yard_boxes=len(engine.instance.load_list))
        global_screen = engine._global
        if global_screen is not None:
            dimensions.update(c2_A_shape=list(global_screen.A.shape), c2_At_shape=list(global_screen.At.shape),
                              c2_row_count=len(global_screen.names),
                              c2_constraint_names=list(global_screen.names))

    def pushed(positional, outcome):
        engine = positional[0]
        counters["child_pushes"] += 1
        depth = engine.depth
        by_depth[depth] += 1
        counters["maximum_engine_depth"] = max(counters["maximum_engine_depth"], depth)

    def indexed(positional, outcome):
        depth = positional[0].depth
        item = legal_sizes[depth]
        item["calls"] += 1
        item["sum"] += len(outcome)
        item["max"] = max(item["max"], len(outcome))

    profiler = cProfile.Profile()

    def deadline(signum, frame):
        raise ProfileDeadline("whole-profile wall cap")

    signal.signal(signal.SIGALRM, deadline)
    signal.signal(signal.SIGTERM, deadline)
    signal.setitimer(signal.ITIMER_REAL, args.cap)
    checkpoint()
    try:
        from frozen_paths import install
        install(args.output)
        from cache_runtime import redirect_cache, enforce_scope
        redirect_cache(HERE.parent / "runtime")
        enforce_scope()
        from stowage.io import load_instance
        from stowage.solver import twostep
        from stowage.feasibility._engine import IncrementalEngine
        from stowage.feasibility.global_bounds import GlobalScreen
        instance = load_instance(args.input)
        if not Path(twostep.__file__).resolve().is_relative_to(args.snapshot):
            raise RuntimeError("twostep did not import from pinned source")
        dimensions.update(manifest_boxes=len(instance.containers), load_list_boxes=len(instance.load_list),
                          vessel_cells=len(instance.vessel.cells), vessel_stacks=len(instance.vessel.stacks))
        checkpoint(instance_id=instance.id, vessel=instance.vessel.id,
                   loaded_solver_source=twostep.__file__, status="profiling")
        for method in ("__init__", "legal_index", "legal_actions", "legal_discharges", "is_legal",
                       "push", "copy", "is_complete", "dead_end", "decode_index", "forces_restow"):
            if hasattr(IncrementalEngine, method):
                callback = engine_ready if method == "__init__" else pushed if method == "push" else indexed if method == "legal_index" else None
                wrap(IncrementalEngine, method, after=callback)
        for method in ("reference", "intervals", "local_intervals", "slot_interval", "_lp", "_center",
                       "_center_cold", "_slot_interval", "_slot_interval_cold", "_timed_native"):
            if hasattr(GlobalScreen, method):
                wrap(GlobalScreen, method)
        # These checks are executed immediately before a candidate child is
        # copied/pushed, so their count measures candidate branches tested.
        original_geometry = twostep.securing_geometry_violation

        def geometry(*positional, **keywords):
            counters["candidate_branches_tested"] += 1
            outcome = original_geometry(*positional, **keywords)
            counters["securing_geometry_rejected"] += bool(outcome)
            return outcome

        twostep.securing_geometry_violation = geometry
        profiler.enable()
        phase = time.monotonic()
        result = twostep.preplan(instance, time_limit_s=args.budget)
        profiler.disable()
        checkpoint(status=result.status, message=result.message, prestow_wall_s=time.monotonic() - phase,
                   complete_actions=len(result.actions), returned_slots=len(result.slots),
                   infeasibility_proved=False,
                   interpretation="timeout is not infeasibility" if result.status == "timeout" else
                   "pre-stow stage result only; ballast/full checker remain required")
    except ProfileDeadline as exc:
        profiler.disable()
        checkpoint(status="profile_cap_timeout", error=str(exc), infeasibility_proved=False)
    except Exception as exc:
        profiler.disable()
        checkpoint(status="error", error=f"{type(exc).__name__}: {exc}", infeasibility_proved=False)
    finally:
        profiler.disable()
        signal.setitimer(signal.ITIMER_REAL, 0)
        profiler.dump_stats(str(args.output / "cprofile.pstats"))
        stats = pstats.Stats(profiler)
        functions = []
        all_functions = []
        for (filename, line, function), (primitive, calls, self_time, cumulative, callers) in stats.stats.items():
            all_functions.append(dict(file=filename, line=line, function=function, primitive_calls=primitive,
                                      calls=calls, exclusive_s=self_time, inclusive_s=cumulative))
            if "/stowage/" in filename:
                functions.append(dict(file=filename, line=line, function=function, primitive_calls=primitive,
                                      calls=calls, exclusive_s=self_time, inclusive_s=cumulative))
        save(args.output / "hotfunctions-inclusive.json", sorted(functions, key=lambda v: v["inclusive_s"], reverse=True)[:100])
        save(args.output / "hotfunctions-exclusive.json", sorted(functions, key=lambda v: v["exclusive_s"], reverse=True)[:100])
        save(args.output / "all-hotfunctions-inclusive.json", sorted(all_functions, key=lambda v: v["inclusive_s"], reverse=True)[:100])
        save(args.output / "all-hotfunctions-exclusive.json", sorted(all_functions, key=lambda v: v["exclusive_s"], reverse=True)[:100])
        inconsistent = [item for item in all_functions
                        if item["exclusive_s"] > item["inclusive_s"] + 1e-5]
        sanity = dict(timing_consistent=not inconsistent,
                      inconsistent_rows=inconsistent,
                      interpretation="Use independent monotonic method timers when raw cProfile invariants fail")
        save(args.output / "cprofile-sanity.json", sanity)
        ending = hashes(args.snapshot)
        live_end = hashes(ROOT)
        save(args.output / "source-end.json", ending)
        save(args.output / "live-source-end.json", live_end)
        scale = 1024 ** 3 if sys.platform == "darwin" else 1024 ** 2
        checkpoint(pinned_source_drift=sorted(k for k in set(pinned) | set(ending) if pinned.get(k) != ending.get(k)),
                   live_source_drift=sorted(k for k in set(live) | set(live_end) if live.get(k) != live_end.get(k)),
                   peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale,
                   cprofile_total_calls=stats.total_calls, cprofile_total_tt_s=stats.total_tt,
                   cprofile_timing_consistent=not inconsistent)
    return 0 if row["status"] not in ("error",) else 2


if __name__ == "__main__":
    raise SystemExit(main())
