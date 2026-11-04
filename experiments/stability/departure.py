"""Reproduce an illustrative full feeder departure GZ curve on one CPU.

This is a condition demonstration, not an approved loading condition. The
checker remains the sole authority on whether the whole plan is safe. The
curve uses direct free-trim geometric KN rather than cross-curve interpolation.
Sources: HullGeometry module; IS Code MSC.267(85), Part A 2.2 and Part B 3.1.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np

from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.hydrostatics import HullGeometry
from stowage.checker.stability_geometry import StabilityGeometry


def main():
    directory = Path(__file__).resolve().parent
    name = "feeder_500"
    params = load_ship_params(name)
    vessel = load_vessel(name, with_hull=False)
    h = params["hull"]
    g = StabilityGeometry.from_params(params)
    target_draught = .9 * h["design_draught_m"]
    target = g.full_submerged(target_draught, target_draught)
    target_mass = target.displacement_kg
    light_mass = h["lightship_mass_kg"]
    # Closed hatch covers are permanent equipment already in lightship.
    fixed = [(m, x, y, z) for x, y, z, m in vessel.fixed_loads]
    # APPROXIMATION: synthetic departure at90% design draught, all hold
    # cells and the two lowest deck tiers occupied by standard-height20ft cargo.
    # Cargo masses vary linearly along x to balance the real lightship LCG.
    # Empty tanks represent a declared dry condition, not an omitted correction.
    # This illustration is not asserted to meet port/strength/lashing criteria.
    selected_ids = {cid for stack in vessel.stacks
                    for cid in (stack.cell_ids[:2] if stack.on_deck else stack.cell_ids)}
    cells = [c for c in vessel.cells if c.id in selected_ids]
    fixed_loads = [(light_mass, h["lightship_lcg_m"], h["lightship_tcg_m"], h["lightship_vcg_m"]),
                   *fixed]
    cargo_total = target_mass - sum(m for m, *_ in fixed_loads)
    cargo_moment = target_mass * target.lcb_m - sum(m*x for m, x, *_ in fixed_loads)
    xs = np.array([c.lcg_m for c in cells])
    a, b = np.linalg.solve([[len(cells), xs.sum()], [xs.sum(), xs@xs]],
                           [cargo_total, cargo_moment])
    cargo_masses = a + b*xs
    if not (np.all(cargo_masses > 2200) and np.all(cargo_masses <= 32000)):
        raise ValueError("illustrative cargo masses outside declared2200–32000kg range")
    loads = [*fixed_loads,
             *[(float(m), c.lcg_m, c.tcg_m, c.vcg_base_m + 2.591 / 2)
               for m, c in zip(cargo_masses, cells, strict=True)]]
    mass = sum(row[0] for row in loads)
    cg = np.array([sum(row[0] * row[j] for row in loads) / mass for j in (1, 2, 3)])
    start = perf_counter()
    q = g.equilibrium(mass, *cg)
    equilibrium_s = perf_counter() - start
    curve_start = perf_counter()
    degrees = list(range(0, 61, 5))
    arms, _ = g.free_trim_curve(np.radians(degrees), mass, float(cg[0]), float(cg[2]), g.density_kg_m3)
    curve = list(zip(degrees, arms, strict=True))
    curve_s = perf_counter() - curve_start
    with (directory / "feeder_500_departure_gz.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["heel_deg", "heel_rad", "gz_m"])
        writer.writerows((d, math.radians(d), arm) for d, arm in curve)
    summary = dict(vessel=name, geometry="actual synthetic closed envelope; nx=48,nz=64",
                   condition="full hold + bottom two deck tiers; longitudinally balanced standard20ft cargo; dry tanks",
                   containers=len(cells), min_box_mass_kg=float(cargo_masses.min()),
                   max_box_mass_kg=float(cargo_masses.max()), box_height_m=2.591,
                   displacement_kg=mass, lcg_m=float(cg[0]), tcg_m=float(cg[1]), kg_m=float(cg[2]),
                   water_density_kg_m3=g.density_kg_m3, fsc_m=0.,
                   draught_aft_m=q.t_aft_m, draught_fwd_m=q.t_fwd_m,
                   list_rad=q.heel_rad, gm_solid_m=g.full_submerged(q.t_aft_m,q.t_fwd_m,q.heel_rad).vcb_m
                   +g.full_submerged(q.t_aft_m,q.t_fwd_m,q.heel_rad).bm_t_m-cg[2],
                   equilibrium_s=equilibrium_s, batched_13_point_curve_s=curve_s,
                   equilibrium_scaled_residual=q.residual_norm,
                   approval="illustration only; full checker was not run")
    # Build a fully validated Instance from the canonical fixture builder. The
    # exact checker ignores precomputed cross-curve tables; do not spend time
    # generating unused reference tables for this end-to-end performance sample.
    from stowage.model import HullModel, Instance
    from stowage.model.fixtures import build_instance
    from stowage.conventions import make_container_id
    from stowage.vessel.tanks import build_tanks
    from stowage.checker.stability import check_stability
    fields = {**h, **params["operating_limits"]}
    hull_data = {key: value for key, value in fields.items() if key in HullModel.model_fields}
    hull_data.update(fp_x_m=h["lpp_m"], max_draught_m=h["scantling_draught_m"],
                     hydrostatics=[], cross_curves=[], stations=[])
    # Fast submerged intentionally omits waterplane terms: construct row on base.
    base = HullGeometry.from_params(params)
    hull_data["hydrostatics"] = [base.hydrostatic_row(target_draught)]
    complete = vessel.model_copy(update={"hull": HullModel.model_validate(hull_data),
                                         "tanks": build_tanks(params)})
    containers, placements = [], []
    for i, (cell, cargo_mass) in enumerate(zip(cells, cargo_masses, strict=True), 1):
        cid = make_container_id("QZXU", i)
        containers.append(dict(id=cid, iso_type="22G1", length_ft=20, height_m=2.591,
                               tare_kg=2200, gross_mass_kg=float(cargo_mass), pol="THLCH", pod="THBKK"))
        placements.append(dict(slot=cell.id, container_id=cid))
    payload = build_instance().model_dump(mode="python")
    payload.update(id="stability_departure_feeder500", vessel=complete, containers=containers,
                   onboard=dict(vessel_id=complete.id, placements=placements), load_list=[], discharge_list=[])
    payload["yard"]["stacks"] = []
    payload["port"]["max_draught_m"] = h["scantling_draught_m"]
    instance = Instance.model_validate(payload)
    check_times = []
    for _ in range(3):
        start = perf_counter()
        report = check_stability(instance, instance.onboard, call_index=0)
        check_times.append(perf_counter()-start)
    summary.update(checker_times_s=check_times, checker_ran=report.ran,
                   checker_violation_ids=[v.rule_id for v in report.violations],
                   checker_error=report.error, checker_metrics=report.metrics,
                   approval="stability part only; full plan checker has not been run")
    (directory / "departure_metrics.json").write_text(json.dumps(summary, indent=2) + "\n")
    # Self-contained SVG, no plotting dependency or raster approximation.
    width, height = 820, 460
    left, right, top, bottom = 75, 790, 60, 385
    ymin = min(-.2, min(arm for _, arm in curve) * 1.1)
    ymax = max(.2, max(arm for _, arm in curve) * 1.1)
    x = lambda d: left + (right-left) * d / 60
    y = lambda arm: bottom - (bottom-top) * (arm-ymin) / (ymax-ymin)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<g font-family="sans-serif" font-size="13" fill="#172536">',
             '<text x="75" y="26" font-size="19">Synthetic feeder 500: illustrative departure GZ</text>',
             '<text x="75" y="45">Free trim at each heel; dry tanks; numerical samples every 5°</text>']
    for d in range(0, 61, 10):
        parts.extend([f'<line x1="{x(d):.2f}" y1="{top}" x2="{x(d):.2f}" y2="{bottom}" stroke="#e0e5eb"/>',
                      f'<text x="{x(d):.2f}" y="410" text-anchor="middle">{d}°</text>'])
    for arm in np.linspace(ymin, ymax, 6):
        parts.extend([f'<line x1="{left}" y1="{y(arm):.2f}" x2="{right}" y2="{y(arm):.2f}" stroke="#e0e5eb"/>',
                      f'<text x="65" y="{y(arm)+4:.2f}" text-anchor="end">{arm:.2f}</text>'])
    parts += [f'<line x1="{left}" y1="{y(0):.2f}" x2="{right}" y2="{y(0):.2f}" stroke="#8492a2"/>',
              '<text x="22" y="230" transform="rotate(-90 22 230)" text-anchor="middle">Righting lever GZ (m)</text>',
              '<text x="430" y="438" text-anchor="middle">Heel angle</text>',
              '<polyline fill="none" stroke="#166c9c" stroke-width="3" points="' + ' '.join(f'{x(d):.2f},{y(a):.2f}' for d,a in curve) + '"/>']
    parts += [f'<circle cx="{x(d):.2f}" cy="{y(a):.2f}" r="3" fill="#166c9c"/>' for d,a in curve]
    parts += ['</g></svg>']
    (directory / "feeder_500_departure_gz.svg").write_text("\n".join(parts) + "\n")
    print(json.dumps(summary, indent=2))
    return instance


if __name__ == "__main__":
    main()
