"""Per-candidate attribution of the D36 reference displacement screen (no gate changed).

Reproduces the real producer's setup (``random_yard.generate_random_instance``
up to the draw: parent/region/tiny region, rule-freeze cargo params, D36 target
count) and then draws candidates with the producer's own seeds
(``seed * 1_000_000 + attempt``). For each candidate it evaluates the unchanged
``random_export`` reference screen and records which component fails and by
how much. No physics (recondition, C1, background gate) is run: the reference
screen depends only on the drawn boxes and the parent's policy.

    uv run python experiments/background-1028/d64-displacement/measure_reference_screen.py \
        --out experiments/background-1028/d64-displacement/reference-screen.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import statistics

from stowage.generate import random_export
from stowage.generate import random_yard as ry
from stowage.ports.profiles import load_port, scaled_yard
from stowage.rules.catalogue import rule_freeze_v1


def setup(vessel_name, size, port_name, cargo_profile):
    from stowage.generate.random_cargo import RandomCargoDraws, prepare_random_cargo
    rung = ry.SIZE_LADDER[size]
    parent = ry._full_vessel(vessel_name)
    vessel = ry._decision_region(parent, size)
    if size == "tiny":
        vessel = ry._tiny_region(vessel)
    port = load_port(port_name)
    rows = min(rung.yard_rows, min(b.n_rows for b in port.yard.blocks))
    bays = min(rung.yard_bays, min(b.n_bays for b in port.yard.blocks))
    port = scaled_yard(port, blocks=rung.yard_blocks, bays=bays, rows=rows)
    params, rotation = prepare_random_cargo(port, cargo_profile=cargo_profile)
    if size == "tiny":
        params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
    count, teu_budget, _ = ry.capacity_load_size(vessel, rung, params, full_vessel=parent)
    return parent, vessel, params, rotation, count, RandomCargoDraws()


def components(policy, audit):
    out = []
    if (not policy.mass_limited and audit["teu_fraction"] < 0.75) or audit["teu_fraction"] > 0.90:
        out.append("teu")
    f = audit["cargo_mass_fraction"]
    if f < 0.75:
        out.append("cargo_low")
    elif f > 0.90:
        out.append("cargo_high")
    d = audit["design_displacement_fraction"]
    if d < 0.90:
        out.append("disp_low")
    elif d > 1.0:
        out.append("disp_high")
    return out


def run(vessel_name, size, port_name, profile, seeds, attempts):
    parent, region, params, rotation, count, draws = setup(vessel_name, size, port_name, profile)
    policy = random_export.full_export_policy(parent, params)
    scale = len(region.cells) / policy.capacity_teu
    rows = []
    for seed in seeds:
        for attempt in range(attempts):
            boxes = draws.draw(rotation, 0, count, seed=seed * 1_000_000 + attempt, params=params)
            mass = sum(b.gross_mass_kg for b in boxes)
            teu = sum(b.length_ft / 20 for b in boxes)
            audit = policy.audit(mass / scale, teu / scale)
            reasons = random_export.export_rejections(region, boxes, params, full_vessel=parent)
            rows.append(dict(seed=seed, attempt=attempt, n=len(boxes), mass_t=mass / 1e3,
                             mean_box_t=mass / len(boxes) / 1e3,
                             cargo_fraction=audit["cargo_mass_fraction"],
                             displacement_fraction=audit["design_displacement_fraction"],
                             teu_fraction=audit["teu_fraction"],
                             components=components(policy, audit), reasons=reasons))
    # Mass window per box implied by the band (solve f(c) = bound on the scaled booking).
    lo = hi = None
    grid = [policy.design_displacement_kg * i / 20000 for i in range(20001)]
    ok = [c for c in grid if 0.75 <= c / policy.cargo_budget_kg(c) <= 0.90
          and 0.90 <= (policy.base_kg + c + policy.ballast_kg(c)) / policy.design_displacement_kg <= 1.0]
    if ok:
        lo, hi = min(ok), max(ok)
    per_box = lambda c: c * scale / count / 1e3 if c is not None else None
    tally = Counter(tuple(r["components"]) for r in rows)
    return dict(vessel=vessel_name, size=size, profile=profile, region_cells=len(region.cells),
                capacity_teu=policy.capacity_teu, count=count, scale=scale,
                mean_teu=policy.mean_teu, mean_mass_t=policy.mean_mass_kg / 1e3,
                target_count=policy.target_count, mass_limited=policy.mass_limited,
                full_cargo_window_t=[lo and lo / 1e3, hi and hi / 1e3],
                accepted_mean_box_window_t=[per_box(lo), per_box(hi)],
                expected_reference_cargo_fraction=random_export.ExportPolicy.audit(
                    policy, count * policy.mean_mass_kg / scale, count / scale)["cargo_mass_fraction"],
                candidates=len(rows),
                reject_rate=sum(bool(r["reasons"]) for r in rows) / len(rows),
                component_tally={"+".join(k) or "pass": v for k, v in tally.items()},
                mean_box_t=dict(mean=statistics.mean(r["mean_box_t"] for r in rows),
                                sd=statistics.pstdev(r["mean_box_t"] for r in rows)),
                rows=rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vessels", nargs="+", default=["feeder_500", "feeder_800"])
    ap.add_argument("--sizes", nargs="+", default=["tiny", "small"])
    ap.add_argument("--profiles", nargs="+", default=["feeder_export", "dg_stress"])
    ap.add_argument("--port", default="laem_chabang_like")
    ap.add_argument("--seeds", nargs="+", type=int, default=[710, 711, 712])
    ap.add_argument("--attempts", type=int, default=20)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    results = []
    with rule_freeze_v1(enabled=True):
        for v in args.vessels:
            for s in args.sizes:
                for p in args.profiles:
                    r = run(v, s, args.port, p, args.seeds, args.attempts)
                    summary = {k: r[k] for k in r if k != "rows"}
                    print(json.dumps(summary), flush=True)
                    results.append(r)
    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=1)


if __name__ == "__main__":
    main()
