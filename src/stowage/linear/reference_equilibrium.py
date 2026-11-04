"""Couple the liquid centres and hull balance in one numerical root.

This solves the same volume and waterplane-axis moment equations as the exact
checkers. Evaluating liquid movement inside the residual avoids repeatedly
solving a whole hull root in an outer fixed-point iteration. No tank or hull is
replaced by a simpler solid. Sources: Biran & López-Pulido (2014), ch. 2–4;
the production strength checker's coupled liquid/hull balance.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import root

from stowage.checker.stability_loading import liquid_cg
from stowage.vessel.hydrostatics import FloatingCondition


def liquid_equilibrium(geometry, condition, density, *, upright=False, initial=None):
    """Return exact coupled floating equilibrium, optionally constrained upright."""
    if initial is None:
        initial = geometry.equilibrium(condition.mass_kg, condition.lcg_m,
            0. if upright else condition.tcg_m, condition.kg_m, density)
    seed = [initial.t_aft_m, initial.t_fwd_m]
    if not upright:
        seed.append(initial.heel_rad)

    def residual(values):
        aft, fore = values[:2]
        heel = 0. if upright else values[2]
        cg = np.asarray(liquid_cg(condition, heel, math.atan((aft-fore)/geometry.lpp_m)))
        submerged = geometry.submerged(aft, fore, heel, density)
        _, along, across = geometry._axes(aft, fore, heel)
        diff = np.asarray(submerged.centroid_m)-cg
        values = [(submerged.displacement_kg-condition.mass_kg)/condition.mass_kg,
                  float(diff@along)/geometry.lpp_m]
        if not upright:
            values.append(float(diff@across)/geometry.beam_m)
        return values

    solved = root(residual, seed, tol=1e-9, options={'eps': 1e-8})
    error = float(np.linalg.norm(residual(solved.x)))
    if not np.isfinite(solved.x).all() or error > 2e-8:
        raise ValueError('coupled liquid/hull reference equilibrium did not converge')
    aft, fore = map(float, solved.x[:2])
    heel = 0. if upright else float(solved.x[2])
    return FloatingCondition(aft, fore, heel,
        geometry.submerged(aft, fore, heel, density), error)
