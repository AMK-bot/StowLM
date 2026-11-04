"""D74 R2 per-state HEEL bands with cold endpoint evidence.

APPROXIMATION: list is linearised in the upright transfer moment at the actual
state's fluid GM. Exact tank soundings retain fill-dependent transverse levers.
Finite failed bounds are narrowed toward the ideal, so a feasible path may be
missed. Every used nonideal split must still pass the full exact state check;
these endpoint proofs establish P3/P4 alone, never plan acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from scipy.optimize import brentq


class HeelIntervalNumericalError(ValueError):
    """Endpoint availability failures cannot become physical changeovers."""

    _in_port_numerical_failure = True


@dataclass(frozen=True)
class HeelInterval:
    lo_kg: float
    hi_kg: float
    ideal_split_kg: float
    endpoint_reports: tuple
    shrunk: bool = False
    linearised_miss: bool = False

    @property
    def feasible(self):
        return self.lo_kg <= self.hi_kg

    def metrics(self):
        return dict(heel_lo_kg=self.lo_kg, heel_hi_kg=self.hi_kg,
            heel_ideal_split_kg=self.ideal_split_kg,
            heel_interval_feasible=float(self.feasible),
            heel_interval_shrunk=float(self.shrunk),
            heel_interval_linearised_miss=float(self.linearised_miss))


def _pair(context, ship):
    tanks = {tank.id:tank for tank in context.instance.vessel.tanks}
    port, starboard = tanks[context.pair.port_tank], tanks[context.pair.starboard_tank]
    pm = ship.tank_masses_kg.get(port.id, 0.)
    sm = ship.tank_masses_kg.get(starboard.id, 0.)
    pc = port.capacity_m3*port.content_density_kg_m3
    sc = starboard.capacity_m3*starboard.content_density_kg_m3
    if not all(isfinite(value) for value in (pm, sm, pc, sc)):
        raise ValueError('nonfinite HEEL masses/capacities')
    if not 0 <= pm <= pc or not 0 <= sm <= sc:
        raise ValueError('HEEL masses exceed capacity')
    total = pm+sm
    if not isfinite(total):
        raise ValueError('nonfinite HEEL pair total')
    return port, starboard, pm, total, max(0.,total-sc), min(pc,total)


def ship_at_split(context, ship, port_mass_kg):
    """Copy this state's tank fills; move only within its fixed-total HEEL pair."""
    port, starboard, _, total, lo, hi = _pair(context, ship)
    if not isfinite(port_mass_kg) or not lo <= port_mass_kg <= hi:
        raise ValueError('HEEL split is nonfinite or outside fixed-total capacity bounds')
    masses = dict(ship.tank_masses_kg)
    masses[port.id], masses[starboard.id] = float(port_mass_kg), total-port_mass_kg
    return ship.model_copy(update={'tank_masses_kg':masses})


def _p34_pass(report, *, open_hatch):
    if (not report.ran or report.error is not None or
            any(v.rule_id.startswith('checker.') for v in report.violations)):
        raise HeelIntervalNumericalError(
            f'HEEL endpoint calculation unavailable: {report.error or report.violations}')
    if any(not isfinite(value) for value in (*report.metrics.values(), *report.margins.values())):
        raise ValueError('nonfinite HEEL endpoint calculation')
    if 'list_rad' not in report.margins:
        # A finite P1 failure has no list measurement; it cannot prove a bound.
        if any(v.message.startswith('P1:') for v in report.violations):
            return False
        raise ValueError('HEEL endpoint lacks P3 list evidence')
    if report.margins['list_rad'] < 0.:
        return False
    if open_hatch:
        if 'open_hatch_immersion_angle_rad' not in report.margins:
            raise ValueError('HEEL endpoint lacks P4 open-hatch evidence')
        if report.margins['open_hatch_immersion_angle_rad'] <= 0.:
            return False
    return True



@dataclass(frozen=True)
class HeelOuterBand:
    """Capacity outer bound plus an independently checked singleton certificate.

    The range is not an admissible stability interval. Only the ideal point is
    certified, and callers must refine every other point before using it.
    """
    lo_kg: float
    hi_kg: float
    ideal_split_kg: float
    ideal_certified: bool

    def metrics(self):
        return dict(heel_outer_lo_kg=self.lo_kg, heel_outer_hi_kg=self.hi_kg,
            heel_ideal_split_kg=self.ideal_split_kg,
            heel_ideal_point_certified=float(self.ideal_certified),
            heel_interval_is_outer_bound=1.)


