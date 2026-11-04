"""D64 rev 3.1 two-pass liquid policy for the gated every-lift checker.

HEEL is an explicit model pair. A transfer changes actual loading moments, while
maximum FSM changes the regulatory correction alone: it never replaces the
physical liquid masses/centroids used in the equilibrium. This module does not
change the seagoing loading primitive or its defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isclose, isfinite

from stowage.checker.stability_loading import (
    CONSUMABLE_KINDS, CorrectionTank, LoadingCondition, loading_condition,
)
from stowage.checker.slots import cells_of_slot
from stowage.model import ShipState
from stowage.vessel.tanks import TankGeometry


def _equal(a, b):
    return isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-6)


def _mirrored(port, starboard):
    if (port.kind != starboard.kind or port.adjustable != starboard.adjustable
            or len(port.table) != len(starboard.table)):
        return False
    if any(not _equal(getattr(port, key), getattr(starboard, key)) for key in
           ('capacity_m3', 'content_density_kg_m3', 'x_aft_m', 'x_fore_m')):
        return False
    return all(_equal(p.tcg_m, -s.tcg_m) and all(
        _equal(getattr(p, key), getattr(s, key)) for key in
        ('volume_m3', 'lcg_m', 'vcg_m', 'fsm_m4'))
        for p, s in zip(port.table, starboard.table, strict=True))


def _validate_departure_symmetry(tanks, heel_ids, masses):
    """Match non-HEEL ballast pairs by mirrored geometry, never ID spelling.

    The model exposes only anti-heeling pairs. Ordinary paired ballast is
    identified by its mirrored sounding table and equal physical properties;
    a missing or ambiguous mate is invalid data rather than silently permitted.
    """
    remaining = {t.id: t for t in tanks if t.adjustable and t.id not in heel_ids}
    for tid in sorted(tuple(remaining)):
        if tid not in remaining:
            continue
        tank = remaining.pop(tid)
        if all(_equal(row.tcg_m, 0.) for row in tank.table):
            continue
        matches = [other for other in remaining.values() if _mirrored(tank, other)]
        if len(matches) != 1:
            raise ValueError(f'non-HEEL tank {tid} needs one unambiguous symmetric mate')
        mate = matches[0]
        remaining.pop(mate.id)
        if not _equal(masses.get(tid, 0.), masses.get(mate.id, 0.)):
            raise ValueError(f'non-HEEL departure tanks {tid}/{mate.id} must be symmetric')


@dataclass
class InPortLiquidContext:
    instance: object
    departure_ship: ShipState
    pair: object
    geometry: object
    params: dict
    max_fsm_tank_ids: frozenset[str]
    arrival_max_fsm_tank_ids: frozenset[str]
    departure_max_fsm_tank_ids: frozenset[str]
    _maximum_corrections: dict[str, CorrectionTank] = field(default_factory=dict)
    _baseline_moments: dict[str, float] = field(default_factory=dict)
    _heel_solids: dict = field(default_factory=dict)

    def for_phase(self, phase):
        """Select the A.3 FSM set while sharing immutable geometry/maxima.

        Arrival charges HEEL and arrival-carried consumables, independent of
        the departure candidate. Departure additionally charges every changed
        adjustable tank. Phase selection never mutates an existing context.
        """
        if phase == 'arrival':
            selected = self.arrival_max_fsm_tank_ids
        elif phase == 'departure':
            selected = self.departure_max_fsm_tank_ids
        else:
            raise ValueError(f'unknown in-port ballast phase {phase}')
        return replace(self, max_fsm_tank_ids=selected)

    def _pair_moment(self, port, starboard, pm, sm):
        # Hull-following HEEL tanks have a small but real fill-dependent lever.
        # Keep the closed-form estimate, then refine its upright moment using
        # the same exact sounding oracle as the loading primitive. This is a
        # scalar tank-volume root, not an additional floating equilibrium.
        from stowage.checker.stability_sounding import sounding
        raw = {record['id']: record for record in self.params.get('tanks', [])}
        value = 0.
        for tank, mass in ((port, pm), (starboard, sm)):
            if tank.id not in self._heel_solids:
                if tank.id not in raw:
                    raise ValueError(f'tank {tank.id}: exact HEEL geometry unavailable')
                self._heel_solids[tank.id] = TankGeometry.from_params(
                    raw[tank.id], ship_params=self.params)
            value += mass*sounding(self._heel_solids[tank.id],
                mass/tank.content_density_kg_m3).tcg_m
        return value

    def _heel_moment(self, candidate, phase):
        # Transverse cargo moments do not depend on stack height. Compute the
        # exact static hull/fixed-load/tank contribution once per ballast phase,
        # then add cargo and relocated covers without repeating tank geometry.
        if phase not in self._baseline_moments:
            bare = candidate.model_copy(update={'placements': [], 'covers_off': {}})
            condition = loading_condition(self.instance, bare,
                geometry=self.geometry, ship_params=self.params,
                precomputed_soundings=True, small_tank_exemption=True)
            if condition.errors:
                raise ValueError('; '.join(condition.errors))
            self._baseline_moments[phase] = condition.mass_kg*condition.tcg_m
        cells = {cell.id: cell for cell in self.instance.vessel.cells}
        boxes = {box.id: box for box in self.instance.containers}
        moment = self._baseline_moments[phase]
        for placement in candidate.placements:
            ids = cells_of_slot(placement.slot)
            if placement.container_id not in boxes or any(cid not in cells for cid in ids):
                raise ValueError('unknown cargo or cell in in-port loading moments')
            moment += boxes[placement.container_id].gross_mass_kg * sum(
                cells[cid].tcg_m for cid in ids)/len(ids)
        covers = {cover.id: cover for cover in self.instance.vessel.hatch_covers}
        for cid, destination in candidate.covers_off.items():
            if cid not in covers or (destination != 'quay' and destination not in covers):
                raise ValueError('unknown hatch cover or destination in in-port loading moments')
            moment -= covers[cid].mass_kg*covers[cid].tcg_m
            if destination != 'quay':
                moment += covers[cid].mass_kg*covers[destination].tcg_m
        return moment

    def transferred_ship(self, ship, phase='arrival', *, heel_moment_kgm=None):
        """Return the authoritative ballast phase with a clipped HEEL transfer.

        ``heel_moment_kgm`` may supply the exact *pre-transfer total* loading
        moment, including the phase's tank fills, from an incremental builder.
        Without it the existing exact loading primitive computes that moment.
        Positive transfer is port to starboard; positive heel moment lowers port.
        """
        if phase not in ('arrival', 'departure'):
            raise ValueError(f'unknown in-port ballast phase {phase}')
        baseline = self.instance.onboard if phase == 'arrival' else self.departure_ship
        masses = dict(baseline.tank_masses_kg)
        candidate = ship.model_copy(update={'tank_masses_kg': masses})
        tanks = {tank.id: tank for tank in self.instance.vessel.tanks}
        port = tanks[self.pair.port_tank]
        starboard = tanks[self.pair.starboard_tank]
        pm, sm = masses.get(port.id, 0.), masses.get(starboard.id, 0.)
        capacity = port.capacity_m3 * port.content_density_kg_m3
        lever = port.table[0].tcg_m
        if heel_moment_kgm is None:
            heel_moment_kgm = self._heel_moment(candidate, phase)
        moment = float(heel_moment_kgm)
        if not isfinite(moment):
            raise ValueError('nonfinite HEEL loading moment')
        lower, upper = max(-sm, pm-capacity), min(pm, capacity-sm)
        transfer = min(upper, max(lower, moment/(2*lever)))
        before = self._pair_moment(port, starboard, pm, sm)
        low_moment = self._pair_moment(port, starboard, pm-lower, sm+lower)
        high_moment = self._pair_moment(port, starboard, pm-upper, sm+upper)
        def residual(value):
            return moment-before+self._pair_moment(port, starboard, pm-value, sm+value)
        # Even equal table samples do not establish a constant lever between
        # samples. Test the exact geometry residual, not table interpolation.
        if abs(residual(transfer)) > 1e-6:
            if residual(lower)*residual(upper) < 0:
                from scipy.optimize import brentq
                transfer = float(brentq(residual, lower, upper, xtol=1e-8))
            else:
                transfer = min((lower, upper), key=lambda value: abs(residual(value)))
        after = self._pair_moment(port, starboard, pm-transfer, sm+transfer)
        masses[port.id], masses[starboard.id] = pm-transfer, sm+transfer
        authority = max(abs(low_moment), abs(high_moment))
        # Remaining correction in the direction demanded by this state's moment.
        remaining = (after-high_moment) if moment >= 0 else (low_moment-after)
        return candidate, {
            'heel_transfer_kg': transfer,
            'heel_transfer_moment_kgm': before-after,
            'heel_authority_kgm': authority,
            'heel_authority_remaining_kgm': max(0., remaining),
            'heel_residual_moment_kgm': moment-before+after,
            'heel_pair_total_kg': pm+sm,
        }

    def correct_loading(self, condition: LoadingCondition) -> LoadingCondition:
        """Apply exact whole-fill maxima, including empty/full worked tanks.

        Source maxima use the same geometry oracle and sounding implementation
        as the primitive. No small-tank omission is allowed for worked tanks.
        Actual liquid equilibrium remains on ``actual_tanks`` and the original
        physical centres; only the regulatory free-surface correction changes.
        """
        if condition.mass_kg <= 0:
            raise ValueError('nonpositive in-port displacement')
        raw = {record['id']: record for record in self.params.get('tanks', [])}
        tanks = {tank.id: tank for tank in self.instance.vessel.tanks}
        for tid in sorted(self.max_fsm_tank_ids):
            if tid in self._maximum_corrections:
                continue
            if tid not in raw:
                raise ValueError(f'tank {tid}: exact worked-tank geometry unavailable')
            solid = TankGeometry.from_params(raw[tid], ship_params=self.params)
            from stowage.checker.stability_sounding import maximum_fsm
            inertia, volume = maximum_fsm(solid)
            density = tanks[tid].content_density_kg_m3
            self._maximum_corrections[tid] = CorrectionTank(
                tid, density, volume, inertia*density, solid, variable=True)
        corrections = tuple(t for t in condition.tanks if t.id not in self.max_fsm_tank_ids)
        corrections += tuple(self._maximum_corrections[tid] for tid in sorted(self.max_fsm_tank_ids))
        fsm = sum(t.fsm_kgm for t in corrections)
        return replace(condition, tanks=corrections, fsm_kgm=fsm,
            fsc_m=fsm/condition.mass_kg,
            omitted_tanks=tuple(tid for tid in condition.omitted_tanks
                                if tid not in self.max_fsm_tank_ids))


def prepare_context(instance, departure_ship) -> InPortLiquidContext:
    """Validate the ballast policy and return the candidate-independent pass 1.

    Call ``for_phase('departure')`` for the candidate's pass-2 FSM convention.
    Both phases retain the arrival-carried consumable kinds; the departure
    candidate cannot alter the set used to determine the arrival changeover.
    """
    pairs = [pair for pair in instance.vessel.tank_pairs if pair.role == 'anti_heeling']
    if len(pairs) != 1:
        raise ValueError('in-port stability requires one explicit anti-heeling TankPair')
    pair = pairs[0]
    tanks = {tank.id: tank for tank in instance.vessel.tanks}
    if pair.port_tank not in tanks or pair.starboard_tank not in tanks:
        raise ValueError('explicit anti-heeling TankPair references missing tanks')
    port, starboard = tanks[pair.port_tank], tanks[pair.starboard_tank]
    if not _mirrored(port, starboard) or not port.adjustable or port.kind != 'ballast':
        raise ValueError('explicit HEEL pair must be mirrored adjustable ballast')
    arm = port.table[0].tcg_m
    if arm <= 0 or any(row.tcg_m <= 0 for row in port.table):
        raise ValueError('closed-form HEEL transfer requires positive port levers')
    heel_ids = {pair.port_tank, pair.starboard_tank}
    arrival, departure = instance.onboard.tank_masses_kg, departure_ship.tank_masses_kg
    for phase, masses in [('arrival', arrival), ('departure', departure)]:
        for tid, mass in masses.items():
            if tid not in tanks:
                raise ValueError(f'unknown {phase} tank {tid}')
            capacity = tanks[tid].capacity_m3*tanks[tid].content_density_kg_m3
            if not isfinite(mass) or not 0 <= mass <= capacity:
                raise ValueError(f'{phase} tank {tid} exceeds capacity')
    if not _equal(sum(arrival.get(tid, 0.) for tid in heel_ids),
                  sum(departure.get(tid, 0.) for tid in heel_ids)):
        raise ValueError('departure HEEL pair total must equal arrival pair total')
    _validate_departure_symmetry(instance.vessel.tanks, heel_ids, departure)
    carried = {tank.kind for tank in tanks.values() if tank.kind in CONSUMABLE_KINDS
               and arrival.get(tank.id, 0.) > 0}
    arrival_maximum = frozenset(heel_ids | {tank.id for tank in tanks.values()
                                          if tank.kind in carried})
    departure_maximum = arrival_maximum | frozenset(tank.id for tank in tanks.values()
        if tank.adjustable and arrival.get(tank.id, 0.) != departure.get(tank.id, 0.))
    from stowage.checker.stability import _context
    geometry, params = _context(instance.vessel)
    return InPortLiquidContext(instance, departure_ship, pair, geometry, params,
        arrival_maximum, arrival_maximum, departure_maximum)
