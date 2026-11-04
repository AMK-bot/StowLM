"""Reproducible one-thread error sweep against numerical hull/tank geometry.

Use the shared fixture builder for the planning context, replacing its explicitly
placeholder ship with each complete synthetic fleet vessel. Random cargo masses
are physical-cell masses, not a generated container manifest. This isolates the
physics approximation from slot legality; the exact checker remains the judge.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import hashlib
import tempfile
from pathlib import Path
import time

import numpy as np

from stowage.linear.model import build_linear_model
from stowage.linear.physics import measure_errors
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import list_fleet, load_vessel
from stowage.model import Vessel
from stowage.params import params_dir


def fleet_instance(name, *, allow_artificial_stations=False):
    base = build_instance()
    # Always enter the production loader: its cache verifies raw offset pins.
    # A second JSON cache here could bypass that mandatory validation.
    vessel = load_vessel(name, with_hull=True)
    print(f"{name} production_fleet_loader=with_hull_true", flush=True)
    if not vessel.hull.stations:
        raise ValueError("production fleet strength stations required; artificial envelopes disabled")
    return base.model_copy(update={
        "vessel": vessel,
        "onboard": ShipState(vessel_id=vessel.id),
        "port": base.port.model_copy(update={"max_draught_m": vessel.hull.max_draught_m}),
    })


def serializable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    return value


def write_summary(results, path):
    rows = ["# Measured linearisation errors", "",
            "SF/BM rows show the largest station value; JSON retains every station.", "",
            "| Ship | Accepted / attempted | Metric | Maximum | 99th percentile |",
            "|---|---:|---|---:|---:|"]
    for name, result in results.items():
        diagnostic = result["diagnostics"]
        for key in ("gm_m", "trim_m", "list_rad", "draught_m", "sf_n", "bm_nm"):
            maximum = float(np.max(diagnostic["max"][key]))
            p99 = float(np.max(diagnostic["p99"][key]))
            count = f"{diagnostic['samples']} / {diagnostic['attempts']}"
            rows.append(f"| {name} | {count} | {key} | {maximum:.8g} | {p99:.8g} |")
    rows.extend(["", "| Ship | Displacement band (kg) | Draught band (m) | Trim / heel bounds (m / rad) |",
                 "|---|---|---|---|"])
    for name, result in results.items():
        b = result["band"]
        rows.append(f"| {name} | {b['displacement_min_kg']:.3f}–{b['displacement_max_kg']:.3f} | "
                    f"{b['draught_min_m']:.6g}–{b['draught_max_m']:.6g} | "
                    f"{b['max_abs_trim_m']:.6g} / {b['max_abs_heel_rad']:.6g} |")
    path.write_text("\n".join(rows)+"\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=256)
    parser.add_argument("--calibration-samples", type=int, help="Private timing pilot only; production defaults192")
    parser.add_argument("--seed", type=int, default=3702)
    parser.add_argument("--ship", action="append")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("results.json"))
    args = parser.parse_args()
    results = {}
    started = time.perf_counter()
    for name in args.ship or list_fleet():
        begin = time.perf_counter()
        instance = fleet_instance(name)
        loaded = time.perf_counter()
        print(f"{name} loaded_seconds={loaded-begin:.3f}", flush=True)
        if args.calibration_samples is None:
            model = build_linear_model(instance)
        else:
            from stowage.linear.physics import build_model
            model = build_model(instance, _samples=args.calibration_samples)
        built = time.perf_counter()
        print(f"{name} build_seconds={built-loaded:.3f}", flush=True)
        errors, diagnostics = measure_errors(model, instance, samples=args.samples, seed=args.seed)
        results[name] = {
            "vessel_id": model.vessel_id, "seed": args.seed, "samples_requested": args.samples,
            "calibration_samples": getattr(model, "measurement_diagnostics", {}).get("samples", args.calibration_samples or 192),
            "band": asdict(model.band), "model_errors": asdict(model.errors),
            "calibration_diagnostics": getattr(model, "measurement_diagnostics", {}),
            "sweep_errors": asdict(errors), "diagnostics": diagnostics,
            "station_x_m": model.station_x_m,
            "strength_limits": "production synthetic fleet IACS S11A envelopes", "load_seconds": loaded-begin,
            "build_seconds": built-loaded, "sweep_seconds": time.perf_counter()-built,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(serializable(results), indent=2) + "\n")
        write_summary(results, args.output.with_suffix(".md"))
        print(name, json.dumps(serializable({"build_seconds": built-loaded,
              "elapsed_seconds": time.perf_counter()-begin, "diagnostics": diagnostics})), flush=True)
    print(f"total_seconds={time.perf_counter()-started:.3f}", flush=True)


if __name__ == "__main__":
    main()
