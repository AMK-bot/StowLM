"""Public cargo-provider before/after D60 mass audit, no placement claims.

Each equipment stratum preserves the source empty/POD/event probabilities.
Route counts follow configured call shares exactly, then box statistics pool
those strata. Dense/tiny dry20 is the dry_20 row; empty_dry20 is audited too.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import sys
import time

from stowage.params import load_param_file

HERE = Path(__file__).resolve().parent
CAP_KG = 30_500.0  # Audit threshold from D60; production has its own named constant.

def percentile(values, p):
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    left = int(position)
    return ordered[left] + (ordered[min(left + 1, len(ordered) - 1)] - ordered[left]) * (position - left)

def statistics(boxes):
    if not boxes:
        return None
    masses = [b.gross_mass_kg for b in boxes]
    return {"n": len(masses), "mean_kg": sum(masses) / len(masses),
            "p95_kg": percentile(masses, .95), "min_kg": min(masses),
            "max_kg": max(masses), "above_30500_count": sum(m > CAP_KG for m in masses),
            "above_30500_share": sum(m > CAP_KG for m in masses) / len(masses),
            "at_30500_count": sum(m == CAP_KG for m in masses),
            "empty_share": sum(b.empty for b in boxes) / len(boxes),
            "weight_classes": dict(sorted(Counter(b.weight_class for b in boxes).items())),
            "pods": dict(sorted(Counter(b.pod for b in boxes).items()))}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["before", "after"])
    parser.add_argument("--n-per-profile", type=int, default=5000)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    start = time.perf_counter()
    began = datetime.now(timezone.utc).isoformat()
    if args.phase == "before":
        source = HERE / "weights_before_cargo.py"
        spec = importlib.util.spec_from_file_location("d60_weights_before_cargo", source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    else:
        import stowage.generate.cargo as module
        source = Path(module.__file__)
    params = load_param_file("cargo", "feeder_export")
    samples, summaries = [], []
    profiles = [(row["id"], row, False) for row in params["equipment"]]
    profiles += [("empty_dry20", next(row for row in params["equipment"] if row["id"] == "dry_20"), True)]
    for ei, (name, equipment, force_empty) in enumerate(profiles):
        conditioned = deepcopy(params)
        row = deepcopy(equipment)
        row["share"]["value"] = 1.0
        if force_empty:
            row["empty_share"]["value"] = 1.0
        conditioned["equipment"] = [row]
        pooled = []
        assigned = 0
        route_counts = {}
        for ri, route in enumerate(params["rotations"]):
            count = (args.n_per_profile - assigned if ri == len(params["rotations"]) - 1
                     else round(args.n_per_profile * float(route["share"]["value"])))
            assigned += count
            seed = 600_000 + ei * 100 + ri
            boxes = module.generate_load_list(route["id"], 0, count, seed=seed, params=conditioned)
            assert len(boxes) == count
            assert all(b.max_gross_mass_kg == float(equipment["max_gross_kg"]["value"]) for b in boxes)
            if args.phase == "after":
                assert all(b.gross_mass_kg <= CAP_KG for b in boxes)
            route_counts[route["id"]] = count
            for box in boxes:
                samples.append({"profile": name, "route": route["id"], "seed": seed,
                                "gross_mass_kg": box.gross_mass_kg, "tare_kg": box.tare_kg,
                                "csc_rating_kg": box.max_gross_mass_kg, "empty": box.empty,
                                "weight_class": box.weight_class, "pod": box.pod})
            pooled.extend(boxes)
        summaries.append({"profile": name, "iso_type": equipment["iso_size_type"],
                          "csc_rating_kg": float(equipment["max_gross_kg"]["value"]),
                          "source_empty_share": 1.0 if force_empty else float(equipment["empty_share"]["value"]),
                          "source_equipment_share": float(equipment["share"]["value"]),
                          "route_counts": route_counts, "all": statistics(pooled),
                          "loaded": statistics([b for b in pooled if not b.empty]),
                          "empty": statistics([b for b in pooled if b.empty])})
    output = args.out or HERE / f"weights-{args.phase}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    sample_path = output.with_suffix(".samples.jsonl")
    with sample_path.open("w") as stream:
        for sample in samples:
            stream.write(json.dumps(sample, sort_keys=True) + "\n")
    result = {"phase": args.phase, "started_utc": began, "wall_s": time.perf_counter() - start,
              "pid": __import__("os").getpid(), "maxrss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "source_file": str(source.relative_to(HERE.parents[2])),
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "params_sha256": hashlib.sha256(Path("params/cargo/feeder_export.toml").read_bytes()).hexdigest(),
              "n_per_profile": args.n_per_profile, "n_total": len(samples),
              "sampling": "Equipment-conditioned public cargo provider; route counts proportional to profile call shares; original POD, empty, DG/OOG/special event rates. Shared starting seeds; unchanged equipment/POD/empty facts are independently compared after sampling.",
              "percentile": "Linear interpolation at rank (n-1)*.95", "profiles": summaries,
              "sample_path": str(sample_path)}
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("phase", "pid", "wall_s", "maxrss_bytes_macos", "n_total")}))

if __name__ == "__main__":
    main()
