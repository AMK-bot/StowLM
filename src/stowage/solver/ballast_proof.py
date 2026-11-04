"""Necessary conditions that can certify an unballastable fixed stow.

A proof uses a larger feasible set than the real ship: every adjustable tank
may take any fill independently, and the hull may immerse its complete sealed
polygon envelope. If even that set cannot supply the required displacement,
no actual ballast can work. Sources: Archimedes' displacement balance, as
implemented by :mod:`stowage.vessel.hydrostatics`; IMO MSC.267(85), Part B 3.1.

Measured linear-model errors are empirical maxima over a local sampled band,
not uniform bounds over all tank fills. Therefore a failed local ballast LP is
never used here as an infeasibility certificate. These tests are deliberately
incomplete: returning None means only that no proof was established.
"""
from __future__ import annotations

from collections.abc import Mapping
import math

import numpy as np

from stowage.model import Instance, PartReport, ShipState


def prove_infeasible(
    instance: Instance, ship: ShipState, *, exact_parts: Mapping[str, PartReport] | None = None,
) -> str | None:
    """Return a rigorous necessary-condition contradiction, or no certificate.

    ``ship`` is the replayed departure stow; fixed tank fills are authoritative.
    A static stack-weight/height rejection also certifies this fixed stow:
    changing liquid fills changes neither container mass nor stack height.
    It does not prove that another cargo arrangement or the instance is infeasible.
    ``exact_parts``, if supplied, must be checker reports for this exact ship.
    They certify failure only when no adjustable tank has positive capacity,
    so there is just one possible ballast condition. No numerical physics
    checks run inside this helper. The static checker scans the finite input
    cargo/geometry (including DG pairs and any background audit); its cost
    depends on input size and remains under the caller's worker deadline.
    """
    hull = instance.vessel.hull
    if hull is None or ship.vessel_id != instance.vessel.id or ship.covers_off:
        return None
    boxes = {b.id: b for b in instance.containers}
    tanks = {t.id: t for t in instance.vessel.tanks}
    if set(ship.tank_masses_kg) - tanks.keys():
        return None
    if any(p.container_id not in boxes for p in ship.placements):
        return None
    if len({p.container_id for p in ship.placements}) != len(ship.placements):
        return None
    minimum = hull.lightship_mass_kg + sum(load.mass_kg for load in instance.vessel.fixed_loads)
    minimum += sum(boxes[p.container_id].gross_mass_kg for p in ship.placements)
    capacity = 0.0
    for tank in tanks.values():
        cap = tank.capacity_m3 * tank.content_density_kg_m3
        mass = ship.tank_masses_kg.get(tank.id, 0.0)
        if not math.isfinite(mass) or mass < 0 or mass > cap + 1e-6:
            return None
        if tank.adjustable:
            capacity += cap
        else:
            minimum += mass
    maximum = minimum + capacity
    if not all(math.isfinite(x) for x in (minimum, maximum)) or minimum <= 0:
        return None
    guard = max(1e-6, 1e-10 * maximum)
    override = instance.rule_overrides.get('iscode.loading.displacement', {})
    upper = override.get('maximum')
    lower = override.get('minimum')
    if upper is not None and math.isfinite(float(upper)) and minimum > float(upper) + guard:
        return (f'iscode.loading.displacement: every ballast fill has displacement >= {minimum:.12g} kg; '
                f'the required maximum is {float(upper):.12g} kg. Removing all adjustable ballast '
                'already exceeds the maximum (exact mass-balance bound).')
    if lower is not None and math.isfinite(float(lower)) and maximum < float(lower) - guard:
        return (f'iscode.loading.displacement: every ballast fill has displacement <= {maximum:.12g} kg; '
                f'the required minimum is {float(lower):.12g} kg. Even all adjustable tanks full '
                'cannot reach the minimum (exact mass-balance bound).')
    max_draft = min(hull.max_draught_m, instance.port.max_draught_m or hull.max_draught_m)
    if hull.min_draught_fwd_m > max_draft + 1e-9:
        return (f'iscode.loading.draft: forward draught must be >= {hull.min_draught_fwd_m:.12g} m '
                f'and <= {max_draft:.12g} m; these intervals are disjoint for every ballast fill.')
    # Ballast does not alter cargo placement, mass, or physical stack height.
    # Reuse the source-of-truth checker including its inherited/background
    # policies, and certify only these explicit ballast-independent rules.
    # Missing evidence or a failed checker invocation is never a proof.
    try:
        from stowage.checker.rules import check_static_rules
        static = check_static_rules(instance, ship, call_index=instance.call_index)
        immutable = {'expert.physical.stack_weight', 'expert.physical.tier_height'}
        violations = sorted({v.rule_id for v in static.violations
                             if v.severity == 'hard' and v.rule_id in immutable})
        if static.ran and violations:
            return ('This fixed cargo stow violates ballast-independent stack constraints: '
                    + ', '.join(violations) + '. Tank fills cannot change container '
                    'masses, stack heights, or placements; another cargo stow is not ruled out.')
    except (ValueError, KeyError, TypeError, ArithmeticError):
        pass
    # The exact checker clips these same positively weighted polygons. Clipping
    # cannot increase their area, at any trim or heel. Thus their complete
    # uncut volume is an upper bound independently of all tank free surfaces.
    try:
        from stowage.checker.stability import _context
        geometry, _ = _context(instance.vessel)
        volume = 0.0
        for weight, polygons in zip(geometry.integration_weights_m, geometry.sections):
            if weight <= 0:
                return None
            for polygon in polygons:
                cross = polygon[:, 0] * np.roll(polygon[:, 1], -1) - np.roll(polygon[:, 0], -1) * polygon[:, 1]
                volume += float(weight * abs(cross.sum()) / 2)
        buoyancy_max = volume * instance.port.water_density_kg_m3
        # The exact equilibrium accepts a relative residual of 2e-6. Relax
        # this certificate to include that tolerance, rather than proving a
        # mathematically impossible condition that the numerical checker accepts.
        if minimum * (1 - 2e-6) > buoyancy_max + guard:
            return (f'iscode.loading.displacement: minimum displacement {minimum:.12g} kg exceeds '
                    f'the complete sealed hull buoyancy {buoyancy_max:.12g} kg, even allowing '
                    'the equilibrium residual tolerance 2e-6. Archimedes mass balance cannot hold '
                    'at any trim, heel, or tank fill (polygon-envelope volume bound).')
    except (ValueError, KeyError, TypeError, ArithmeticError):
        # Missing/foreign geometry is not a physical impossibility certificate.
        pass
    if capacity == 0 and exact_parts:
        violations = sorted({v.rule_id for report in exact_parts.values() if report.ran
                             for v in report.violations if v.severity == 'hard'})
        if violations:
            return ('No adjustable tank has positive capacity, so the ballast feasible set is a singleton. '
                    'The exact checker rejects that sole condition under: ' + ', '.join(violations) + '.')
    return None
