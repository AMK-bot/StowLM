"""D64 rev-3 necessary current-prefix screen, using qualified 3.7 rows.

The linear program asks whether *some* allowed ballast can pass these relaxed
in-port rows. Infeasibility can prune; feasibility does not certify a plan.
Outside the measured D49 domain, or on solver trouble, the answer is undecided.
See experiments/stability/in-port-criteria/DESIGN.md §4. In particular rev 3
explicitly drops displacement: a seagoing maximum is unsafe in denser water.

The checker owns IN_PORT_EVERY_LIFT_ENABLED. This capability declaration must
mean DESIGN rev-3 criteria at every lift, including compulsory maximum-FSM
charges for anti-heeling and carried consumable tanks. It must not be set for
sampling-only coverage or before the rev-3 FSM policy lands. Its absence is
deliberately false: primitive physics alone does not establish that coverage.
The separate mask cost/pruning gate also stays off until warm medium/full
measurements establish useful pruning at acceptable per-step cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, linprog

RULE_ID = 'port.stability.cargo_operations'

# Separate from checker coverage: the mask must first show useful pruning at
# acceptable warm medium/full latency. Offline probes may explicitly patch it.
D64_COST_PRUNING_QUALIFIED = False


@dataclass(frozen=True)
class InPortResult:
    status: Literal['feasible', 'reject', 'undecided']
    reason: str
    rule_id: str = RULE_ID


def in_port_screen_enabled() -> bool:
    """Cheap shared gate; never enables from a catalogue parameter alone."""
    from stowage.checker import full
    from stowage.rules import catalogue
    return (D64_COST_PRUNING_QUALIFIED
            and catalogue.rule_freeze_v1_enabled()
            and getattr(full, 'IN_PORT_EVERY_LIFT_ENABLED', False) is True)


def _in_port_list_limit(instance):
    """Resolve the cargo-operation limit independently of seagoing policy."""
    from stowage.rules.catalogue import catalogue_snapshot
    catalogue = catalogue_snapshot().catalogue
    parameter = catalogue.get_rule(RULE_ID).parameters['max_list_rad']
    overrides = getattr(instance, 'rule_overrides', {})
    if 'max_list_rad' in overrides.get(RULE_ID, {}):
        value = catalogue.resolve_parameter(RULE_ID, 'max_list_rad', overrides)
    elif parameter.profile_field is not None:
        fields = parameter.profile_field.split('.')
        value = instance if fields[0] == 'instance' else getattr(instance, fields[0])
        for field in fields[1:]:
            value = getattr(value, field)
    else:
        value = parameter.value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
        raise ValueError('invalid in-port catalogue list limit')
    return float(value)


def _mandatory_fsm(model, instance):
    """A lower bound on unavoidable rev-3 FSM; unknown worked tanks add none.

    Retain the compulsory anti-heeling and carried-consumable maxima. The
    model maxima are upward-enclosed numerical reserves, so cap the retained
    charge at the checker's own maximum for that actual tank geometry. Missing
    geometry/kind/lightship metadata retains zero optimistically. Potentially
    small exempt tanks also retain zero, safe whether rev 3 keeps or removes
    the existing B3.1.12 exemption; that policy needs no guess here.
    """
    from stowage.checker.stability_loading import CONSUMABLE_KINDS
    from stowage.checker.stability_sounding import maximum_fsm
    from stowage.vessel.tanks import TankGeometry
    lightship = getattr(instance.vessel.hull, 'lightship_mass_kg', None)
    params = getattr(model, '_reference_params', None)
    if (not isinstance(lightship, (int, float)) or isinstance(lightship, bool)
            or not np.isfinite(lightship) or lightship <= 0 or not isinstance(params, dict)):
        return 0.
    tanks = {tank.id: tank for tank in getattr(instance.vessel, 'tanks', ())}
    raw = {record['id']: record for record in params.get('tanks', ())}
    carried = {tank.kind for tank in tanks.values()
               if tank.kind in CONSUMABLE_KINDS
               and instance.onboard.tank_masses_kg.get(tank.id, 0.) > 0}
    mandatory = {tid for pair in getattr(instance.vessel, 'tank_pairs', ())
                 if pair.role == 'anti_heeling' for tid in (pair.port_tank, pair.starboard_tank)}
    mandatory.update(tank.id for tank in tanks.values() if tank.kind in carried)
    total = 0.
    for i, tid in enumerate(model.tank_ids):
        if tid not in mandatory or tid not in tanks or tid not in raw:
            continue
        density = getattr(tanks[tid], 'content_density_kg_m3', None)
        if not isinstance(density, (int, float)) or not np.isfinite(density) or density <= 0:
            continue
        try:
            solid = TankGeometry.from_params(raw[tid], ship_params=params)
            exact_max, _ = maximum_fsm(solid)
        except (ValueError, KeyError, TypeError, ArithmeticError):
            continue
        charge = min(float(model.tank_fsm_max_kgm[i]), float(exact_max)*density)
        if np.isfinite(charge) and charge/lightship >= .01:
            total += max(0., charge)
    return total


def _tank_box(model, instance):
    """Affine tank masses ``offset + B*x``; ordinary pairs share one variable.

    Ordinary mirror pairs are identified by physical arms and capacities, never
    tank names. The anti-heeling pair comes from the explicit vessel relation.
    An unpaired off-centre tank has no declared symmetric operation: defer.
    """
    n = len(model.tank_ids)
    offset = np.zeros(n)
    cols, bounds, used = [], [], set()
    ids = {key: i for i, key in enumerate(model.tank_ids)}
    cap = np.asarray(model.tank_capacity_kg, dtype=float)
    adjustable = set(np.flatnonzero(model.tank_adjustable))
    for pair in getattr(instance.vessel, 'tank_pairs', ()):
        if pair.role != 'anti_heeling':
            continue
        p, s = ids[pair.port_tank], ids[pair.starboard_tank]
        if p not in adjustable or s not in adjustable or {p, s} & used:
            raise ValueError('invalid anti-heeling pair in linear model')
        total = sum(float(instance.onboard.tank_masses_kg.get(model.tank_ids[i], 0.)) for i in (p, s))
        lower, upper = max(0., total-cap[s]), min(cap[p], total)
        if not np.isfinite(total) or lower > upper:
            raise ValueError('arrival HEEL total outside capacities')
        column = np.zeros(n); column[p] = 1.; column[s] = -1.
        offset[s] = total
        cols.append(column); bounds.append((lower, upper)); used.update((p, s))
    for i in sorted(adjustable-used):
        if i in used:
            continue
        column = np.zeros(n); column[i] = 1.
        if not np.isclose(model.tank_tcg_m[i], 0., rtol=0., atol=1e-7):
            matches = [j for j in sorted(adjustable-used-{i})
                if np.allclose([model.tank_lcg_m[i], model.tank_vcg_m[i], cap[i], model.tank_tcg_m[i]],
                               [model.tank_lcg_m[j], model.tank_vcg_m[j], cap[j], -model.tank_tcg_m[j]],
                               rtol=0., atol=1e-7)]
            if len(matches) != 1:
                raise ValueError('ordinary tank lacks a unique physical mirror pair')
            j = matches[0]
            arrival = instance.onboard.tank_masses_kg
            if not np.isclose(arrival.get(model.tank_ids[i], 0.), arrival.get(model.tank_ids[j], 0.),
                              rtol=0., atol=1e-6):
                raise ValueError('asymmetric ordinary arrival fills outside symmetric box')
            column[j] = 1.; used.add(j)
        used.add(i); cols.append(column); bounds.append((0., cap[i]))
    return offset, np.column_stack(cols) if cols else np.zeros((n, 0)), bounds


def _range(const, coefficients, bounds):
    lo = hi = float(const)
    for coefficient, (lower, upper) in zip(coefficients, bounds):
        lo += coefficient*(lower if coefficient >= 0 else upper)
        hi += coefficient*(upper if coefficient >= 0 else lower)
    return lo, hi


def _domain_covers_box(model, instance, cargo, offset, basis, bounds):
    """Do not prove infeasibility merely by cutting away unqualified ballast.

    This deliberately conservative guard requires the whole admissible box to
    fit mass/draught/trim calibration. A narrower D49 model may therefore defer
    a prefix the exact checker rejects. A passing list is inside the band only
    when the band includes the checker's full operational list interval.
    """
    band = model.band
    if band.max_abs_heel_rad + 1e-12 < _in_port_list_limit(instance):
        return False
    adjustable = np.asarray(model.tank_adjustable, dtype=float)
    base_mass = model.const_mass_kg + cargo.sum() + adjustable @ offset
    mass_columns = adjustable @ basis
    low, high = _range(base_mass, mass_columns, bounds)
    if low < band.displacement_min_kg-1e-6 or high > band.displacement_max_kg+1e-6:
        return False
    d0, dd = model.draught_line
    low, high = _range(d0+dd*base_mass, dd*mass_columns, bounds)
    errors = getattr(model, 'errors', None)
    draught_error = float(getattr(errors, 'draught_m', 0.))
    trim_error = float(getattr(errors, 'trim_m', 0.))
    if (not np.isfinite([draught_error, trim_error]).all()
            or min(draught_error, trim_error) < 0):
        return False
    if low-draught_error < band.draught_min_m-1e-8 or high+draught_error > band.draught_max_m+1e-8:
        return False
    t0, tx, td = model.trim_line
    moment = model.const_moment_x_kgm + model.cell_lcg_m @ cargo + model.tank_lcg_m @ offset
    columns = tx*(model.tank_lcg_m @ basis)+td*mass_columns
    low, high = _range(t0+tx*moment+td*base_mass, columns, bounds)
    return (low-trim_error >= -band.max_abs_trim_m-1e-8
            and high+trim_error <= band.max_abs_trim_m+1e-8)


def _rows(model, instance):
    """Retain 3.7 coefficients/reserves, translating only operational limits."""
    from stowage.linear import linear_constraints
    list_limit = _in_port_list_limit(instance)
    # LIST's independently measured boundary-moment allowance is angle-specific.
    # A distinct in-port policy needs its own 3.7 boundary qualification; using
    # the seagoing angle's allowance at another angle could falsely reject.
    if not np.isclose(list_limit, model.max_list_rad, rtol=0., atol=1e-12):
        raise ValueError('in-port list limit lacks matching 3.7 boundary qualification')
    cs = linear_constraints(model, direction='necessary')
    names = ('gm', 'trim', 'list_port', 'list_starboard', 'draught')
    berth = instance.port.max_draught_m
    if berth is not None:
        names += ('draught_aft', 'draught_forward')
    indices = [cs.names.index(name) for name in names]
    ac, at, const = cs.a_cell[indices].copy(), cs.a_tank[indices].copy(), cs.const[indices].copy()
    lo, hi = cs.lb[indices].copy(), cs.ub[indices].copy()
    # GM is a numerator row; changing its floor adds delta * displacement.
    delta = .15-model.gm_min_m
    ac[0] += delta
    at[0] += delta*np.asarray(model.tank_adjustable)
    const[0] += delta*model.const_mass_kg
    # 3.7 charges every tank maximum in GM and LIST. Retain only unavoidable
    # rev-3 maxima: the ordinary worked set is unknown at a prefix, and its
    # unworked ballast could be empty/full. This only widens the qualified
    # necessary inequalities and never replaces the checker's FSM assessment.
    fsm = float(np.asarray(model.tank_fsm_max_kgm).sum())
    if not np.isfinite(fsm) or np.any(np.asarray(model.tank_fsm_max_kgm) < 0):
        raise ValueError('invalid maximum tank free-surface moments')
    removable_fsm = fsm-_mandatory_fsm(model, instance)
    const[0] -= removable_fsm
    const[2:4] -= np.tan(list_limit)*removable_fsm
    trim = instance.vessel.hull.lpp_m*np.tan(np.deg2rad(1.))
    loading = model.measurement_diagnostics.get('loading_policy')
    if loading is None:
        old_min = -min(model.max_trim_m, model.band.max_abs_trim_m)
        old_max = -old_min
    else:
        old_min = max(loading['min_trim_m'], -model.band.max_abs_trim_m)
        old_max = min(loading['max_trim_m'], model.band.max_abs_trim_m)
    lo[1] += -trim-old_min
    hi[1] += trim-old_max
    # Rev 3 mean load-line limit includes freshwater allowance. The public
    # model currently exposes only the stricter saved draught cap. Without an
    # independently declared allowance, adopting that cap could falsely prune
    # in freshwater; defer that geometry rather than invent an allowance.
    if instance.port.water_density_kg_m3 < 1025.:
        raise ValueError('freshwater allowance not exposed by linear model')
    mean_cap = instance.vessel.hull.max_draught_m
    if berth is not None:
        mean_cap = min(mean_cap, berth)
    hi[4] += mean_cap-model.band.draught_max_m
    lo[4:] = -np.inf
    if berth is not None:
        if model.hull_max_draught_m is None:
            raise ValueError('linear endpoint draught geometry unavailable')
        hi[5:] += berth-model.hull_max_draught_m
    return ac, at, const, lo, hi


class InPortPrefixScreen:
    """Prepare qualified rows once and reuse affine cargo deltas at one prefix.

    The caller owns the lifetime: discard this object after committing an
    action or mutating the instance/catalogue. All candidate ships still pass
    ``mass_vectors`` identity and manifest checks. In particular, a model
    qualified for one fixed manifest cannot silently assess a changed manifest.
    Open covers defer before building anything. Reuse does not widen D49.
    """

    def __init__(self, instance, ship, *, model=None):
        self.instance = instance
        self.ship = ship
        self.model = model
        self._prepared = False
        self._failure = None
        self._witness = None
        self.statistics = {'evaluations': 0, 'model_builds': 0, 'row_builds': 0,
                           'lp_solves': 0, 'feasible': 0, 'reject': 0, 'undecided': 0}

    def _prepare(self, ship):
        from stowage.linear import build_linear_model
        from stowage.linear.operations import mass_vectors
        prefix = self.instance.model_copy(update={
            'onboard': ship, 'load_list': [], 'discharge_list': []})
        if self.model is None:
            self.statistics['model_builds'] += 1
            self.model = build_linear_model(prefix)
        self._cargo, _ = mass_vectors(self.model, ship, prefix, ignore_adjustable=True)
        self._offset, self._basis, self._bounds = _tank_box(self.model, self.instance)
        self.statistics['row_builds'] += 1
        self._ac, at, const, self._lo, self._hi = _rows(self.model, self.instance)
        self._base = self._ac @ self._cargo + at @ self._offset + const
        columns = at @ self._basis
        self._upper, self._lower = np.isfinite(self._hi), np.isfinite(self._lo)
        self._matrix = np.vstack((columns[self._upper], -columns[self._lower]))
        self._prepared = True

    def _evaluate(self, ship):
        if not in_port_screen_enabled():
            return InPortResult('undecided', 'activation/every-lift/cost qualification gate off')
        if ship.covers_off:
            return InPortResult('undecided', 'open-hatch geometry outside linear domain')
        if self._failure is not None:
            return InPortResult('undecided', self._failure)
        from stowage.linear.operations import mass_vectors
        try:
            if not self._prepared:
                # Initial geometry is preferable to a candidate-dependent model;
                # an open initial prefix can only be assessed after closure.
                initial = ship if self.ship.covers_off else self.ship
                try:
                    self._prepare(initial)
                except (ValueError, KeyError, IndexError, FloatingPointError,
                        RuntimeError, OverflowError) as exc:
                    self._failure = str(exc)
                    return InPortResult('undecided', self._failure)
            candidate = self.instance.model_copy(update={
                'onboard': ship, 'load_list': [], 'discharge_list': []})
            cargo, _ = mass_vectors(self.model, ship, candidate, ignore_adjustable=True)
            if not _domain_covers_box(self.model, self.instance, cargo,
                                      self._offset, self._basis, self._bounds):
                return InPortResult('undecided', 'tank box outside D49 qualified domain')
            # Row coefficients and tank LP matrix stay fixed. Only these cargo
            # moments change; no model construction or 3.7 row assembly repeats.
            delta = cargo-self._cargo
            changed = np.flatnonzero(delta)
            base = self._base + self._ac[:, changed] @ delta[changed]
            rhs = np.concatenate((self._hi[self._upper]-base[self._upper],
                                  base[self._lower]-self._lo[self._lower]))
            # Numerical slack only widens the necessary relaxation.
            rhs += 1e-7*np.maximum(1., np.abs(rhs))
            if not self._bounds:
                return InPortResult('reject' if np.any(rhs < 0.) else 'feasible',
                                    'constant in-port rows')
            # An already found admissible tank fill remains a constructive
            # witness whenever its row values fit the new RHS. Re-solve only
            # when that exact witness no longer fits; no boundary guess prunes.
            if self._witness is not None and np.all(self._witness <= rhs):
                return InPortResult('feasible', 'cached admissible ballast witness')
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=OptimizeWarning,
                                        message='Unrecognized options detected.*')
                self.statistics['lp_solves'] += 1
                result = linprog(np.zeros(len(self._bounds)), A_ub=self._matrix, b_ub=rhs,
                                 bounds=self._bounds, method='highs', options={'threads': 1})
            if result.status == 2:
                return InPortResult('reject', 'necessary in-port rows infeasible')
            if result.status == 0:
                self._witness = self._matrix @ result.x
                return InPortResult('feasible', 'necessary in-port rows feasible')
            return InPortResult('undecided', 'in-port LP did not establish feasibility')
        except (ValueError, KeyError, IndexError, FloatingPointError, RuntimeError, OverflowError) as exc:
            return InPortResult('undecided', str(exc))

    def evaluate(self, ship) -> InPortResult:
        """Assess a candidate lift, deferring unknown geometry optimistically."""
        verdict = self._evaluate(ship)
        self.statistics['evaluations'] += 1
        self.statistics[verdict.status] += 1
        return verdict


def screen_in_port_prefix(instance, ship, *, model=None) -> InPortResult:
    """One-shot compatibility API; use ``InPortPrefixScreen`` across candidates."""
    return InPortPrefixScreen(instance, ship, model=model).evaluate(ship)
