"""Offline exact transverse-moment capacity at a prescribed list angle.

A list limit is an angle, whereas a linear optimiser constrains mass moment.
Fixing that angle leaves two unknown draughts: displacement and longitudinal
moment balance determine them. The remaining transverse moment residual gives
exactly the additional TCG shift that would balance at the chosen angle.
Longitudinal waterplane direction has zero y component, so that shift cannot disturb the
first two balances. Liquids retain the caller's production method-.1 transfer
representation, including its direct fallback; no free-surface factor is fitted.
Source: Biran & López-Pulido (2014), ch. 2–4, floating-body moment equilibrium.

This is an offline calibration primitive, not a fleet qualification certificate
or an assertion of monotonicity beyond a stable loading branch. Each call owns
its mutable liquid caches. The supplied LoadingCondition, including its cache
contents and usage counters, remains unchanged. No persisted cache is needed.

LoadingCondition.tcg_m includes actual upright liquid moments. Its total moment
coordinate can differ from an affine model using nominal tank arms. Calibrate
that affine coordinate with ``current_affine_My + transverse_moment_shift_kgm``;
use the raw total ``capacity_kgm`` only when both upright arm conventions agree.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import numpy as np
from scipy.optimize import brentq, least_squares, root

from stowage.checker.stability_loading import liquid_cg
from stowage.vessel.hydrostatics import FloatingCondition

BOUNDARY_CALIBRATION_VERSION = '1.0'


@dataclass(frozen=True)
class BoundaryCapacity:
    """Total upright transverse moment and its added moment at a list boundary.

    ``capacity_kgm`` includes the original actual upright liquid moments.
    ``transverse_moment_shift_kgm`` is the added moment relative to the caller's
    original condition, so it also anchors a differently defined affine My.
    """
    capacity_kgm: float
    attitude: FloatingCondition
    residual_norm: float
    tcg_shift_m: float
    transverse_moment_shift_kgm: float


def boundary_capacity(geometry, condition, density, heel_rad, *, initial=None):
    """Solve the exact fixed-heel liquid/hull boundary, or raise ValueError.

    ``initial`` optionally supplies an already solved floating attitude. The
    returned total upright moment includes the condition's actual upright liquid
    moments; its sign follows the vessel convention (positive y to port).
    For an affine baseline with different tank arms, add the returned moment
    shift to that baseline instead of substituting the raw capacity.
    Independent full-equilibrium
    checks must create a fresh condition with ``tcg_m=capacity_kgm/mass_kg`` and
    new mutable caches rather than reuse CG values for the original upright TCG.
    """
    values = [condition.mass_kg, condition.lcg_m, condition.tcg_m,
              condition.kg_m, density, heel_rad, geometry.lpp_m, geometry.beam_m]
    if (not np.isfinite(values).all() or condition.mass_kg <= 0 or density <= 0
            or geometry.lpp_m <= 0 or geometry.beam_m <= 0
            or abs(heel_rad) >= math.pi/2-1e-5):
        raise ValueError('finite CG and valid heel, positive mass/density/dimensions required')
    if condition.errors:
        raise ValueError('boundary capacity requires a valid loading condition')
    # dataclasses.replace alone shares these mutable dictionaries. In particular
    # liquid_cg caches the *total* CG, which depends on the original upright TCG.
    local = replace(condition, cg_cache={}, upright_cache=dict(condition.upright_cache),
                    liquid_usage={})
    bound = geometry.max_z_m+2*geometry.beam_m
    if not np.isfinite(bound) or bound <= 0:
        raise ValueError('positive finite hull extent required')

    def state(draughts):
        aft, fore = map(float, draughts)
        cg = np.asarray(liquid_cg(local, heel_rad,
                                math.atan((aft-fore)/geometry.lpp_m)), dtype=float)
        submerged = geometry.submerged(aft, fore, heel_rad, density)
        _, along, across = geometry._axes(aft, fore, heel_rad)
        diff = np.asarray(submerged.centroid_m)-cg
        residual = np.array([(submerged.displacement_kg-condition.mass_kg)/condition.mass_kg,
                             float(diff@along)/geometry.lpp_m])
        if (not np.isfinite(cg).all() or not np.isfinite(diff).all()
                or not np.isfinite(residual).all() or not np.isfinite(across).all()):
            raise ValueError('nonfinite fixed-heel liquid/hull state')
        if abs(float(across[1])) < 1e-10:
            raise ValueError('fixed-heel transverse direction has zero y component')
        return residual, submerged, diff, across

    if initial is None:
        mean = brentq(lambda d:geometry.submerged(d,d,heel_rad,density).displacement_kg-condition.mass_kg,
                      -bound, bound, xtol=1e-10)
        seed = [mean, mean]
    else:
        seed = [initial.t_aft_m, initial.t_fwd_m]
        if not np.isfinite(seed).all():
            raise ValueError('finite initial draughts required')
    solved = root(lambda d:state(d)[0], seed, tol=1e-10, options={'eps':1e-8})
    valid = np.isfinite(solved.x).all()
    error = float(np.linalg.norm(state(solved.x)[0])) if valid else math.inf
    if not solved.success or error > 2e-8:
        # Bounded second attempt solves the same equations, never substitutes a
        # hydrostatic approximation or accepts an unfinished large residual.
        start = np.clip(solved.x if valid else seed, -bound+1e-8, bound-1e-8)
        solved = least_squares(lambda d:state(d)[0], start, bounds=(-bound,bound),
                               xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=100)
        error = float(np.linalg.norm(state(solved.x)[0]))
    if not solved.success or not np.isfinite(solved.x).all() or error > 2e-8:
        raise ValueError('fixed-heel liquid/hull boundary did not converge')
    residual, submerged, diff, across = state(solved.x)
    shift = float(diff@across)/float(across[1])
    capacity = condition.mass_kg*(condition.tcg_m+shift)
    if not np.isfinite([shift,capacity]).all():
        raise ValueError('nonfinite fixed-heel transverse capacity')
    attitude = FloatingCondition(float(solved.x[0]), float(solved.x[1]),
                                 float(heel_rad), submerged, error)
    return BoundaryCapacity(float(capacity), attitude, error, shift,
                            float(condition.mass_kg*shift))