def heel_outer_band(context, ship, ideal_report):
    """Bound all possible HEEL splits without soundings or equilibrium solves.

    Conservation of pair mass and each tank's capacity prove this outer range.
    A successful base check certifies its ideal point alone. Numerical failures
    remain failures, including at an otherwise unused ideal point.
    """
    _, _, ideal, _, lo, hi = _pair(context, ship)
    p34 = _p34_pass(ideal_report, open_hatch=bool(ship.covers_off))
    certified = p34 and not any(v.severity == 'hard' for v in ideal_report.violations)
    return HeelOuterBand(lo, hi, ideal, certified)


def compute_heel_interval(instance, ship, context, ideal_report, *, exact_check=None):
    """Linearise using actual fluid GM, then cold-verify and safely shrink bounds.

    ``ship`` already has the phase's ballast and ideal HEEL redistribution.
    ``exact_check`` accepts one ShipState; its default forces a cold, direct
    per-state evaluation with interval generation disabled to avoid recursion.
    Numerical failures always propagate; only finite physical failures shrink.
    """
    port, starboard, ideal, total, physical_lo, physical_hi = _pair(context, ship)
    mass = ideal_report.metrics.get('displacement_kg', float('nan'))
    gm = ideal_report.metrics.get('gm_m', float('nan'))
    angle = ideal_report.metrics.get('list_rad', float('nan'))
    # The checked P3 margin includes the actual catalogue/profile/override
    # bound. Do not substitute the seagoing hull limit for this effective one.
    limit = abs(angle)+ideal_report.margins.get('list_rad', float('nan'))
    if not all(isfinite(value) for value in (mass,gm,angle,limit)) or min(mass,gm,limit) <= 0.:
        raise ValueError('nonfinite or nonpositive HEEL linearisation loading/limit')
    open_hatch = bool(ship.covers_off)
    if not _p34_pass(ideal_report, open_hatch=open_hatch):
        return HeelInterval(physical_lo, physical_lo-1., ideal, ())

    def moment(split):
        value = context._pair_moment(port,starboard,split,total-split)
        if not isfinite(value):
            raise ValueError('nonfinite HEEL split moment')
        return value
    left, center, right = moment(physical_lo), moment(ideal), moment(physical_hi)
    if not left <= center <= right:
        raise ValueError('nonmonotone HEEL split moment')
    stiffness = mass*gm
    if not isfinite(stiffness):
        raise ValueError('nonfinite HEEL restoring moment')
    def invert(target):
        if target <= left:
            return physical_lo
        if target >= right:
            return physical_hi
        return float(brentq(lambda split:moment(split)-target,
                            physical_lo,physical_hi,xtol=1e-8))
    lo = min(ideal,invert(center+stiffness*(-limit-angle)))
    hi = max(ideal,invert(center+stiffness*(limit-angle)))
    if exact_check is None:
        from stowage.checker.in_port_evaluator import check_state
        exact_check = lambda candidate:check_state(instance,candidate,context,
            warm_start=False,force_direct=True,heel_intervals=False)
    proofs = {}
    def prove(split):
        if split not in proofs:
            try:
                proofs[split] = exact_check(ship_at_split(context,ship,split))
            except Exception as error:
                raise HeelIntervalNumericalError(
                    f'HEEL endpoint evaluation raised {type(error).__name__}: {error}') from error
        return _p34_pass(proofs[split],open_hatch=open_hatch)
    def shrink(bound):
        if prove(bound):
            return bound
        # Bounded conservative backoff avoids an unbounded equilibrium search.
        # The exact chosen path is checked separately, including all P1--P6.
        inward = min(abs(bound-ideal),1e-6)
        nudged = bound+inward if bound < ideal else bound-inward
        candidates = [nudged]+[ideal+fraction*(bound-ideal)
                              for fraction in (.99,.95,.8,.5,.25,0.)]
        for candidate in candidates:
            if prove(candidate):
                return candidate
        return None
    checked_lo,checked_hi = shrink(lo),shrink(hi)
    if checked_lo is None or checked_hi is None:
        return HeelInterval(physical_lo,physical_lo-1.,ideal,(),True,True)
    return HeelInterval(checked_lo,checked_hi,ideal,
        (proofs[checked_lo],proofs[checked_hi]),
        checked_lo != lo or checked_hi != hi)
