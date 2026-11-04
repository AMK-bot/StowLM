"""Linear stability/strength model and ballast LP (frozen interface, plan 0.9; owner 3.7).

Plain terms: the exact physics (3.1–3.3) is nonlinear (draught depends on total mass, GZ curves, buoyancy). Within a
narrow *validity band* of displacement/draught, almost everything we need is close to linear in the masses placed at
each cell and in each tank. A **linear model** stores those per-unit-mass coefficients so solvers (CP-SAT/MIP need
linear constraints), the feasibility engine and the two-step baseline can reason fast. The exact formulas stay the
judge (4.1, 4.2): every user reserves the unsafe side of the measured residual. A **ballast LP** (linear program:
minimise a linear cost subject to linear inequalities; solved here with HiGHS via scipy.optimize.linprog) picks tank
masses meeting the linearised GM/trim/list/SF/BM limits. Source: Pacino et al. 2011 (linearised stability in
stowage), IS Code 2008 for the limits.

Linearisation of GM (3.7 documents the exact form): GM ≥ g  ⇔  Σ m·z ≤ (KM(Δ) − g − FSC)·Δ; inside the band KM(Δ)·Δ is
bounded below by a linear function of Δ (secant/tangent, whichever is conservative), giving a linear constraint in the
masses. For a known departure manifest, cell VCGs use its mean physical-cell height within declared hold/deck
restrictions: a uniform standard or
high-cube manifest has exact nominal stack heights. Mixed-height placement residuals are measured using the actual
boxes. The manifest's total mass is an equality row, so the displacement band varies only with tank contents.
``stowage.linear.band_for_instance`` can narrow the tank windows further. These windows remain explicit LP rows;
they are local operating restrictions, not evidence that other ballast choices are impossible.

Point estimates use the free-surface correction for the known tank fills, following the exact checker's consumable
policy. Affine constraints still reserve each tank's largest slack free-surface moment. Signed residuals use those
actual affine rows: a GM underestimate is conservatism, while an overestimate sets its safety margin. Upper and
lower limits have separate envelopes. ``LinearErrors`` retains absolute point-prediction diagnostics; it does not
make conservative bias into another required margin. Mixed-height manifests also receive explicit analytic point
error guards and conservative per-cell height arms in the constraint rows.

Hull/slice/tank coefficients and their total-moment calibration domain are shared across bookings. Background fixed
loads enter mass, moments and weight-curve constants exactly. Instance assembly restricts the shared domain with a
narrow band and validates the background's centroid domain. Changing fixed loads invalidates an assembled model's
constants, but does not trigger another geometry-family calibration.

The default reserve is ``1.1 * (measured unsafe envelope + reference-transfer
allowance)``, separately for each bound/station.
The certificate states the observations and reference used. Physics caches use
their actual numerical inputs and linear-owned builder versions (D30), excluding
global checker/ballast versions. ``build_cache``/``warm_cache`` prepares these
families without a booking; qualification never approves a loading plan.

All arrays are numpy float64, SI units: masses kg, arms m, SF N, BM N·m. Column order: cells as ``cell_ids``, tanks
as ``tank_ids``, stations as ``station_x_m``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from stowage.frozen import frozen
from stowage.model import Instance, ShipState


@dataclass(frozen=True)
class ValidityBand:
    """Where the linear model is valid. Users must check a condition is inside before trusting it."""

    displacement_min_kg: float
    displacement_max_kg: float
    draught_min_m: float
    draught_max_m: float
    max_abs_trim_m: float
    max_abs_heel_rad: float
    # Local operating restrictions are part of the domain, including on copies.
    _tank_mass_bounds_kg: dict[str, tuple[float, float]] = field(default_factory=dict, kw_only=True, repr=False, compare=False)
    _cargo_mass_kg: float | None = field(default=None, kw_only=True, repr=False, compare=False)


@dataclass(frozen=True)
class LinearErrors:
    """Absolute point-error diagnostics, including explicit local height guards.

    Builder diagnostics separate measured family maxima from analytic guards.
    Signed affine-row envelopes in ``measurement_diagnostics['signed_errors']``
    set constraint margins; conservative gaps remain separately reported.
    Models without signed metadata retain these absolute margins for compatibility.
    """

    gm_m: float
    trim_m: float
    list_rad: float
    draught_m: float
    sf_n: np.ndarray  # (S,) per station
    bm_nm: np.ndarray  # (S,) per station


@dataclass(frozen=True)
class LinearModel:
    """Per-unit-mass coefficients for one vessel (a mass m_i kg at cell i contributes m_i·coef_i).

    Fields (C cells, T tanks, S stations):
        vessel_id, version, band, errors.
        cell_ids (C,), tank_ids (T,), station_x_m (S,).
        cell_lcg_m, cell_tcg_m, cell_vcg_m (C,): arms of a box centre in each cell (known manifest mean height below).
        tank_lcg_m, tank_tcg_m, tank_vcg_m (T,): arms at the tank's reference fill; tank_capacity_kg (T,);
            tank_fsm_max_kgm (T,): the largest free-surface moment over fills strictly between 0 and full
            (conservative when a tank is slack); tank_adjustable (T,) bool.
        sf_per_kg_cell (S, C), bm_per_kg_cell (S, C), sf_per_kg_tank (S, T), bm_per_kg_tank (S, T): influence of
            1 kg on still-water SF (N) and BM (N·m) at each station, including the buoyancy redistribution within
            the band (linearised).
        const_mass_kg, const_moment_x_kgm, const_moment_y_kgm, const_moment_z_kgm: lightship + fixed loads +
            fixed tanks; const_sf_n (S,), const_bm_nm (S,).
        water_density_kg_m3: density the model was built for; ``evaluate_linear``/``solve_ballast_lp`` raise
            ValueError if it differs from ``instance.port.water_density_kg_m3``.
        km_line (a, b): KM·Δ ≥ a + b·Δ (kg·m) over the band (conservative).
        trim_line (c0, c_mx, c_disp): trim_m ≈ c0 + c_mx·M_x + c_disp·Δ, M_x = Σ m·x (kg·m), Δ in kg.
        draught_line (d0, d_disp): mean draught_m ≈ d0 + d_disp·Δ.
        Tank arms are at a reference fill; the variation of tank centroids with fill over the band is covered by
        ``errors`` (3.7 measures it). A 40'/45' box's mass is split half to each of its two cells.
        gm_min_m, max_trim_m, max_list_rad (ship/catalogue limits), sf_max_n, sf_min_n, bm_max_nm, bm_min_nm (S,).
    """

    vessel_id: str
    version: str
    water_density_kg_m3: float
    band: ValidityBand
    errors: LinearErrors
    cell_ids: tuple[str, ...]
    tank_ids: tuple[str, ...]
    station_x_m: np.ndarray
    cell_lcg_m: np.ndarray
    cell_tcg_m: np.ndarray
    cell_vcg_m: np.ndarray
    tank_lcg_m: np.ndarray
    tank_tcg_m: np.ndarray
    tank_vcg_m: np.ndarray
    tank_capacity_kg: np.ndarray
    tank_fsm_max_kgm: np.ndarray
    tank_adjustable: np.ndarray
    sf_per_kg_cell: np.ndarray
    bm_per_kg_cell: np.ndarray
    sf_per_kg_tank: np.ndarray
    bm_per_kg_tank: np.ndarray
    const_mass_kg: float
    const_moment_x_kgm: float
    const_moment_y_kgm: float
    const_moment_z_kgm: float
    const_sf_n: np.ndarray
    const_bm_nm: np.ndarray
    km_line: tuple[float, float]
    trim_line: tuple[float, float, float]
    draught_line: tuple[float, float]
    gm_min_m: float
    max_trim_m: float
    max_list_rad: float
    sf_max_n: np.ndarray
    sf_min_n: np.ndarray
    bm_max_nm: np.ndarray
    bm_min_nm: np.ndarray

    # Builder safety state belongs to the model, rather than ephemeral attributes:
    # dataclasses.replace must preserve endpoint limits, validity guards and the
    # exact directional qualification used by constraint assembly. Defaults keep
    # the original required constructor interface usable for algebra fixtures.
    measurement_diagnostics: dict[str, Any] = field(default_factory=dict, kw_only=True, repr=False, compare=False)
    hull_length_m: float | None = field(default=None, kw_only=True, repr=False, compare=False)
    hull_max_draught_m: float | None = field(default=None, kw_only=True, repr=False, compare=False)
    hull_min_draught_fwd_m: float = field(default=0., kw_only=True, repr=False, compare=False)
    _fixed_tank_masses: dict[str, float] | None = field(default=None, kw_only=True, repr=False, compare=False)
    _reference_params: dict[str, Any] | None = field(default=None, kw_only=True, repr=False, compare=False)
    _geometry_fingerprint: str | None = field(default=None, kw_only=True, repr=False, compare=False)
    _vessel_physics_fingerprint: str | None = field(default=None, kw_only=True, repr=False, compare=False)
    _fixed_loads_fingerprint: str | None = field(default=None, kw_only=True, repr=False, compare=False)
    _cargo_mass_kg: float | None = field(default=None, kw_only=True, repr=False, compare=False)
    _tank_mass_bounds_kg: dict[str, tuple[float, float]] = field(default_factory=dict, kw_only=True, repr=False, compare=False)
    _nominal_height_m: float | None = field(default=None, kw_only=True, repr=False, compare=False)
    _manifest_ids: tuple[str, ...] | None = field(default=None, kw_only=True, repr=False, compare=False)
    _manifest_fingerprint: str | None = field(default=None, kw_only=True, repr=False, compare=False)
    _constraint_cell_vcg_m: np.ndarray | None = field(default=None, kw_only=True, repr=False, compare=False)
    _family_response_sf: np.ndarray | None = field(default=None, kw_only=True, repr=False, compare=False)
    _family_response_bm: np.ndarray | None = field(default=None, kw_only=True, repr=False, compare=False)
    _family_buoyancy_sf: np.ndarray | None = field(default=None, kw_only=True, repr=False, compare=False)
    _family_buoyancy_bm: np.ndarray | None = field(default=None, kw_only=True, repr=False, compare=False)
    _family_lightship_sf: np.ndarray | None = field(default=None, kw_only=True, repr=False, compare=False)
    _family_lightship_bm: np.ndarray | None = field(default=None, kw_only=True, repr=False, compare=False)
    coefficient_certificate: dict[str, Any] = field(default_factory=dict, kw_only=True, repr=False, compare=False)


@dataclass(frozen=True)
class LinearCondition:
    """Linear estimate of one loading condition. ``in_band`` False ⇒ don't trust it (use the exact checker)."""

    displacement_kg: float
    lcg_m: float
    tcg_m: float
    vcg_m: float
    gm_m: float
    trim_m: float
    list_rad: float
    draught_mean_m: float
    sf_n: np.ndarray
    bm_nm: np.ndarray
    in_band: bool


