"""Resumable, one-worker D58 raw-draw experiment (no physics bypasses).

Example (BLAS must have one thread):
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/background-1028/measure.py

Every generator call has max_attempts=1. Thus rejection rate has a raw candidate
denominator, including cargo/C1 failures, rather than successful-instance seeds.
C1 runs normally; C2 is explicitly off (the independent linear epoch is stale).
The production finisher/full-checker background gate is always enabled. Its
individual calls are recorded even when another generator screen later rejects.
No test fixture, checker monkeypatch, reduced cargo profile, or shortened plan
stands in for either generator's actual pipeline.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import faulthandler
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
VESSELS = ("feeder_500", "feeder_800", "feeder_1700")
RUNGS = ("tiny", "small", "medium")
GENERATORS = ("random", "simulated")


def sources() -> tuple[str, dict[str, object]]:
    files = sorted({*(path for directory in (ROOT / "src/stowage", ROOT / "params")
                      for path in directory.rglob("*")
                      if path.is_file() and path.suffix in {".py", ".json", ".toml"}),
                    HERE / "measure.py", HERE / "design_lashing.py"})
    manifest = {"sources": {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
                            for path in files},
                "runtime": {"python": sys.version,
                            **{package: version(package) for package in ("numpy", "scipy", "pydantic", "ortools")}},
                "environment": {key: value for key, value in sorted(os.environ.items())
                                if key.startswith("STOWAGE_") or key in {
                                    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                    "VECLIB_MAXIMUM_THREADS", "PYTHONHASHSEED"}}}
    key = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    path = HERE / "sources" / f"{key}.json"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return key, manifest


def utilisation(instance) -> dict:
    from stowage.vessel.fleet_conditions import slice_condition
    from stowage.generate.random_yard import _full_vessel
    from stowage.conventions import parse_slot

    boxes = [box for load in instance.vessel.fixed_loads for box in (load.cargo_boxes or [])]
    target_ids = set(instance.load_list)
    planned = [box for box in instance.containers if box.id in target_ids]
    parent_capacity = len(_full_vessel(instance.vessel.slice_of or instance.vessel.id).cells)
    background_teu = sum(box.length_ft / 20 for box in boxes)
    planned_teu = sum(box.length_ft / 20 for box in planned)
    return {
        "background_boxes": len(boxes), "background_teu": background_teu,
        "background_cargo_t": sum(box.gross_mass_kg for box in boxes) / 1000,
        "background_mean_box_t": statistics.mean(box.gross_mass_kg for box in boxes) / 1000 if boxes else 0.,
        "deck_mean_box_t": statistics.mean(box.gross_mass_kg for box in boxes if parse_slot(box.cell_ids[0]).on_deck) / 1000
            if any(parse_slot(box.cell_ids[0]).on_deck for box in boxes) else 0.,
        "planned_teu": planned_teu, "planned_cargo_t": sum(box.gross_mass_kg for box in planned) / 1000,
        "parent_capacity_teu": parent_capacity,
        "background_teu_fraction": background_teu / parent_capacity,
        "whole_ship_teu_fraction": (background_teu + planned_teu) / parent_capacity,
        "decision_teu_fraction": planned_teu / len(instance.vessel.cells),
        "condition": slice_condition(instance.vessel),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "draws.jsonl")
    parser.add_argument("--vessels", nargs="+", choices=VESSELS, default=list(VESSELS))
    parser.add_argument("--rungs", nargs="+", choices=RUNGS, default=list(RUNGS))
    parser.add_argument("--generators", nargs="+", choices=GENERATORS, default=list(GENERATORS))
    parser.add_argument("--tiny-small-draws", type=int, default=10)
    parser.add_argument("--medium-draws", type=int, default=3)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--traceback-after-s", type=float, default=120.)
    args = parser.parse_args()
    print(f"measurement worker pid={os.getpid()}", flush=True)
    from stowage.generate import background_acceptance as gate
    from stowage.generate import instances as simulated
    from stowage.generate import random_yard as random

    # Observe production gate outcomes without altering inputs, results or cache.
    real_evaluate = gate.evaluate_background
    gate_calls = []
    gate_inputs = []
    def observed(instance, **kwargs):
        result = real_evaluate(instance, **kwargs)
        gate_inputs.append((instance, result))
        adjustable = {tank.id for tank in instance.vessel.tanks if tank.adjustable}
        gate_calls.append({**asdict(result), "utilisation": utilisation(instance),
                           "ballast_t": sum(mass for key, mass in result.tank_masses_kg.items()
                                            if key in adjustable) / 1000})
        return result
    gate.evaluate_background = observed
    for module in (random, simulated):
        if getattr(module, "evaluate_background", None) is real_evaluate:
            module.evaluate_background = observed

    args.out.parent.mkdir(parents=True, exist_ok=True)
    previous = [json.loads(line) for line in args.out.read_text().splitlines()] if args.out.exists() else []
    done = {(row["generator"], row["vessel"], row["rung"], row["seed"]) for row in previous}
    jobs = [(generator, vessel, rung, seed)
            for vessel in args.vessels for rung in args.rungs for generator in args.generators
            for seed in range(args.seed_start, args.seed_start + (args.medium_draws if rung == "medium" else args.tiny_small_draws))]
    remaining = [job for job in jobs if job not in done]
    if args.limit is not None:
        remaining = remaining[:args.limit]
    for generator, vessel, rung, seed in remaining:
        gate_calls.clear()
        gate_inputs.clear()
        source_key, _ = sources()
        report = random.RandomGenerationReport() if generator == "random" else simulated.GenerationReport()
        row = dict(generator=generator, vessel=vessel, rung=rung, seed=seed,
                   source_start=source_key, screens={"C1": True, "C2": False,
                   "background_finisher_full_checker": True}, raw_draws=1)
        started = time.perf_counter()
        if args.traceback_after_s > 0:
            faulthandler.dump_traceback_later(args.traceback_after_s, repeat=True)
        try:
            function = random.generate_random_instance if generator == "random" else simulated.generate_simulated_instance
            instance = function(rung, seed=seed, vessel_name=vessel, max_attempts=1, report=report)
            row.update(accepted=True, instance_id=instance.id, utilisation=utilisation(instance))
            if not gate_calls:
                raise AssertionError("generator emitted a slice without observed background gate")
            row["accepted_background"] = {key: value for key, value in gate_calls[-1].items()
                                          if key not in {"utilisation", "ballast_t"}}
            if not gate_calls[-1]["accepted"]:
                raise AssertionError(f"emitted background rejected: {gate_calls[-1]['reasons']}")
        except Exception as exc:
            row.update(accepted=False, error=f"{type(exc).__name__}: {exc}")
        generation_s = time.perf_counter() - started
        from design_lashing import design_lashing
        try:
            row["design_lashing"] = design_lashing(*gate_inputs[0]) if gate_inputs else {}
        except Exception as exc:
            row["design_lashing_error"] = f"{type(exc).__name__}: {exc}"
        row.update(wall_s=generation_s, diagnostics_s=time.perf_counter()-started-generation_s, report=asdict(report),
                   gate_calls=list(gate_calls), source_end=sources()[0])
        usage = resource.getrusage(resource.RUSAGE_SELF)
        row["runtime"] = {"peak_self_rss_mib": usage.ru_maxrss / (1024**2 if sys.platform == "darwin" else 1024),
                          "cpu_self_s": usage.ru_utime + usage.ru_stime,
                          "note": "Self process only; reusable finisher child memory/CPU is not included."}
        faulthandler.cancel_dump_traceback_later()
        with args.out.open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            stream.flush()
        print(generator, vessel, rung, seed, "accept" if row["accepted"] else "reject",
              f"{row['wall_s']:.2f}s", row.get("error", ""), flush=True)


if __name__ == "__main__":
    main()
