"""Populate real background deck tiers when a synthetic departure is too stiff.

Initial transverse GM is KM minus KG: moving a fixed mass upward increases KG
without changing displacement. Source: IMO 2008 IS Code, Part A 2.2 (initial
stability), and the C3 loading-condition discussion in the physics review.

APPROXIMATION: this is a synthetic loading-manual condition screen, not a full
stability certificate. It uses the production hull's upright free-trim KM and
parameter-table interpolated consumable centres, omitting free-surface effects.
For this initial-GM screen only, heel and transverse CG are set to zero; the
actual fixed cargo retains its transverse moments and must be checked for list.
The expected decision-cargo centre is supplied by the caller, not a known plan.
Same-column transfers preserve mass and longitudinal/transverse first moments;
only actual declared deck cells receive mass. The full checker remains final.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from stowage.conventions import CONTAINER_HEIGHT_STD_M
from stowage.model import Vessel
from stowage.model.vessel import FixedLoad


def condition_deck_tiers(
    full: Vessel, params: dict, placements: list[tuple[str, float]], *,
    stack_ids: set[str], expected_load_mass_kg: float,
    expected_load_cg_m: tuple[float, float, float],
    base_loads: Sequence[FixedLoad] = (), target_gm_m: float = 2.0,
) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    """Raise cargo through same-column deck cells if solid GM exceeds 2.5 m.

    Input masses are already normalized to the desired departure displacement.
    A donor hold cell keeps at least 2.2 t (or its original smaller mass). Each
    newly occupied deck cell receives at most 20 t and respects the declared
    twenty-foot stack mass/height limits. Decision stacks and all deck stacks
    above their active covers remain unavailable. Existing deck loads are not
    changed. If deck capacity is insufficient, the audit reports that fact.
    """
    from stowage.vessel.hydrostatics import HullGeometry

    cells = {cell.id: cell for cell in full.cells}
    stacks = {stack.id: stack for stack in full.stacks}
    masses = dict(placements)
    h = params["hull"]
    points = [(h["lightship_lcg_m"], h.get("lightship_tcg_m", 0.0),
               h["lightship_vcg_m"], h["lightship_mass_kg"]),
              (*expected_load_cg_m, expected_load_mass_kg)]
    points.extend(tuple(load) for load in base_loads)
    points.extend((cells[cid].lcg_m, cells[cid].tcg_m,
                   cells[cid].vcg_base_m + CONTAINER_HEIGHT_STD_M/2, mass)
                  for cid, mass in placements)
    for tank in params.get("tanks", []):
        if tank.get("adjustable", False):
            continue
        volume = .97*tank["capacity_m3"]
        rows = tank["table"]
        centre = tuple(float(np.interp(volume, [r["volume_m3"] for r in rows],
                                       [r[key] for r in rows]))
                       for key in ("lcg_m", "tcg_m", "vcg_m"))
        points.append((*centre, volume*tank["content_density_kg_m3"]))
    mass = sum(point[3] for point in points)
    cg = [float(sum(point[axis]*point[3] for point in points)/mass) for axis in range(3)]
    geometry = HullGeometry.from_params(params)
    # Upright free trim: mass and longitudinal CG remain constant in all
    # transfers, so the waterplane and KM used by this screen remain constant.
    equilibrium = geometry._solve(mass, cg[0], 0.0, cg[2],
                                  geometry.density_kg_m3, 0.0)
    km = float(equilibrium.submerged.vcb_m + equilibrium.submerged.bm_t_m)
    before = float(km-cg[2])
    audit: dict[str, Any] = dict(
        policy="same_column_deck_tiers", target_solid_gm_m=target_gm_m,
        estimated_solid_gm_before_m=before, estimated_solid_gm_after_m=before,
        screen_km_m=km, expected_departure_kg_m=cg[2],
        expected_decision_cg_m=[float(value) for value in expected_load_cg_m],
        screen_mean_draught_m=equilibrium.mean_draught_m,
        screen_trim_m=equilibrium.trim_m, transferred_mass_kg=0.0,
        added_deck_cells=0, target_reached=before <= 2.5,
        approximation="APPROXIMATION: synthetic fixed cargo; upright free-trim solid-GM "
        "screen with parameter-table consumable centres and expected decision CG; "
        "no liquid free-surface correction or full-checker acceptance implied",
    )
    if before <= 2.5:
        return placements, audit

    active_covers = {stacks[sid].hatch_cover_id for sid in stack_ids}
    donors: dict[tuple[int, int], list[str]] = {}
    available: dict[str, float] = {}
    for cid, value in placements:
        cell = cells[cid]
        stack = stacks[cell.stack_id]
        if not stack.on_deck:
            donors.setdefault((stack.bay, stack.row), []).append(cid)
            available[cid] = max(0.0, value-2200.0)
    actions: list[tuple[str, str, float]] = []
    planned = dict(masses)
    for stack in sorted(full.stacks, key=lambda item: item.id):
        if (not stack.on_deck or not stack.cell_ids or stack.id in stack_ids or
                stack.hatch_cover_id in active_covers):
            continue
        column = donors.get((stack.bay, stack.row), [])
        # Exact same-column geometry preserves both horizontal first moments.
        column = [cid for cid in column if
                  abs(cells[cid].lcg_m-cells[stack.cell_ids[0]].lcg_m) < 1e-9 and
                  abs(cells[cid].tcg_m-cells[stack.cell_ids[0]].tcg_m) < 1e-9]
        if not column:
            continue
        used = sum(masses.get(cid, 0.0) for cid in stack.cell_ids)
        cap = stack.max_mass_20_kg or stack.max_mass_kg
        for index, cid in enumerate(stack.cell_ids):
            if cid in masses:
                continue
            cell = cells[cid]
            if not cell.accepts_20 or (index+1)*CONTAINER_HEIGHT_STD_M > stack.max_height_m+1e-9:
                break
            funds = sum(available[donor] for donor in column)
            lower_mass = planned.get(stack.cell_ids[index-1], 0.) if index else float("inf")
            amount = min(20000.0, cap-used, funds, lower_mass)
            if amount <= 1e-9:
                break
            for donor in column:
                transfer = amount*available[donor]/funds
                if transfer > 0:
                    actions.append((donor, cid, transfer))
                    available[donor] -= transfer
            used += amount
            planned[cid] = amount

    moment_capacity = sum(value*(cells[recipient].vcg_base_m-cells[donor].vcg_base_m)
                          for donor, recipient, value in actions)
    required = max(0.0, (before-target_gm_m)*mass)
    fraction = min(1.0, required/moment_capacity) if moment_capacity > 0 else 0.0
    additions: dict[str, float] = {}
    for donor, recipient, value in actions:
        transfer = value*fraction
        masses[donor] -= transfer
        additions[recipient] = additions.get(recipient, 0.0)+transfer
    result = [(cid, masses[cid]) for cid, _ in placements]
    result.extend((cid, value) for cid, value in sorted(additions.items()) if value > 0)
    # Proportional transfers can leave an upward inversion of a few ulps in
    # nominally equal deck masses. D61 E2 allows equality, not a kg tolerance.
    # Keep each stack's mass and horizontal moments; include the tiny downward
    # vertical-moment correction in this diagnostic's reported KG/GM.
    ordered = dict(result)
    order_moment_change = 0.
    for stack in full.stacks:
        occupied = [cid for cid in stack.cell_ids if cid in ordered]
        weights = sorted((ordered[cid] for cid in occupied), reverse=True)
        for cid, value in zip(occupied, weights):
            order_moment_change += (value-ordered[cid])*(
                cells[cid].vcg_base_m+CONTAINER_HEIGHT_STD_M/2)
            ordered[cid] = value
    result = [(cid, ordered[cid]) for cid, _ in result]
    after = before-fraction*moment_capacity/mass-order_moment_change/mass
    audit.update(estimated_solid_gm_after_m=after,
                 expected_departure_kg_m=cg[2]+fraction*moment_capacity/mass+order_moment_change/mass,
                 exact_gross_kg_order=True,
                 order_vertical_moment_change_kgm=order_moment_change,
                 transferred_mass_kg=sum(additions.values()),
                 added_deck_cells=int(sum(value > 0 for value in additions.values())),
                 available_vertical_moment_kgm=moment_capacity,
                 target_reached=bool(abs(after-target_gm_m) < 1e-7))
    return result, audit
