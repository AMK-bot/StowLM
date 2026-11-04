"""D58 medium follow-up, using the verified raw-draw runner unchanged.

The only wrappers observe production gate inputs/results and add mass audits.
No screen, tank recipe, checker input or returned generator instance is changed.
Defaults:10draws per tiny/small cell,6per medium cell, max_attempts=1.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
ROOT = PARENT.parents[1]
sys.path.insert(0, str(PARENT))
spec = importlib.util.spec_from_file_location("original_background_measure", PARENT / "measure.py")
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)
original_sources = runner.sources
original_utilisation = runner.utilisation
gate_audits = {}


def sources():
    _, original = original_sources()
    manifest = deepcopy(original)
    manifest["sources"][str(Path(__file__).relative_to(ROOT))] = sha256(Path(__file__).read_bytes()).hexdigest()
    manifest["measurement_format"] = "D58-medium-witness-booking-separation-v1"
    key = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    path = HERE / "sources" / f"{key}.json"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    return key, manifest


def mass_audit(instance, witness=None):
    from stowage.vessel.fleet_conditions import slice_condition

    vessel = instance.vessel
    condition = slice_condition(vessel) or {}
    tanks = {tank.id: tank for tank in vessel.tanks}
    adjustable = {tank.id for tank in vessel.tanks if tank.adjustable}
    initial = dict(instance.onboard.tank_masses_kg)
    expected_booking = condition.get("expected_load_mass_kg", 0.)
    target_ids = set(instance.load_list)
    actual_booking = sum(box.gross_mass_kg for box in instance.containers if box.id in target_ids)
    lightship = vessel.hull.lightship_mass_kg
    fixed = sum(load.mass_kg for load in vessel.fixed_loads)
    design = condition.get("design_displacement_kg")
    maximum = vessel.hull.max_displacement_kg
    booking_displacement = lightship + fixed + sum(initial.values()) + expected_booking
    violations = [key for key, value in initial.items() if key not in tanks
                  or value < -1e-6
                  or value > tanks[key].capacity_m3*tanks[key].content_density_kg_m3+1e-6]
    audit = {
        "booking_initial_tank_masses_kg": initial,
        "booking_initial_adjustable_mass_kg": sum(mass for key, mass in initial.items() if key in adjustable),
        "booking_initial_nonadjustable_mass_kg": sum(mass for key, mass in initial.items() if key not in adjustable),
        "lightship_mass_kg": lightship, "fixed_load_mass_kg": fixed,
        "expected_booking_mass_kg": expected_booking,
        "actual_emitted_booking_mass_kg": actual_booking if target_ids else None,
        "expected_actual_booking_difference_kg": actual_booking-expected_booking if target_ids else None,
        "full_booking_initial_displacement_kg": booking_displacement,
        "D40_maximum_displacement_kg": maximum, "design_displacement_kg": design,
        "full_booking_initial_D40_headroom_kg": maximum-booking_displacement,
        "full_booking_initial_design_headroom_kg": design-booking_displacement if design is not None else None,
        "booking_tank_capacity_violation_ids": violations,
        "full_booking_initial_mass_legal": not violations and booking_displacement <= maximum+1e-5
            and (design is None or booking_displacement <= design+1e-5),
        "booking_reference_ballast_sizing": condition.get("booking_reference_ballast_sizing"),
        "booking_departure_budget": condition.get("booking_departure_budget"),
        "booking_reference_sizing_status": (condition.get("booking_reference_ballast_sizing") or {}).get("status", "missing"),
        "booking_reference_retained_headroom_kg": (condition.get("booking_reference_ballast_sizing") or {}).get("retained_displacement_headroom_kg"),
        "booking_reference_mass_x_residual": (condition.get("booking_reference_ballast_sizing") or {}).get("mass_x_moment_residual"),
    }
    if witness is not None:
        witness_masses = dict(witness.witness_tank_masses_kg)
        empty_displacement = lightship + fixed + sum(witness_masses.values())
        audit.update(
            empty_witness_tank_masses_kg=witness_masses,
            empty_witness_adjustable_mass_kg=sum(mass for key, mass in witness_masses.items() if key in adjustable),
            empty_witness_nonadjustable_mass_kg=sum(mass for key, mass in witness_masses.items() if key not in adjustable),
            empty_witness_displacement_kg=empty_displacement,
            empty_witness_D40_headroom_kg=maximum-empty_displacement,
            hypothetical_full_booking_with_empty_witness_displacement_kg=empty_displacement+expected_booking,
            hypothetical_full_booking_with_empty_witness_D40_headroom_kg=maximum-empty_displacement-expected_booking,
            fixed_consumable_tanks_unchanged=all(initial.get(key, 0.) == witness_masses.get(key, 0.)
                                                for key in tanks if key not in adjustable),
            initial_and_witness_tanks_identical=initial == witness_masses,
            D57={phase: {"beam_over_draught": vessel.hull.beam_m/metrics["draught_mean_m"],
                         "beam_over_draught_ge_3p3": vessel.hull.beam_m/metrics["draught_mean_m"] >= 3.3,
                         "beam_over_draught_clearance_to_3p5": 3.5-vessel.hull.beam_m/metrics["draught_mean_m"],
                         "displacement_kg": metrics.get("displacement_kg"),
                         "gm_m": metrics.get("gm_m"), "kg_m": metrics.get("kg_m"),
                         "b23_ratio": metrics.get("b23_ratio")}
                 for phase, metrics in witness.states.items() if "draught_mean_m" in metrics},
        )
    return audit


def utilisation(instance):
    result = original_utilisation(instance)
    result["mass_sizing_audit"] = gate_audits.get(id(instance), mass_audit(instance))
    return result


def main():
    from stowage.generate import background_acceptance as gate

    evaluate = gate.evaluate_background
    def observed(instance, **kwargs):
        result = evaluate(instance, **kwargs)
        gate_audits[id(instance)] = mass_audit(instance, result)
        return result
    gate.evaluate_background = observed
    runner.sources = sources
    runner.utilisation = utilisation
    if "--out" not in sys.argv:
        sys.argv.extend(["--out", str(HERE / "draws.jsonl")])
    if "--medium-draws" not in sys.argv:
        sys.argv.extend(["--medium-draws", "6"])
    extend = "--extend-medium-positive" in sys.argv
    if extend:
        sys.argv.remove("--extend-medium-positive")
    extension_deadline = None
    if "--extension-deadline-utc" in sys.argv:
        index = sys.argv.index("--extension-deadline-utc")
        extension_deadline = datetime.fromisoformat(sys.argv[index+1]).timestamp()
        del sys.argv[index:index+2]
    if extend and extension_deadline is None:
        raise ValueError("--extend-medium-positive requires --extension-deadline-utc")
    out = Path(sys.argv[sys.argv.index("--out")+1])
    runner.main()
    # Keep production/numerical caches warm. Each extension remains one raw
    # candidate; no shipment, profile, screen or failed draw is removed.
    while extend and time.time() < extension_deadline:
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        cells = sorted({(row["vessel"], row["generator"]) for row in rows if row["rung"] == "medium"})
        missing = [(vessel, generator) for vessel, generator in cells
                   if not any(row["accepted"] for row in rows if row["rung"] == "medium"
                              and row["vessel"] == vessel and row["generator"] == generator)]
        if not missing:
            break
        for vessel, generator in missing:
            if time.time() >= extension_deadline:
                break
            seed = max(row["seed"] for row in rows if row["rung"] == "medium"
                       and row["vessel"] == vessel and row["generator"] == generator)+1
            event = {"vessel": vessel, "generator": generator, "next_seed": seed,
                     "utc": datetime.now(timezone.utc).isoformat(),
                     "reason": "No emitted medium candidate yet; sequential extension explicitly authorized."}
            with out.with_suffix(".extensions.jsonl").open("a") as stream:
                stream.write(json.dumps(event, sort_keys=True)+"\n")
            # Restore this observation wrapper rather than nesting completed
            # runner observers on every extension. Physics still runs once.
            gate.evaluate_background = observed
            for module_name in ("stowage.generate.random_yard", "stowage.generate.instances"):
                module = sys.modules.get(module_name)
                if module is not None and hasattr(module, "evaluate_background"):
                    module.evaluate_background = observed
            gate_audits.clear()
            sys.argv = [str(Path(__file__)), "--vessels", vessel, "--rungs", "medium",
                        "--generators", generator, "--seed-start", str(seed),
                        "--medium-draws", "1", "--out", str(out)]
            runner.main()


if __name__ == "__main__":
    main()
