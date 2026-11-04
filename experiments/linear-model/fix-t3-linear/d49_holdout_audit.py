"""D49 replay of untouched physical holdouts against both public row directions.

This audits modeled quantities, not legal cargo placements or full-checker
plans. Exact replay checkpoints are independent of margin-bank promotion, so
interrupted runs resume without recomputing completed physical equilibria.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")
LEVELS = ("light", "medium", "full")
QUANTITIES = ("gm", "trim", "list", "draught", "draught_aft", "draught_forward",
              "sf", "bm", "displacement", "cargo_mass", "tank_mass")


def driver():
    spec = importlib.util.spec_from_file_location("d49_epoch_driver", HERE.parent/"epoch_requalification/driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def pin(path):
    path = Path(path).resolve()
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2, allow_nan=False)+"\n")
    temporary.replace(path)


def finite_tree(exact):
    return {key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in exact.items()}


def prepare(ship, level, source, owner):
    from stowage.linear.model import build_linear_model
    from stowage.linear.bands import band_for_instance
    from stowage.linear.physics import _prepared_params_for
    instance, _ = owner.cargo_instance(ship, level)
    _, parameters = _prepared_params_for(instance)
    snapshot = owner.snapshot(ship, parameters)
    if (source["physical_inputs_start"] != snapshot or source["physical_inputs_end"] != snapshot
            or source["physical_inputs_unchanged"] is not True):
        raise ValueError("holdout numerical/reference epoch differs from current inputs")
    model = build_linear_model(instance, band=band_for_instance(instance))
    if owner.serializable(asdict(model.band)) != source["band"]:
        raise ValueError("holdout band differs from current public model")
    return model, instance, parameters, snapshot


def replay_case(ship, level, input_root, output, *, limit=None):
    from stowage.linear.physics import exact_condition
    owner = driver()
    source_path = input_root/ship/f"holdout-{level}.json"
    source = json.loads(source_path.read_text())
    if source["phase"] != "holdout" or source["samples"] != 64 or len(source["list_row_samples"]) != 64:
        raise ValueError("expected unchanged 64-state independent holdout")
    model, instance, parameters, before = prepare(ship, level, source, owner)
    path = output/"replay"/ship/f"{level}.json"
    input_binding = {"source": pin(source_path), "physical_inputs": before,
                     "states_sha256": digest(source["list_row_samples"])}
    record = json.loads(path.read_text()) if path.exists() else {
        "format": 1, "ship": ship, "level": level, "input_binding": input_binding,
        "expected_samples": 64, "states": [], "complete": False}
    if record["input_binding"] != input_binding:
        raise ValueError("stale replay checkpoint: use a fresh output directory")
    target = min(64, limit) if limit is not None else 64
    start = time.monotonic()
    for index in range(len(record["states"]), target):
        state = source["list_row_samples"][index]
        began = time.monotonic()
        exact = exact_condition(model, instance, np.asarray(state["cell_masses_kg"]),
            np.asarray(state["tank_masses_kg"]), cell_vcg_m=np.asarray(state["cell_vcg_m"]))
        comparisons = {}
        for key in ("trim_m", "list_rad", "draught_m", "sf_n", "bm_nm"):
            observed, saved = np.asarray(exact[key]), np.asarray(state["exact_"+key])
            if not np.allclose(observed, saved, rtol=1e-9, atol=1e-6):
                raise ValueError(f"exact replay changed recorded {key}: {ship}/{level}/{index}")
            comparisons[key] = float(np.max(np.abs(observed-saved)))
        if not np.isclose(exact["displacement_kg"], state["mass_kg"], rtol=1e-12, atol=1e-5):
            raise ValueError("exact replay changed recorded mass")
        record["states"].append({"index": index, "sample_sha256": digest(state),
            "exact": finite_tree(exact), "recorded_replay_max_absolute_differences": comparisons,
            "seconds": time.monotonic()-began})
        record["complete"] = len(record["states"]) == 64
        record["physical_inputs_end"] = owner.snapshot(ship, parameters)
        if record["physical_inputs_end"] != before:
            raise ValueError("reference/numerical inputs changed during replay")
        write(path, record)
    print(json.dumps({"ship": ship, "level": level, "replayed": len(record["states"]),
        "complete": record["complete"], "call_seconds": time.monotonic()-start,
        "state_seconds": [r["seconds"] for r in record["states"]],
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2}), flush=True)
    return record


def row_group(name):
    if name.startswith("sf@"): return "sf"
    if name.startswith("bm@"): return "bm"
    if name.startswith("tank_mass@"): return "tank_mass"
    if name.startswith("list_"): return "list"
    return name


def exact_tests(model, state, exact):
    policy = model.measurement_diagnostics["loading_policy"]
    band = model.band
    draught, trim = exact["draught_m"], exact["trim_m"]
    cells, tanks = np.asarray(state["cell_masses_kg"]), np.asarray(state["tank_masses_kg"])
    def inside(value, low, high):
        return bool(np.all(np.asarray(value) >= np.asarray(low)-1e-7)
                    and np.all(np.asarray(value) <= np.asarray(high)+1e-7))
    result = dict(
        gm=inside(exact["gm_m"], model.gm_min_m, np.inf),
        trim=inside(trim, max(policy["min_trim_m"], -band.max_abs_trim_m),
                    min(policy["max_trim_m"], band.max_abs_trim_m)),
        list=inside(exact["list_rad"], -min(model.max_list_rad, band.max_abs_heel_rad),
                    min(model.max_list_rad, band.max_abs_heel_rad)),
        draught=inside(draught, band.draught_min_m, band.draught_max_m),
        draught_aft=inside(draught+trim/2, -np.inf, model.hull_max_draught_m),
        draught_forward=inside(draught-trim/2, model.hull_min_draught_fwd_m, model.hull_max_draught_m),
        displacement=inside(exact["displacement_kg"], max(policy["min_displacement_kg"], band.displacement_min_kg),
                            min(policy["max_displacement_kg"], band.displacement_max_kg)),
        sf=inside(exact["sf_n"], model.sf_min_n, model.sf_max_n),
        bm=inside(exact["bm_nm"], model.bm_min_nm, model.bm_max_nm),
        cargo_mass=True if model._cargo_mass_kg is None else inside(cells.sum(), model._cargo_mass_kg, model._cargo_mass_kg),
        tank_mass=all(inside(tanks[model.tank_ids.index(key)], lower, upper)
                      for key, (lower, upper) in model._tank_mass_bounds_kg.items()))
    return result


def audit_case(ship, level, input_root, output):
    from stowage.linear.model import linear_constraints
    owner = driver()
    source_path = input_root/ship/f"holdout-{level}.json"
    source = json.loads(source_path.read_text())
    model, _, _, before = prepare(ship, level, source, owner)
    replay_path = output/"replay"/ship/f"{level}.json"
    replay = json.loads(replay_path.read_text())
    if (replay.get("complete") is not True or replay["input_binding"]["source"] != pin(source_path)
            or replay["input_binding"]["physical_inputs"] != before
            or replay["physical_inputs_end"] != before or len(replay["states"]) != 64):
        raise ValueError("complete unchanged 64-state exact replay required")
    rows = {direction: linear_constraints(model, direction=direction)
            for direction in ("necessary", "conservative")}
    totals = {key: {"samples": 0, "exact_pass": 0, "necessary_accept": 0, "conservative_accept": 0,
                    "necessary_rejects_exact_pass": 0, "conservative_accepts_exact_fail": 0}
              for key in (*QUANTITIES, "all")}
    details = []
    for index, (state, reference) in enumerate(zip(source["list_row_samples"], replay["states"], strict=True)):
        if reference["index"] != index or reference["sample_sha256"] != digest(state):
            raise ValueError("exact replay sample binding mismatch")
        exact = exact_tests(model, state, reference["exact"])
        accepts = {}
        for direction, table in rows.items():
            values = table.a_cell@np.asarray(state["cell_masses_kg"])+table.a_tank@np.asarray(state["tank_masses_kg"])+table.const
            passed = (values >= table.lb-1e-7)&(values <= table.ub+1e-7)
            by_quantity = {key: True for key in QUANTITIES}
            for name, accepted in zip(table.names, passed, strict=True):
                key = row_group(name)
                if key not in by_quantity:
                    raise ValueError("unclassified public row: "+name)
                by_quantity[key] &= bool(accepted)
            by_quantity["all"] = all(by_quantity.values())
            accepts[direction] = by_quantity
        exact["all"] = all(exact.values())
        for key, stats in totals.items():
            stats["samples"] += 1
            stats["exact_pass"] += int(exact[key])
            for direction in rows:
                stats[direction+"_accept"] += int(accepts[direction][key])
            stats["necessary_rejects_exact_pass"] += int(exact[key] and not accepts["necessary"][key])
            stats["conservative_accepts_exact_fail"] += int(not exact[key] and accepts["conservative"][key])
        details.append({"index": index, "exact_pass": exact, "row_acceptance": accepts})
    record = {"format": 1, "ship": ship, "level": level, "samples": 64,
        "source_holdout": pin(source_path), "exact_replay": pin(replay_path),
        "physical_inputs": before, "totals": totals, "states": details,
        "policy_certificate_sha256": model.measurement_diagnostics["margin_policy"]["sha256"]}
    record["passed"] = all(not stats["necessary_rejects_exact_pass"] and not stats["conservative_accepts_exact_fail"]
                           for stats in totals.values())
    write(output/"rows"/ship/f"{level}.json", record)
    return record


def summary(output):
    import stowage.linear.margin_policy as margins
    cases = [json.loads((output/"rows"/ship/f"{level}.json").read_text()) for ship in SHIPS for level in LEVELS]
    totals = {key: {name: sum(case["totals"][key][name] for case in cases)
                    for name in cases[0]["totals"][key]} for key in (*QUANTITIES, "all")}
    source_paths = [ROOT/"src/stowage/linear"/name for name in
                    ("operations.py", "margin_policy.py", "margin_policy_data.json")]
    pins = [pin(path) for path in source_paths]
    pins += [pin(output/"rows"/ship/f"{level}.json") for ship in SHIPS for level in LEVELS]
    report = {"format": 1, "purpose": "modeled-quantity empirical row inclusion, not full-checker plan approval",
        "samples": sum(case["samples"] for case in cases), "cases": len(cases),
        "passed": all(case["passed"] for case in cases), "totals": totals,
        "pins": pins, "policy_inputs": margins.policy_inputs(),
        "required_exact_reference": "same 576 independently held-out physical states, actual saved cargo VCG"}
    write(output/"summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=HERE/"epoch-final")
    parser.add_argument("--output", type=Path, default=HERE/"d49-holdout-audit")
    parser.add_argument("--phase", choices=("replay", "rows", "summary", "all"), default="all")
    parser.add_argument("--ship", choices=SHIPS)
    parser.add_argument("--level", choices=LEVELS)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= 64:
        raise ValueError("limit must be within1..64")
    if args.phase in ("replay", "rows", "all"):
        for ship in (args.ship,) if args.ship else SHIPS:
            for level in (args.level,) if args.level else LEVELS:
                if args.phase in ("replay", "all"):
                    replay_case(ship, level, args.input_root, args.output, limit=args.limit)
                if args.phase in ("rows", "all"):
                    print(json.dumps({"case": ship+"/"+level, "passed": audit_case(ship,level,args.input_root,args.output)["passed"]}), flush=True)
    if args.phase in ("summary", "all") and not args.ship and not args.level and args.limit is None:
        report = summary(args.output)
        print(json.dumps({"summary": report["passed"], "samples": report["samples"], "totals": report["totals"]}), flush=True)
        if not report["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
