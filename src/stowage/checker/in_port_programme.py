"""D74 Addendum B: pure ballast timing and a rate-constrained HEEL path.

APPROXIMATION: synthetic pumping/crane rates, the single total lift order and
endpoint treatment of a transfer window are specified by DESIGN Addendum B.
This module does no hydrostatics and grants no physical acceptance: callers must
check every requested window endpoint with worked-tank FSM and exactly verify
every chosen HEEL split that differs from the unconstrained ideal.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
import math
from numbers import Integral, Real

from stowage.model import Violation


RULE = "port.ballast.transfer_rate"


def _finite(value, name):
    try:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{name} must be finite")
        result = float(value)
    except (OverflowError, TypeError) as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _number(value, name, *, positive=False):
    result = _finite(value, name)
    if result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _derived(name, calculate, *, positive=False):
    try:
        result = calculate()
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError(f"{name} must be finite") from error
    return _number(result, name, positive=positive)


def _integer(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


@dataclass(frozen=True)
class ProgrammeParameters:
    """Rates are m³/hour; allowances are hours; all internal split masses are kg."""

    ballast_pump_m3_h: float
    heel_transfer_m3_h: float
    max_cranes: int
    port_cranes: int
    gross_moves_per_hour: float
    pre_operation_allowance_h: float
    post_operation_allowance_h: float
    water_density_kg_m3: float

    def __post_init__(self):
        for name in ("ballast_pump_m3_h", "heel_transfer_m3_h", "gross_moves_per_hour",
                     "water_density_kg_m3"):
            _number(getattr(self, name), name, positive=True)
        for name in ("pre_operation_allowance_h", "post_operation_allowance_h"):
            _number(getattr(self, name), name)
        _integer(self.max_cranes, "max_cranes", minimum=1)
        _integer(self.port_cranes, "port_cranes", minimum=1)
        for name, value in (("t_lift_s", self.t_lift_s),
                            ("ballast_kg_per_s", self.ballast_kg_per_s),
                            ("heel_step_limit_kg", self.heel_step_limit_kg)):
            _number(value, name, positive=True)
        _number(self.heel_pre_operation_kg, "heel_pre_operation_kg")

    @property
    def cranes(self):
        return min(self.max_cranes, self.port_cranes)

    @property
    def t_lift_s(self):
        return _derived("t_lift_s", lambda: 3600./(self.gross_moves_per_hour*self.cranes),
                        positive=True)

    @property
    def ballast_kg_per_s(self):
        return _derived("ballast_kg_per_s",
            lambda: self.water_density_kg_m3*self.ballast_pump_m3_h/3600., positive=True)

    @property
    def heel_step_limit_kg(self):
        return _derived("heel_step_limit_kg",
            lambda: self.water_density_kg_m3*self.heel_transfer_m3_h*self.t_lift_s/3600.,
            positive=True)

    @property
    def heel_pre_operation_kg(self):
        return _derived("heel_pre_operation_kg",
            lambda: self.water_density_kg_m3*self.heel_transfer_m3_h*self.pre_operation_allowance_h)


def _field(value, name, path):
    result = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    if result is None:
        raise ValueError(f"D74 programme requires {path}.{name}")
    return result


def programme_parameters(instance) -> ProgrammeParameters:
    """Read the public production schema; absent data never gets synthetic defaults.

    ``QuayCraneSpec.count`` is the model adapter for TOML count_per_berth.
    The params/model owner supplies the labelled synthetic rates themselves.
    """
    vessel = _field(instance, "vessel", "instance")
    system = _field(vessel, "ballast_system", "vessel")
    port = _field(instance, "port", "instance")
    crane = _field(port, "quay_crane", "port")
    return ProgrammeParameters(
        ballast_pump_m3_h=_field(system, "ballast_pump_m3_h", "vessel.ballast_system"),
        heel_transfer_m3_h=_field(system, "heel_transfer_m3_h", "vessel.ballast_system"),
        max_cranes=_field(system, "max_cranes", "vessel.ballast_system"),
        port_cranes=_field(crane, "count", "port.quay_crane"),
        gross_moves_per_hour=_field(crane, "gross_moves_per_hour", "port.quay_crane"),
        pre_operation_allowance_h=_field(crane, "pre_operation_allowance_h", "port.quay_crane"),
        post_operation_allowance_h=_field(crane, "post_operation_allowance_h", "port.quay_crane"),
        water_density_kg_m3=_field(port, "water_density_kg_m3", "port"))


@dataclass(frozen=True)
class ChangeoverResult:
    """Arithmetic result; valid R1 inputs pass, with excess time reported as delay.

    ``passed`` and ``violations`` remain for finisher compatibility. They do not
    certify the separately hard pumping-window endpoints or R2 HEEL path.
    """
    passed: bool
    window_indices: tuple[int, ...]
    # Each endpoint specifies both actual ballast and regulatory FSM phases.
    window_checks: tuple[tuple[int, str, str], ...]
    metrics: dict[str, float | str | None]
    violations: tuple[Violation, ...]


def changeover_programme(instance, departure_ship, *, k: int, lift_count: int,
                         parameters: ProgrammeParameters | None = None) -> ChangeoverResult:
    """R1 lower-bound pumped mass, reported delay and required endpoint requests.

    ``lift_count=n`` means states s0..sn; k=n+1 is the no-changeover sentinel.
    B is allowed only for that sentinel. Follow Addendum B's literal ceiling
    m=ceil(max(0,tau-A)/t_lift), also at the sentinel; B changes availability,
    not that formula. A sentinel window is clipped to real states s0..sn when
    its equivalent lift count exceeds the actual sequence. No invented state
    is evaluated. This conservative endpoint obligation is reported unchanged.

    The worked example's 600t case has m=88 (ceil87.073...), not its printed87.
    Physical tank capacities, symmetry and HEEL total are the caller's already
    validated contract. This function never charges fixed tanks or HEEL twice.
    D74's 29 Oct user decision makes max(0,tau-T) a delay, never a rejection.
    The delay grants no extra HEEL step authority or pumping-window exemption.
    """
    n = _integer(lift_count, "lift_count")
    k = _integer(k, "k")
    if k > n+1:
        raise ValueError("k must be a state index in [0,lift_count+1]")
    p = parameters if parameters is not None else programme_parameters(instance)
    pairs = getattr(instance.vessel, "tank_pairs", ())
    heel_ids = {tid for pair in pairs if pair.role == "anti_heeling"
                for tid in (pair.port_tank, pair.starboard_tank)}
    if not heel_ids:
        raise ValueError("D74 programme requires an explicit anti-heeling TankPair")
    arrival = instance.onboard.tank_masses_kg
    departure = departure_ship.tank_masses_kg
    increases, decreases = [], []
    for tank in instance.vessel.tanks:
        if not tank.adjustable or tank.id in heel_ids:
            continue
        before = _number(arrival.get(tank.id, 0.), f"arrival tank {tank.id}")
        after = _number(departure.get(tank.id, 0.), f"departure tank {tank.id}")
        difference = Fraction(after)-Fraction(before)
        increases.append(max(0, difference))
        decreases.append(max(0, -difference))
    added = sum(increases, Fraction(0))
    removed = sum(decreases, Fraction(0))
    pumped_exact = max(added, removed)
    capacity_per_h = Fraction(p.water_density_kg_m3)*Fraction(p.ballast_pump_m3_h)
    moves_per_h = Fraction(p.gross_moves_per_hour)*p.cranes
    required = pumped_exact/capacity_per_h
    available = (Fraction(p.pre_operation_allowance_h)+Fraction(k)/moves_per_h
                 +(Fraction(p.post_operation_allowance_h) if k == n+1 else 0))
    # Exact arithmetic on the declared float values avoids an extra window
    # state when e.g. (.9h-.5h)*60 rounds just above the integer24.
    window_lifts = math.ceil(max(0, required-Fraction(p.pre_operation_allowance_h))*moves_per_h)
    pumped = _number(pumped_exact, "pumped_mass_kg")
    absolute_change = _number(added+removed, "abs_change_kg")
    required_h = _number(required, "pump_time_required_h")
    available_h = _number(available, "pump_time_available_h")
    delay_h = _number(max(Fraction(0), required-available), "ballast_delay_h")
    # These seconds/count conversions feed caller windows and budgets. They
    # must not overflow into unlimited time, even when the hours are finite.
    _number(required*3600, "pump_time_required_s")
    _number(available*3600, "pump_time_available_s")
    window_lifts_metric = _number(window_lifts, "pump_window_lifts")
    # A delayed changeover before the first lift still works the tanks at s0;
    # both endpoint fills need their maximum-FSM physical certificates there.
    window_end = 1 if k == 0 and delay_h > 0. else min(k, n+1)
    indices = tuple(range(max(0, k-window_lifts), window_end))
    checks = tuple(request for index in indices for request in
                   ((index, "arrival", "departure"), (index, "departure", "departure")))
    metrics = dict(changeover_k=_number(k, "changeover_k"),
        lifts_after_k=_number(n-k+1, "lifts_after_k"),
        pumped_mass_t=pumped/1000., abs_change_t=absolute_change/1000.,
        pump_time_required_h=required_h, pump_time_available_h=available_h,
        ballast_delay_h=delay_h,
        pump_window_lifts=window_lifts_metric, ballast_pump_m3_h=p.ballast_pump_m3_h,
        t_lift_s=p.t_lift_s, cranes=_number(p.cranes, "cranes"),
        gross_moves_per_hour=p.gross_moves_per_hour, rate_check_rule=RULE)
    return ChangeoverResult(True, indices, checks, metrics, ())


@dataclass(frozen=True)
class HeelPathResult:
    feasible: bool
    path_kg: tuple[float, ...]
    changed_indices: tuple[int, ...]
    forward_bands_kg: tuple[tuple[float, float], ...]
    backward_bands_kg: tuple[tuple[float, float], ...]
    failure_index: int | None
    metrics: dict[str, float | str | None]
    violations: tuple[Violation, ...]


def _band(value):
    if value is None:
        return None
    if hasattr(value, "lo_kg") and hasattr(value, "hi_kg"):
        lo, hi = value.lo_kg, value.hi_kg
    else:
        try:
            lo, hi = value
        except (TypeError, ValueError):
            return None
    if (isinstance(lo, bool) or isinstance(hi, bool)
            or not isinstance(lo, Real) or not isinstance(hi, Real)
            or not math.isfinite(lo) or not math.isfinite(hi) or lo < 0 or lo > hi):
        return None
    return float(lo), float(hi)


def _intersection(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else None


def _clip(value, band):
    return min(band[1], max(band[0], value))


def _expanded(band, allowance):
    """Round inward if floating addition would exceed the actual step bound."""
    lo = _finite(band[0]-allowance, "HEEL reachable lower bound")
    hi = _finite(band[1]+allowance, "HEEL reachable upper bound")
    if band[0]-lo > allowance:
        lo = math.nextafter(lo, band[0])
    if hi-band[1] > allowance:
        hi = math.nextafter(hi, band[1])
    return lo, hi


def heel_path(intervals: Sequence, ideal_splits_kg: Sequence[float] | None = None, *,
              arrival_split_kg: float, parameters: ProgrammeParameters,
              call_index: int) -> HeelPathResult:
    """R2 O(n) reachability, backward feasibility, then nearest-ideal path.

    Accept (lo,hi) tuples or the interval owner's lo_kg/hi_kg objects. Missing
    bands are explicit heel_step failures, never an unconstrained interval.
    Failure sets the potential linearisation-miss flag; no exact alternative
    path is claimed absent. A feasible result is only an interval-path proof:
    the caller must exactly re-evaluate every ``changed_indices`` state.
    A failed path has no measured chosen-step maximum (reported as None).
    Pre-operation time limits initial positioning; post-operation time grants
    no HEEL step. Step statistics cover successive states, not the initial
    positioning during its separate allowance. All split arguments are kg.
    """
    arrival = _number(arrival_split_kg, "arrival_split_kg")
    bands = tuple(_band(value) for value in intervals)
    if ideal_splits_kg is None:
        try:
            ideal_splits_kg = tuple(value.ideal_split_kg for value in intervals)
        except AttributeError as error:
            raise ValueError("ideal_splits_kg required for tuple/missing intervals") from error
    ideals = tuple(_number(value, "ideal HEEL split") for value in ideal_splits_kg)
    if len(ideals) != len(bands) or not bands:
        raise ValueError("one ideal split and interval are required per replay state")
    delta = parameters.heel_step_limit_kg
    ideal_max = max((abs(b-a) for a, b in zip(ideals, ideals[1:])), default=0.)
    base_metrics = dict(heel_step_limit_t=delta/1000., heel_max_ideal_step_t=ideal_max/1000.,
                        rate_check_rule=RULE)
    forward = []
    for index, band in enumerate(bands):
        if index:
            previous = forward[-1]
            expanded = _expanded(previous, delta)
            allowance = delta
        else:
            allowance = parameters.heel_pre_operation_kg
            expanded = _expanded((arrival, arrival), allowance)
        reachable = _intersection(expanded, band) if band is not None else None
        if reachable is None:
            if band is None:
                needed, reason = None, "missing or invalid admissible split interval"
            else:
                original = (forward[-1] if index else (arrival, arrival))
                needed = max(0., band[0]-original[1], original[0]-band[1])/1000.
                reason = (f"required split change at least {needed:.12g} t, "
                          f"available {allowance/1000.:.12g} t")
            violation = Violation(rule_id=RULE, severity="hard", call_index=call_index,
                move_index=index-1 if index else None, measured=needed,
                limit=allowance/1000., unit="t",
                message=f"heel_step: state={index}; {reason}; linearised interval path unavailable")
            metrics = dict(base_metrics, heel_max_step_t=None, heel_path_feasible=0.,
                heel_path_linearised_miss=1., heel_first_unreachable_state=float(index))
            return HeelPathResult(False, (), (), tuple(forward), (), index, metrics, (violation,))
        forward.append(reachable)
    backward = list(forward)
    for index in range(len(bands)-2, -1, -1):
        following = backward[index+1]
        backward[index] = _intersection(forward[index], _expanded(following, delta))
        if backward[index] is None:
            raise ArithmeticError("nonempty HEEL reachability lost during backward sweep")
    path = []
    for index, band in enumerate(backward):
        if index:
            band = _intersection(band, _expanded((path[-1], path[-1]), delta))
            if band is None:
                raise ArithmeticError("chosen HEEL split lost its feasible successor")
        path.append(_clip(ideals[index], band))
    maximum_step = max((abs(b-a) for a, b in zip(path, path[1:])), default=0.)
    metrics = dict(base_metrics, heel_max_step_t=maximum_step/1000.,
                   heel_path_feasible=1., heel_path_linearised_miss=0.)
    return HeelPathResult(True, tuple(path),
        tuple(index for index, (chosen, ideal) in enumerate(zip(path, ideals, strict=True))
              if chosen != ideal), tuple(forward), tuple(backward), None, metrics, ())
