"""Full-rung export intake per ship (Task B), from the real ExportPolicy.

For each ship, with the main profile as the producer prepares it (rule freeze
ON: ``prepare_random_cargo``; OFF: source feeder_export), print the policy's
target count, TEU, expected cargo tonnes, cargo budget, departure ballast and
the D64 reserve, plus the band-edge cargo window. No draws, no physics.

    uv run python experiments/background-1028/d64-displacement/full_rung_intake.py
"""
from __future__ import annotations

from copy import deepcopy
import json

from stowage.generate import random_export
from stowage.generate import random_yard as ry
from stowage.params import load_param_file
from stowage.ports.profiles import load_port
from stowage.rules.catalogue import rule_freeze_v1


def row(ship, freeze, profile="feeder_export", dry20=False):
    with rule_freeze_v1(enabled=freeze):
        parent = ry._full_vessel(ship)
        if freeze:
            from stowage.generate.random_cargo import prepare_random_cargo
            params, _ = prepare_random_cargo(load_port("laem_chabang_like"), cargo_profile=profile)
        else:
            params = deepcopy(load_param_file("cargo", "feeder_export"))
        if dry20:  # the tiny rung's equipment conditioning, scaled to the whole ship (D36 reference)
            params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
        p = random_export.full_export_policy(parent, params)
        n = p.target_count
        cargo = n * p.mean_mass_kg
        a = p.audit(cargo, n * p.mean_teu)
        budget = p.cargo_budget_kg(cargo)
        return dict(ship=ship, freeze=freeze, profile=profile + ("/dry20" if dry20 else ""),
                    capacity_teu=p.capacity_teu,
                    design_displacement_t=p.design_displacement_kg / 1e3,
                    base_t=p.base_kg / 1e3, ballast_capacity_t=p.ballast_capacity_kg / 1e3,
                    heel_reserve_t=p.mandatory_ballast_kg / 1e3,
                    operating_ballast_capacity_t=p.operating_ballast_capacity_kg / 1e3,
                    target_count=n, target_teu=n * p.mean_teu, teu_fraction=a["teu_fraction"],
                    mass_limited=p.mass_limited, mean_box_t=p.mean_mass_kg / 1e3,
                    cargo_t=cargo / 1e3, cargo_budget_t=budget / 1e3,
                    cargo_fraction=a["cargo_mass_fraction"],
                    departure_ballast_t=a["ballast_reserve_kg"] / 1e3,
                    departure_displacement_fraction=a["design_displacement_fraction"],
                    cargo_band_upper_t=0.90 * budget / 1e3)


def main():
    ships = ("feeder_500", "feeder_800", "feeder_1700")
    rows = [row(s, f) for s in ships for f in (False, True)]
    rows += [row(s, f, dry20=True) for s in ships for f in (False, True)]
    rows += [row(s, True, profile="dg_stress") for s in ships]
    for r in rows:
        print(json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in r.items()}))


if __name__ == "__main__":
    main()
