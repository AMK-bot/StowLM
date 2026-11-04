"""D49 necessary C2 screen for random-yard generation.

Use the feasibility engine's fractional-completion relaxation, not a plan
constructor. A passing LP never certifies a plan; the full checker does that.
"""
from __future__ import annotations

import numpy as np

from stowage.feasibility.global_bounds import GlobalScreen
from stowage.linear import band_for_instance, build_linear_model, linear_constraints


def _necessary_screen(instance, tables) -> GlobalScreen:
    """Adapt linear rows to the canonical C2 LP without changing its physics.

    This is the row assembly of ``GlobalScreen.from_instance``, with D49's
    explicit margin direction. Keeping this adapter local avoids changing the
    independently owned feasibility caller during the generator fix.
    """
    if not getattr(instance.vessel.hull, "stations", None):
        raise ValueError("linear model requires a hull and permissible strength stations")
    model = build_linear_model(instance, band=band_for_instance(instance))
    rows = linear_constraints(model, direction="necessary", margin_scale=1.0)
    cell_count = len(tables.cell_ids)
    columns = np.array([tables.cell_index[c] for c in model.cell_ids])
    A = np.zeros((len(rows.names), cell_count))
    A[:, columns] = rows.a_cell
    tanks = np.flatnonzero(model.tank_adjustable)
    windows = getattr(model, "_tank_mass_bounds_kg", {})
    bounds = [windows.get(model.tank_ids[i], (0.0, float(model.tank_capacity_kg[i])))
              for i in tanks]
    keep = np.isfinite(rows.lb) | np.isfinite(rows.ub)
    stack_count = len(tables.stack_ids)
    S = np.zeros((stack_count, cell_count))
    for j, cells in enumerate(tables.stack_cells):
        S[j, cells] = 1.0
    # APPROXIMATION: canonical C2 uses the largest mixed/20'/40' stack
    # limit and maximum box share per cell. Both widen the feasible set,
    # preserving necessary-screen semantics; see global_bounds.py.
    caps = np.maximum.reduce([tables.stack_lim_mixed, tables.stack_lim_20, tables.stack_lim_40])
    names = (tuple(n for n, selected in zip(rows.names, keep) if selected)
             + tuple(f"stack_mass@{s}" for s in tables.stack_ids))
    return GlobalScreen(
        np.vstack([A[keep], S]),
        np.vstack([rows.a_tank[keep][:, tanks], np.zeros((stack_count, len(tanks)))]),
        np.concatenate([rows.const[keep], np.zeros(stack_count)]),
        np.concatenate([rows.lb[keep], np.full(stack_count, -np.inf)]),
        np.concatenate([rows.ub[keep], caps]),
        [b[0] for b in bounds], [b[1] for b in bounds],
        float(tables.share.max()) if len(tables.share) else 0.0, names,
    )


def necessary_completion_failures(instance, *, engine=None, diagnostics=None) -> tuple[str, ...]:
    """Return C2 failure keys, failing closed when a requested model is absent.

    ``engine`` may reuse the C1 engine constructed with ``global_screen=0``.
    Its departure-state mapper includes retained onboard cargo and makes cells
    occupied by boxes discharged here free, exactly as the canonical engine.
    No next-action enumeration or search is run.
    """
    audit = diagnostics if diagnostics is not None else {}
    audit.update(direction="necessary", margin_scale=1.0)
    try:
        if not getattr(instance.vessel.hull, "stations", None):
            raise ValueError("linear model requires a hull and permissible strength stations")
        if engine is None:
            from stowage.actions import get_action_space
            from stowage.feasibility import FeasibilityConfig, make_feasibility_engine

            engine = make_feasibility_engine(instance, get_action_space("boxslot"),
                config=FeasibilityConfig(lookahead=0, extra={"global_screen": 0}))
        screen = _necessary_screen(instance, engine.T)
        fixed, free, remaining = engine._global_state(engine._cur.ex)
        ref = screen.reference(fixed, free, remaining)
    except (ValueError, KeyError) as exc:
        audit.update(status="unavailable", error=str(exc))
        return ("c2.unavailable",)
    audit.update(status="pass" if ref is not None else "reject",
                 lp_calls=screen.lp_calls, lp_time_s=screen.lp_time_s)
    return () if ref is not None else ("c2",)
