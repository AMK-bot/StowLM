"""Single-threaded, cold-per-prefix mask benchmark for the 28 October wave.

Run restored production against the isolated, unshipped ``candidate.py`` loader. Timing
does not include engine construction, action materialization, or push; those are
recorded separately. LP counters wrap entry points, so replacing a failed native
workspace cannot lose its solve or warm-start count. No feasibility inputs change.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import resource
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INSTANCES = HERE.parent / "fix-t4-measure"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins():
    paths = []
    for area in ("feasibility", "linear", "checker", "vessel", "model", "generate"):
        paths += sorted((ROOT / "src/stowage" / area).rglob("*.py"))
    paths += [ROOT / "src/stowage/versions.py", ROOT / "src/stowage/linear/margin_policy_data.json"]
    paths += sorted((ROOT / "params").rglob("*.toml"))
    paths += sorted((ROOT / "params").rglob("*.yaml"))
    paths += sorted((ROOT / "params").rglob("*.yml"))
    paths += [HERE / "candidate.py", HERE / "candidate/manifest.json"]
    paths += sorted((HERE / "candidate").glob("*.py"))
    paths += sorted(HERE.glob("bench*.py"))
    return {str(p.relative_to(ROOT)): sha(p) for p in paths if p.exists()}


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def runtime():
    versions = {}
    for package in ("numpy", "scipy", "pydantic", "stowage"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return dict(python=platform.python_version(), executable=sys.executable,
                platform=platform.platform(), versions=versions,
                threads={k: os.environ.get(k) for k in
                         ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")})


def instrument():
    """Count native solves, independent cold LPs, and actual basis activations."""
    from candidate import get_classes
    variants = [get_classes(False), get_classes(True)]
    assert variants[0].Engine is not variants[1].Engine, "Reference and candidate engines must differ"
    assert variants[0].GlobalScreen is not variants[1].GlobalScreen, "Reference and candidate screens must differ"

    counts = Counter()
    def wrap(cls, name, label, count=True):
        original = getattr(cls, name)
        def measured(self, *args, **kwargs):
            if count:
                counts[label] += 1
            old_warm = getattr(self, "basis_starts", 0)
            started = time.perf_counter()
            try:
                return original(self, *args, **kwargs)
            finally:
                counts[label + "_s"] += time.perf_counter() - started
                if name == "activate":
                    counts["warm"] += self.basis_starts - old_warm
        setattr(cls, name, measured)
    # Both screens share the unchanged native helper. Wrap each class exactly
    # once to avoid double-counting calls through that shared implementation.
    for cls in {v.PersistentLP for v in variants}:
        wrap(cls, "_run", "native")
        wrap(cls, "activate", "activate", count=False)
    for cls in {v.GlobalScreen for v in variants}:
        wrap(cls, "_lp", "cold")
    return counts


def delta(counts, old):
    return {k: counts[k] - old.get(k, 0) for k in sorted(set(counts) | set(old))}


def arguments():
    ap = argparse.ArgumentParser()
    ap.add_argument("--optimized", choices=(0, 1), type=int, required=True)
    ap.add_argument("--rungs", nargs="+", default=["tiny", "small", "medium", "full"])
    ap.add_argument("--c2", nargs="+", type=int, choices=(0, 1), default=[0, 1])
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1704)
    ap.add_argument("--replay", type=Path)
    ap.add_argument("--pair", action="store_true", help="Run reference then optimized in this process.")
    ap.add_argument("--output", type=Path, required=True)
    return ap.parse_args()


def run(args, counts, previous=None):
    from candidate import get_classes, make_engine
    from stowage.io import load_instance

    if previous is None and args.replay:
        previous = json.loads(args.replay.read_text())
    out = dict(config={**vars(args), "output": str(args.output),
                       "replay": str(args.replay) if args.replay else None},
               environment=runtime(), candidate_status="quarantined, unshipped",
               engine_module=get_classes(bool(args.optimized)).Engine.__module__,
               sources=pins(), blocks={})
    started = time.perf_counter()
    for rung in args.rungs:
        path = INSTANCES / (rung + "-current.json")
        inst = load_instance(path)
        for c2 in args.c2:
            key = f"{rung}/c2-{c2}"
            rng = random.Random(args.seed)
            old = dict(counts)
            t0 = time.perf_counter()
            eng = make_engine(inst, {"global_screen": c2}, optimized=bool(args.optimized))
            rec = dict(construct_s=time.perf_counter() - t0, construct_lp=delta(counts, old),
                       instance_sha256=sha(path), boxes=len(inst.load_list), cells=len(inst.vessel.cells),
                       slots=eng.n_slots, states=[], c2_status=eng.diagnostics["global_screen"])
            out["blocks"][key] = rec
            if c2 and eng.diagnostics["global_screen"] != "on":
                write(args.output, out)
                raise RuntimeError(f"{key}: C2 unavailable: {eng.diagnostics['global_screen']}")
            for step in range(args.steps):
                if eng.is_complete():
                    rec["status"] = "complete"
                    break
                old = dict(counts)
                t0 = time.perf_counter()
                mask = eng.legal_index()
                index_ms = 1000 * (time.perf_counter() - t0)
                row = dict(depth=eng.depth, index_ms=index_ms, places=len(mask),
                           mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest(), lp=delta(counts, old))
                t0 = time.perf_counter()
                actions = eng.legal_actions()
                row.update(materialize_ms=1000 * (time.perf_counter() - t0), actions=len(actions))
                rec["states"].append(row)
                write(args.output, out)
                if previous is not None:
                    expected = previous["blocks"][key]["states"][step]
                    assert row["mask_sha256"] == expected["mask_sha256"], (key, step, "mask differs")
                    row["same_mask"] = True
                if not actions:
                    rec["status"] = "dead"
                    break
                if previous is not None:
                    action = eng.space.decode(expected["action"])
                    assert action in actions, (key, step, "replay action absent")
                else:
                    action = rng.choice(actions)
                row["action"] = eng.space.encode(action)
                old = dict(counts)
                t0 = time.perf_counter()
                eng.push(action)
                row.update(push_ms=1000 * (time.perf_counter() - t0), push_lp=delta(counts, old))
                write(args.output, out)
            rec.setdefault("status", "capped")
            vals = [s["index_ms"] for s in rec["states"]]
            rec["index_ms_p50_p95"] = np.percentile(vals, [50, 95]).tolist() if vals else []
            # Every initial box requires at least one PLACE. Restow reloads and
            # discharges can add steps; this is a labelled base-step projection.
            rec["projected_base_steps"] = len(inst.load_list)
            rec["projection_assumption"] = "One PLACE per initial box; excludes extra restow/discharge steps."
            rec["projected_base_plan_ms_p50_p95"] = [len(inst.load_list) * x for x in rec["index_ms_p50_p95"]]
            rec["mask_lp_total"] = {k: sum(s["lp"].get(k, 0) for s in rec["states"])
                                     for k in ("native", "cold", "warm", "native_s", "cold_s")}
            rec["diagnostics"] = dict(eng.diagnostics)
            write(args.output, out)
            print(key, "p50/p95", rec["index_ms_p50_p95"], "LP", rec["mask_lp_total"], flush=True)
    out.update(wall_s=time.perf_counter() - started, sources_after=pins(),
               maxrss_bytes_macos=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    out["changed_sources"] = {k: v for k, v in out["sources_after"].items() if out["sources"].get(k) != v}
    write(args.output, out)
    return out


def main():
    args = arguments()
    # Guard before any timing. A failed policy qualification cannot be reported
    # as a fast C2 mask, including when only a later rung would need C2.
    if 1 in args.c2:
        from candidate import make_engine
        from stowage.io import load_instance
        preflight_started = time.perf_counter()
        probe = make_engine(load_instance(INSTANCES / "tiny-current.json"),
                            {"global_screen": 1}, optimized=False)
        if probe.diagnostics["global_screen"] != "on":
            write(args.output, dict(status="preflight_failed", timing_samples=0,
                                   reason=probe.diagnostics["global_screen"], sources=pins(),
                                   environment=runtime(), candidate_status="quarantined, unshipped",
                                   preflight_s=time.perf_counter() - preflight_started))
            raise RuntimeError("C2 preflight: " + probe.diagnostics["global_screen"])
        del probe
    counts = instrument()
    if args.pair:
        dest = args.output
        args.optimized = 0
        args.output = dest.with_name(dest.stem + "-reference.json")
        previous = run(args, counts)
        args.optimized = 1
        args.output = dest.with_name(dest.stem + "-optimized.json")
        run(args, counts, previous)
    else:
        run(args, counts)


if __name__ == "__main__":
    main()
