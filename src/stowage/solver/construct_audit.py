"""Read-only comparisons of a joint incumbent with the production C2 rows.

This helper never pushes actions, changes a mask, accepts a candidate or issues
an infeasibility certificate. It diagnoses whether an already chosen complete
assignment and departure ballast are a primal witness for the prefix engine's
own relaxed physics model. Necessary D49 margins remain empirical screens.
"""
from __future__ import annotations

import numpy as np

from stowage.conventions import cells_of_slot


def audit_c2_assignment(instance, assignment, ballast, engine, *, limit=24):
    """Compare fixed final cargo/shared ballast against this engine's C2 rows.

    ``assignment`` maps box IDs to final D18 slots; retained onboard cargo is
    included automatically. ``ballast`` is a tank-ID/mass mapping or the
    constructor's BallastCondition. All results are diagnostics, not gates.
    No expensive checker or new LP is run; existing 3.7 models may be rebuilt
    from their numerical caches to recover the C2 tank ordering.
    """
    from stowage.linear import band_for_instance, build_linear_model, linear_constraints

    screen = getattr(engine, "_global", None)
    tables = getattr(engine, "T", None)
    if screen is None or tables is None:
        return {"available": False, "reason": getattr(engine, "_global_error", None)
                or "native C2 screen/tables unavailable"}
    masses = getattr(ballast, "tank_masses_kg", ballast)
    targets = {p.container_id: p.slot for p in instance.onboard.placements
               if p.container_id not in instance.discharge_list}
    targets.update(assignment)
    boxes = {box.id: box for box in instance.containers}
    cell_mass = np.zeros(len(tables.cell_ids))
    for cid, slot in targets.items():
        footprint = cells_of_slot(slot)
        for cell in footprint:
            cell_mass[tables.cell_index[cell]] += boxes[cid].gross_mass_kg/len(footprint)
    model = build_linear_model(instance, band=band_for_instance(instance))
    active = np.flatnonzero(model.tank_adjustable)
    tank_ids = [model.tank_ids[i] for i in active]
    tank_mass = np.array([float(masses.get(tid, 0.)) for tid in tank_ids])
    if screen.At.shape[1] != len(tank_ids):
        return {"available": False, "reason": "current source and engine tank columns differ"}
    values = screen.A@cell_mass+screen.At@tank_mass+screen.const
    tolerances = screen._slack()
    violations = []
    for i, value in enumerate(values):
        excess = max(float(screen.lo[i]-value), float(value-screen.hi[i]), 0.)
        if excess > tolerances[i]:
            violations.append(dict(index=i, name=screen.names[i], value=float(value),
                lower=float(screen.lo[i]), upper=float(screen.hi[i]),
                raw_excess=excess, tolerance=float(tolerances[i]),
                tolerance_units=excess/max(float(tolerances[i]), 1e-15)))
    violations.sort(key=lambda row: -row["tolerance_units"])
    tank_violations = [{"id": tid, "value": float(value), "lower": float(lo), "upper": float(hi)}
                       for tid, value, lo, hi in zip(tank_ids, tank_mass, screen.tank_lo, screen.tank_hi)
                       if value < lo-1e-6 or value > hi+1e-6]
    cs = linear_constraints(model, direction="necessary")
    ordered_cell = np.array([tables.cell_index[cell] for cell in model.cell_ids])
    aligned = np.zeros((len(cs.names), len(tables.cell_ids)))
    aligned[:, ordered_cell] = cs.a_cell
    keep = np.isfinite(cs.lb) | np.isfinite(cs.ub)
    count = int(keep.sum())
    same = (count <= len(screen.names)
        and np.array_equal(aligned[keep], screen.A[:count])
        and np.array_equal(cs.a_tank[keep][:, active], screen.At[:count])
        and np.array_equal(cs.const[keep], screen.const[:count])
        and np.array_equal(cs.lb[keep], screen.lo[:count])
        and np.array_equal(cs.ub[keep], screen.hi[:count]))
    completion = getattr(engine, "completion_check", None)
    checks = completion() if callable(completion) else None
    reference = getattr(getattr(engine, "_cur", None), "ref", None)
    return dict(available=True, passed=not violations and not tank_violations,
                source="existing production C2 matrix evaluated at chosen final cargo/ballast",
                source_rows_identical_to_current_necessary_model=bool(same),
                source_row_count=count, screen_row_count=len(screen.names),
                chosen_ballast_tank_order=tank_ids, cargo_mass_kg=float(cell_mass.sum()),
                maximum_cell_share_kg=float(cell_mass.max(initial=0.)), screen_cell_cap_kg=screen.u0,
                chosen_cargo_fits_cell_cap=bool(np.all(cell_mass <= screen.u0+1e-6)),
                violation_count=len(violations), violations=violations[:limit],
                tank_bound_violations=tank_violations,
                prefix_completion=checks,
                prefix_reference_available=reference is not None,
                prefix_remaining_mass_kg=float(reference.R) if reference is not None else None)