@dataclass(frozen=True)
class LinearConstraintSet:
    """Linear constraints for solvers: ``lb ≤ A_cell @ m_cell + A_tank @ m_tank + const ≤ ub``, one row per named
    constraint (``names[k]``, e.g. ``"gm"``, ``"trim"``, ``"list"``, ``"sf@<x>"``, ``"bm@<x>"``, ``"displacement"``).
    Margins (stated error × scale) are already applied to lb/ub. CP-SAT users scale to integers themselves."""

    names: tuple[str, ...]
    a_cell: np.ndarray  # (K, C)
    a_tank: np.ndarray  # (K, T)
    const: np.ndarray  # (K,)
    lb: np.ndarray  # (K,), -inf allowed
    ub: np.ndarray  # (K,), +inf allowed


@dataclass(frozen=True)
class BallastLPResult:
    """``status``: ``optimal``, ``infeasible`` (the LP over the model's tank ranges and validity band has no solution;
    explicit local tank windows restrict this statement to those windows — with the margins this
    is evidence, not a proof, that no ballast exists; 4.2 turns it into a proof or keeps searching), ``error``.
    ``tank_masses_kg``: adjustable tank id → kg (optimal only). ``min_violation``: for infeasible, the optimum of
    the slack-minimising LP (how far from feasible, in the constraint's units) and ``binding``: names of the
    constraints with nonzero slack."""

    status: Literal["optimal", "infeasible", "error"]
    tank_masses_kg: dict[str, float]
    objective: float | None
    min_violation: float | None
    binding: tuple[str, ...]


