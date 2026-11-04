"""Same-instance/same-prefix cold-mask timings and exhaustive pre-change oracle.

One process and one BLAS/HiGHS thread. Profiles are separate from latency samples.
The saved baseline is the corrected exhaustive necessary-direction engine.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
from pathlib import Path
import pstats
import random
import sys
import time
import types

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INSTANCES = HERE.parent / "fix-t4-measure"


def baseline_engine():
    """Load all private dependencies from the snapshot, never current helpers."""
    prefix = "stowage.feasibility._perf_before_"
    for name in ("_legacy", "capacity", "tables", "global_bounds", "_engine"):
        qual = prefix + name
        if qual in sys.modules:
            continue
        path = HERE / "baseline" / (name + ".py")
        source = path.read_text()
        for dep in ("capacity", "tables", "_legacy", "global_bounds"):
            source = source.replace("from ." + dep + " import", "from ._perf_before_" + dep + " import")
            source = source.replace("from stowage.feasibility." + dep + " import",
                                    "from stowage.feasibility._perf_before_" + dep + " import")
        module = types.ModuleType(qual)
        module.__package__ = "stowage.feasibility"
        sys.modules[qual] = module
        exec(compile(source, str(path), "exec"), module.__dict__)
    return sys.modules[prefix + "_engine"].IncrementalEngine


def make_engine(instance, c2=True, phase="before"):
    from stowage.actions import get_action_space
    from stowage.feasibility.engine import FeasibilityConfig
    if phase == "before":
        cls = baseline_engine()
    else:
        from stowage.feasibility._engine import IncrementalEngine as cls
    return cls(instance, get_action_space(), FeasibilityConfig(extra={"global_screen": int(c2)}))


def pins():
    paths = []
    for area in ("feasibility", "linear", "checker", "vessel", "model", "rules"):
        paths += sorted((ROOT / "src/stowage" / area).rglob("*.py"))
    paths += [ROOT / "src/stowage/linear/margin_policy_data.json", ROOT / "src/stowage/versions.py"]
    paths += sorted((ROOT / "params/fleet").glob("*.toml"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def component_timers(eng):
    """Wall-clock attribution alongside cProfile (HiGHS callback nesting distorts pstats)."""
    counters = {}
    def bind(obj, name, label):
        original = getattr(obj, name)
        def measured(*args, **kwargs):
            row = counters.setdefault(label, dict(calls=0, wall_s=0.0, candidates=0, slots=0))
            row["calls"] += 1
            if name in ("_c1", "_c2"):
                row["candidates"] += len(args[1])
                row["slots"] += len(np.unique(args[2]))
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                row["wall_s"] += time.perf_counter() - start
        setattr(obj, name, measured)
    for name in ("_c1", "_c2", "_c1g", "_heat_ok", "_restow_places", "_evaluate"):
        bind(eng, name, name)
    if eng._global is not None:
        for name in ("_lp", "intervals", "local_intervals", "slot_interval", "reference"):
            bind(eng._global, name, "C2." + name)
    return counters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("before", "after"), required=True)
    ap.add_argument("--rungs", nargs="+", default=["tiny", "small", "medium", "full"])
    ap.add_argument("--steps", type=int, default=3)
    ap.add_argument("--medium-steps", type=int, default=None)
    ap.add_argument("--full-steps", type=int, default=1)
    ap.add_argument("--c2", nargs="+", type=int, default=[0, 1])
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--components", action="store_true", help="instrumented attribution; not latency data")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    from stowage.io import load_instance
    start = time.perf_counter()
    out = dict(config=vars(args), sources=pins(), blocks={})
    dest = HERE / (args.phase + ("-profile" if args.profile else "") + args.tag + ".json")
    # After timing always replays the exact saved before prefixes.
    previous = json.loads((HERE / "before.json").read_text()) if args.phase == "after" else None
    for rung in args.rungs:
        path = INSTANCES / (rung + "-current.json")
        instance = load_instance(path)
        for c2 in args.c2:
            t0 = time.perf_counter()
            eng = make_engine(instance, bool(c2), args.phase)
            if c2 and eng.diagnostics["global_screen"] != "on":
                raise RuntimeError(f"{rung}: C2 unavailable: {eng.diagnostics['global_screen']}")
            rec = dict(construct_s=time.perf_counter() - t0, boxes=len(instance.load_list),
                       cells=len(instance.vessel.cells), slots=eng.n_slots, states=[],
                       instance_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            if eng._global is not None:
                rec.update(rows=eng._global.A.shape[0], tanks=eng._global.At.shape[1],
                           root_lp_calls=eng._global.lp_calls)
            key = f"{rung}/c2-{c2}"
            out["blocks"][key] = rec
            rng = random.Random(1704)
            prof = cProfile.Profile()
            components = component_timers(eng) if args.components else None
            steps = args.full_steps if rung == "full" else args.steps
            if rung == "medium" and args.medium_steps is not None:
                steps = args.medium_steps
            for step in range(steps):
                old_lp = eng._global.lp_calls if eng._global else 0
                old_slots = eng.diagnostics["c2_slot_lps"]
                if args.profile:
                    prof.enable()
                t0 = time.perf_counter()
                mask = eng.legal_index()
                latency = (time.perf_counter() - t0) * 1000
                if args.profile:
                    prof.disable()
                t0 = time.perf_counter()
                actions = eng.legal_actions()
                materialize = (time.perf_counter() - t0) * 1000
                row = dict(depth=eng.depth, index_ms=latency, action_materialize_ms=materialize,
                           actions_ms=latency + materialize, actions=len(actions),
                           places=len(mask), lp_calls=(eng._global.lp_calls if eng._global else 0) - old_lp,
                           slot_lps=eng.diagnostics["c2_slot_lps"] - old_slots,
                           mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest())
                rec["states"].append(row)
                if components is not None:
                    row["components"] = json.loads(json.dumps(components))
                    components.clear()
                write(dest, out)
                if not actions:
                    break
                if previous is not None:
                    expected = previous["blocks"][key]["states"][step]
                    row["same_mask"] = row["mask_sha256"] == expected["mask_sha256"]
                    assert row["same_mask"], (key, step)
                    encoded = expected["action"]
                    action = next(a for a in actions if eng.space.encode(a) == encoded)
                else:
                    action = rng.choice(actions)
                row["action"] = eng.space.encode(action)
                t0 = time.perf_counter()
                eng.push(action)
                row["push_ms"] = (time.perf_counter() - t0) * 1000
                write(dest, out)
            rec["diagnostics"] = dict(eng.diagnostics)
            for field in ("index_ms", "actions_ms", "push_ms"):
                vals = [s[field] for s in rec["states"] if field in s]
                rec[field + "_p50_p95"] = np.percentile(vals, [50, 95]).tolist() if vals else []
            if args.profile:
                stream = io.StringIO()
                pstats.Stats(prof, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(50)
                (HERE / f"{args.phase}-{rung}-c2-{c2}{args.tag}.profile.txt").write_text(stream.getvalue())
            write(dest, out)
            print(key, "construct", round(rec["construct_s"], 2), "index", rec["index_ms_p50_p95"],
                  "lp", [r["lp_calls"] for r in rec["states"]], flush=True)
    out["wall_s"] = time.perf_counter() - start
    out["sources_after"] = pins()
    write(dest, out)


if __name__ == "__main__":
    main()
