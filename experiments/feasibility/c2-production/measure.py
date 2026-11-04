"""Current production C2 timings on the immutable earlier ladder fixtures.

One thread, cold per-state masks; LP construction and action materialization are
reported separately. No fixture generation or qualification bypass is permitted.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import pstats
import random
import os
import platform
import resource
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SAVED = HERE.parent / "fix-t4-measure"
spec = importlib.util.spec_from_file_location("_perf_measure_helpers", HERE.parent / "performance-followup/measure.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins():
    paths = {ROOT / p for p in helpers.pins()}
    paths.update((ROOT / "params").rglob("*.toml"))
    paths.update((ROOT / "params").rglob("*.yaml"))
    paths.update((ROOT / "params").rglob("*.yml"))
    paths.add(Path(__file__).resolve())
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def native_stats(eng):
    g = eng._global
    if g is None:
        return {}
    native = g._persistent
    result = dict(lp_calls=g.lp_calls, lp_time_s=g.lp_time_s)
    if native is not None:
        result.update(model_builds=native.model_builds, solve_count=native.solve_count,
                      basis_starts=native.basis_starts)
    return result


def extra_timers(eng):
    counters = helpers.component_timers(eng)
    def bind(obj, name, label):
        old = getattr(obj, name)
        def timed(*args, **kwargs):
            row = counters.setdefault(label, dict(calls=0, wall_s=0.0))
            row["calls"] += 1
            t = time.perf_counter()
            try:
                return old(*args, **kwargs)
            finally:
                row["wall_s"] += time.perf_counter() - t
        setattr(obj, name, timed)
    g = eng._global
    if g is not None:
        for name in ("_center", "basis_intervals", "shift", "basis_shift", "solution_shift"):
            if hasattr(g, name):
                bind(g, name, "C2." + name)
        if g._persistent is not None:
            for name in ("activate", "center", "interval", "_run"):
                bind(g._persistent, name, "native." + name)
    return counters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rungs", nargs="+", default=["tiny", "small", "medium", "full"])
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--c2", nargs="+", type=int, default=[1])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--profile", action="store_true")
    args = ap.parse_args()
    from stowage.io import load_instance
    t_all = time.perf_counter()
    dest = HERE / (args.tag + ".json")
    before = pins()
    out = dict(config=vars(args), started=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               sources_before=before, blocks={},
               environment=dict(python=platform.python_version(), numpy=np.__version__,
                    platform=platform.platform(), threads={key:os.environ.get(key) for key in
                    ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")}))
    for rung in args.rungs:
        path = SAVED / (rung + "-current.json")
        instance_sha = sha(path)
        instance = load_instance(path)
        for c2 in args.c2:
            key = f"{rung}/c2-{c2}"
            rec = dict(instance=str(path.relative_to(ROOT)), instance_sha256=instance_sha,
                       boxes=len(instance.load_list), cells=len(instance.vessel.cells), states=[])
            out["blocks"][key] = rec
            root_counters = {}
            saved_methods = []
            if args.profile and c2:
                from stowage.feasibility import _certificates
                from stowage.feasibility.global_bounds import GlobalScreen
                for obj, name, label in ((_certificates, "capture", "root.capture"),
                                          (GlobalScreen, "_center", "root.center")):
                    old_method = getattr(obj, name)
                    def measured(*args, _method=old_method, _label=label, **kwargs):
                        row = root_counters.setdefault(_label, dict(calls=0, wall_s=0.0))
                        row["calls"] += 1
                        t = time.perf_counter()
                        try:
                            return _method(*args, **kwargs)
                        finally:
                            row["wall_s"] += time.perf_counter() - t
                    saved_methods.append((obj, name, old_method))
                    setattr(obj, name, measured)
            t = time.perf_counter()
            construct_prof = cProfile.Profile()
            if args.profile:
                construct_prof.enable()
            eng = helpers.make_engine(instance, c2=bool(c2), phase="after")
            if args.profile:
                construct_prof.disable()
            for obj, name, old_method in saved_methods:
                setattr(obj, name, old_method)
            rec["construct_s"] = time.perf_counter() - t
            if root_counters:
                rec["components_construct"] = root_counters
            rec["root_diagnostics"] = dict(eng.diagnostics)
            rec["root_native"] = native_stats(eng)
            if c2 and eng.diagnostics["global_screen"] != "on":
                write(dest, out)
                raise RuntimeError(f"{rung}: C2 unavailable: {eng.diagnostics['global_screen']}")
            if eng._global is not None:
                rec.update(rows=len(eng._global.A), tanks=eng._global.At.shape[1], slots=eng.n_slots)
                g = eng._global
                arrays = {name: getattr(g, name) for name in ("A", "At", "const", "lo", "hi", "tank_lo", "tank_hi")}
                arrays["u0"] = g.u0
                if eng._cur.ref is not None:
                    arrays.update({"root_"+name: getattr(eng._cur.ref, name)
                                   for name in ("fixed", "free", "y", "t", "R", "v", "Y")})
                frozen_path = HERE / f"{args.tag}-{rung}-lp-inputs.npz"
                np.savez_compressed(frozen_path, **arrays)
                rec["lp_inputs"] = dict(path=frozen_path.name, sha256=sha(frozen_path), names=g.names)
            prof = cProfile.Profile()
            counters = extra_timers(eng) if args.profile else None
            rng = random.Random(1704)
            for _ in range(args.steps):
                old = native_stats(eng)
                if args.profile:
                    prof.enable()
                t = time.perf_counter()
                mask = eng.legal_index()
                index_ms = (time.perf_counter() - t) * 1000
                if args.profile:
                    prof.disable()
                row = dict(depth=eng.depth, index_ms=index_ms, places=len(mask),
                           mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest(),
                           native_after_index=native_stats(eng),
                           native_index_delta={k: v - old.get(k, 0) for k, v in native_stats(eng).items()})
                if counters is not None:
                    row["components_index"] = json.loads(json.dumps(counters))
                    counters.clear()
                t = time.perf_counter()
                actions = eng.legal_actions()
                row.update(actions=len(actions), action_materialize_ms=(time.perf_counter()-t)*1000)
                row["actions_ms"] = row["index_ms"] + row["action_materialize_ms"]
                rec["states"].append(row)
                write(dest, out)
                if not actions:
                    rec["status"] = "dead"
                    rec["completion"] = eng.completion_check()
                    rec["dead_end"] = str(eng.dead_end())
                    break
                action = rng.choice(actions)
                row["action"] = eng.space.encode(action)
                old = native_stats(eng)
                t = time.perf_counter()
                eng.push(action)
                row["push_ms"] = (time.perf_counter() - t) * 1000
                row["index_push_ms"] = row["index_ms"] + row["push_ms"]
                row["native_push_delta"] = {k: v - old.get(k, 0) for k, v in native_stats(eng).items()}
                if counters is not None:
                    row["components_push"] = json.loads(json.dumps(counters))
                    counters.clear()
                if eng.is_complete():
                    rec["status"] = "complete"
                    break
                write(dest, out)
            rec["diagnostics"] = dict(eng.diagnostics)
            rec["native_final"] = native_stats(eng)
            for field in ("index_ms", "action_materialize_ms", "actions_ms", "push_ms", "index_push_ms"):
                vals = [s[field] for s in rec["states"] if field in s]
                rec[field + "_p50_p95"] = np.percentile(vals, [50,95]).tolist() if vals else []
            rec["instance_sha256_after"] = sha(path)
            assert rec["instance_sha256_after"] == instance_sha
            if args.profile:
                stream = io.StringIO()
                pstats.Stats(prof, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(70)
                (HERE / f"{args.tag}-{rung}-c2-{c2}.profile.txt").write_text(stream.getvalue())
                stream = io.StringIO()
                pstats.Stats(construct_prof, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(70)
                (HERE / f"{args.tag}-{rung}-c2-{c2}.construct-profile.txt").write_text(stream.getvalue())
            write(dest, out)
            print(key, "construct_s", round(rec["construct_s"],3), "index_ms",rec["index_ms_p50_p95"],
                  "states",len(rec["states"]), "native",rec["native_final"],flush=True)
    out["wall_s"] = time.perf_counter() - t_all
    out["maxrss_bytes_macos"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out["sources_after"] = pins()
    out["sources_changed"] = {k:v for k,v in out["sources_after"].items() if before.get(k) != v}
    write(dest, out)


if __name__ == "__main__":
    main()