@frozen("linear.build_model", owner="3.7", implemented=True)
def build_linear_model(instance: Instance, *, band: ValidityBand | None = None) -> LinearModel:
    """Coefficients for ``instance.vessel`` (cells, tanks, stations) in ``instance.port`` water density.
    ``band=None``: choose a band around the expected departure displacement (onboard + load list mass, ± ballast
    range). Geometry-family calibration excludes per-instance fixed loads and bookings. Deterministic;
    complete geometry, versions, parameters and reference tables invalidate the shared cache.
    Raises ValueError if the vessel has no hull or the instance exceeds the calibrated domain."""
    from stowage.linear.physics import build_model

    return build_model(instance, band)


@frozen("linear.evaluate", owner="3.7", implemented=True)
def evaluate_linear(model: LinearModel, ship: ShipState, instance: Instance) -> LinearCondition:
    """Linear estimate of the condition ``ship`` (boxes' gross masses from ``instance.containers``). O(boxes + S·C
    touched); target < 1 ms."""
    from .operations import evaluate

    return evaluate(model, ship, instance)


@frozen("linear.constraints", owner="3.7", implemented=True)
def linear_constraints(
    model: LinearModel,
    *,
    direction: Literal["necessary", "conservative"],
    margin_scale: float = 1.0,
) -> LinearConstraintSet:
    """Explicit necessary (outward) or conservative (inward) empirical rows.

    Necessary rows screen possible completions; accepted states can be exact
    dead ends. They require independently qualified opposite residuals and LIST
    boundary evidence. Conservative rows construct candidate plans using the
    existing inward reserves. Both retain the calibrated affine predictor.
    ``margin_scale >= 1`` enlarges the chosen measured reserve. Qualifications
    are empirical; only the full exact checker certifies a departure (D49).
    """
    from .operations import constraints

    return constraints(model, margin_scale, direction=direction)


@frozen("linear.solve_ballast_lp", owner="3.7", implemented=True)
def solve_ballast_lp(
    model: LinearModel,
    ship: ShipState,
    instance: Instance,
    *,
    direction: Literal["necessary", "conservative"],
    margin_scale: float = 1.0,
    objective: Literal["min_total_ballast", "max_gm_margin"] = "min_total_ballast",
) -> BallastLPResult:
    """Solve explicitly outward screening or inward construction rows (D49).

    Adjustable tank contents in ``ship`` are ignored. A necessary optimum is
    evidence of possible completion, not checker approval. Conservative results
    also require exact checking. Deterministic HiGHS, single thread; infeasibility
    returns a result, while missing/invalid qualification raises ValueError.
    """
    from .operations import solve

    return solve(model, ship, instance, margin_scale, objective, direction=direction)
