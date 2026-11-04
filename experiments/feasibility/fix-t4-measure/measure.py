"""Scoped current-hull feasibility audit. One process, one BLAS/HiGHS thread, no GPU.

Baseline sources are saved before this fix wave; only their C2 margin direction is
migrated. Profiles include cold per-state masks, not repeated warm-cache calls.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.util
import io
import json
import pstats
import random
import sys
import time
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins():
    from stowage.frozen import current_provenance
    paths = sorted((ROOT / "src/stowage/feasibility").glob("*.py"))
    paths += sorted((ROOT / "src/stowage/linear").glob("*.py"))
    paths += [ROOT / "src/stowage/linear/margin_policy_data.json", ROOT / "src/stowage/versions.py"]
    paths += sorted((ROOT / "src/stowage/model").glob("*.py"))
    paths += sorted((ROOT / "src/stowage/vessel").glob("*.py"))
    paths += sorted((ROOT / "src/stowage/generate").glob("*.py"))
    paths += sorted((ROOT / "src/stowage/solver").glob("scalable*.py"))
    paths += sorted((ROOT / "src/stowage/checker").glob("*.py"))
    paths += sorted((ROOT / "params").rglob("*.yaml"))
    paths += sorted((ROOT / "params").rglob("*.yml"))
    paths += sorted((ROOT / "params").rglob("*.toml"))
    return {"provenance": current_provenance().model_dump(mode="json"),
            "sources": {str(p.relative_to(ROOT)): sha(p) for p in paths}}


def factory(phase):
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig
    if phase == "baseline":
        for name in ("capacity", "tables", "global_bounds", "engine"):
            qual = "stowage.feasibility._fix_t4_baseline_" + name
            source = (HERE / "baseline" / (name + ".py")).read_text()
            if name == "global_bounds":
                source = source.replace("linear_constraints(model, margin_scale=margin_scale)",
                    'linear_constraints(model, direction="necessary", margin_scale=margin_scale)')
            if name == "engine":
                for dep in ("capacity", "tables", "global_bounds"):
                    source = source.replace("from ." + dep + " import", "from ._fix_t4_baseline_" + dep + " import")
            module = types.ModuleType(qual)
            module.__package__ = "stowage.feasibility"
            sys.modules[qual] = module
            exec(compile(source, str(HERE / "baseline" / (name + ".py")), "exec"), module.__dict__)
        Engine = sys.modules["stowage.feasibility._fix_t4_baseline_engine"].IncrementalEngine
    else:
        from stowage.feasibility._engine import IncrementalEngine as Engine
    space = get_action_space()
    return lambda inst, c2: Engine(inst, space, FeasibilityConfig(extra={"global_screen": int(c2)}))


def write(path, data):
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["baseline", "after"], required=True)
    ap.add_argument("--rungs", nargs="+", default=["tiny", "small", "medium", "full"])
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--draws", type=int, default=0)
    ap.add_argument("--large-steps", type=int, default=None)
    ap.add_argument("--full-steps", type=int, default=None)
    ap.add_argument("--profile", action="store_true", help="profile run; excluded from latency comparisons")
    args = ap.parse_args()
    from stowage.generate.random_yard import generate_random_instance
    from stowage.io import load_instance, save_instance
    before = pins()
    out = {"phase": args.phase, "start": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "pins": before, "config": vars(args), "blocks": {}, "acceptance": []}
    if args.phase == "baseline":
        out["baseline_sources"] = {p.name: sha(p) for p in sorted((HERE / "baseline").glob("*.py"))}
        out["baseline_adapter"] = 'Only linear_constraints direction="necessary" added to saved global_bounds.py'
    make = factory(args.phase)
    dest = HERE / (args.phase + ("-profile" if args.profile else "") + ".json")
    started = time.perf_counter()
    for ri, rung in enumerate(args.rungs):
        path = HERE / (rung + "-current.json")
        t = time.perf_counter()
        if not path.exists():
            inst = generate_random_instance(rung, seed=70_500_000 + ri * 100_000,
                vessel_name="feeder_500" if rung in ("tiny", "small") else "feeder_800")
            save_instance(inst, path)
        else:
            inst = load_instance(path)
        generation = time.perf_counter() - t
        for c2 in (False, True):
            t = time.perf_counter()
            eng = make(inst, c2)
            construction = time.perf_counter() - t
            rec = dict(instance_sha256=sha(path), boxes=len(inst.load_list), cells=len(inst.vessel.cells),
                       generation_s=generation, construct_s=construction, c2=c2, states=[], status="capped")
            rec["pins"] = pins()
            out["blocks"][f"{rung}/c2-{int(c2)}"] = rec
            profile = cProfile.Profile()
            rng = random.Random(1704)
            steps = args.steps
            if c2 and rung == "medium" and args.large_steps is not None:
                steps = args.large_steps
            if c2 and rung == "full" and args.full_steps is not None:
                steps = args.full_steps
            for step in range(steps):
                if eng.is_complete():
                    rec["status"] = "complete"
                    break
                t = time.perf_counter()
                if args.profile:
                    profile.enable()
                actions = eng.legal_actions()
                if args.profile:
                    profile.disable()
                latency = time.perf_counter() - t
                state = {"depth": eng.depth, "legal_ms": latency * 1000, "actions": len(actions)}
                if not actions:
                    rec["status"] = "dead"
                    rec["dead_end"] = eng.dead_end()
                    rec["states"].append(state)
                    break
                action = rng.choice(actions)
                state["action"] = eng.space.encode(action)
                t = time.perf_counter()
                eng.push(action)
                state["push_ms"] = (time.perf_counter() - t) * 1000
                rec["states"].append(state)
                write(dest, out)
            if eng.is_complete():
                rec["status"] = "complete"
                from stowage.checker import check
                t = time.perf_counter()
                checked = check(inst, eng.to_plan())
                rec["checker"] = dict(passed=checked.passed,
                    hard_rules=sorted(set(v.rule_id for v in checked.violations if v.severity == "hard")),
                    wall_s=time.perf_counter() - t,
                    ballast="none: this comparison checks the raw mask completion, not a ballast-finished plan")
            rec["diagnostics"] = dict(eng.diagnostics)
            values = [s["legal_ms"] for s in rec["states"]]
            rec["legal_ms_p50_p95"] = np.percentile(values, [50, 95]).tolist() if values else []
            if args.profile:
                stream = io.StringIO()
                pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(30)
                (HERE / f"{args.phase}-{rung}-c2-{int(c2)}.profile.txt").write_text(stream.getvalue())
            out["blocks"][f"{rung}/c2-{int(c2)}"] = rec
            write(dest, out)
            print(rung, c2, "construct", round(construction, 3), "mask_ms", rec["legal_ms_p50_p95"],
                  rec["status"], flush=True)
    for rung in ("tiny", "small"):
        for draw in range(args.draws):
            seed = 70_900_000 + int(rung == "small") * 100_000 + draw
            row = dict(rung=rung, seed=seed, max_attempts=1, generated=False, c2_reached=False, accepted=False)
            row["pins_before"] = pins()
            t = time.perf_counter()
            try:
                inst = generate_random_instance(rung, seed=seed, max_attempts=1)
                row["generated"] = True
                eng = make(inst, True)
                row["c2_reached"] = eng.diagnostics.get("global_screen") == "on"
                row["checks"] = eng.completion_check()
                row["accepted"] = all(row["checks"].values())
                row["c2_accepted"] = bool(row["checks"].get("c2"))
                row["diagnostics"] = dict(eng.diagnostics)
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            row["wall_s"] = time.perf_counter() - t
            row["pins_after"] = pins()
            row["source_changed_during_draw"] = row["pins_before"] != row["pins_after"]
            out["acceptance"].append(row)
            write(dest, out)
            print("draw", rung, draw, row["accepted"], round(row["wall_s"], 3), flush=True)
    out["wall_s"] = time.perf_counter() - started
    out["pins_after"] = pins()
    out["changed_during_run"] = {k: v for k, v in out["pins_after"]["sources"].items()
                                 if before["sources"].get(k) != v}
    write(dest, out)


if __name__ == "__main__":
    main()
