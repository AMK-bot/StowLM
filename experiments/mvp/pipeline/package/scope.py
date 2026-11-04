"""Visible section boundary and mass accounting, from native departure state."""
from __future__ import annotations

import math

from stowage.checker.full import _ballast
from stowage.conventions import cells_of_slot, parse_slot
from stowage.moves import apply_plan


def departure_state(instance, plan):
    replay = apply_plan(instance, plan)
    if not replay.legal or replay.executed != len(plan.moves):
        raise ValueError('Cannot account for departure: move replay failed')
    ship, problems = _ballast(instance, plan, replay.final_state.ship)
    if problems:
        raise ValueError('Cannot account for departure: invalid ballast declaration')
    return ship


def scope_summary(instance, plan, report) -> dict:
    """No hydrostatic approximation: sum the same native state masses as the checker.

    Background box counts are null when a legacy aggregate has no per-box evidence.
    Hatch-cover mass is already in lightship; only covers left on quay are subtracted.
    """
    vessel = instance.vessel
    ship = departure_state(instance, plan)
    boxes = {box.id: box for box in instance.containers}
    planned_bays = sorted({parse_slot(cell.id).bay for cell in vessel.cells})
    background_bays = sorted({parse_slot(cell.id).bay for cell in vessel.fixed_load_cells})
    all_bays = sorted(set(planned_bays + background_bays))
    background = [load for load in vessel.fixed_loads if load.kind == 'cargo']
    complete = all(load.cargo_boxes is not None for load in background)
    background_count = sum(len(load.cargo_boxes or []) for load in background) if complete else None
    background_mass = math.fsum(load.mass_kg for load in background)
    cargo_mass = math.fsum(boxes[p.container_id].gross_mass_kg for p in ship.placements)
    fixed_other = math.fsum(load.mass_kg for load in vessel.fixed_loads if load.kind != 'cargo')
    tanks = [dict(tank=tank.id, kind=tank.kind, adjustable=tank.adjustable,
                  arrival_mass_kg=instance.onboard.tank_masses_kg.get(tank.id, 0.),
                  departure_mass_kg=ship.tank_masses_kg.get(tank.id, 0.),
                  capacity_kg=tank.capacity_m3 * tank.content_density_kg_m3)
             for tank in vessel.tanks]
    ballast = math.fsum(row['departure_mass_kg'] for row in tanks if row['kind'] == 'ballast')
    consumables = math.fsum(row['departure_mass_kg'] for row in tanks if row['kind'] != 'ballast')
    covers = {cover.id: cover for cover in vessel.hatch_covers}
    off_mass = -math.fsum(covers[cid].mass_kg for cid, dst in ship.covers_off.items() if dst == 'quay')
    entries = [('Lightship (closed hatch covers included)', getattr(vessel.hull, 'lightship_mass_kg', 0.)),
               ('Planned section cargo aboard', cargo_mass), ('Fixed synthetic background cargo', background_mass),
               ('Other fixed loads', fixed_other), ('Ballast tanks', ballast),
               ('Fuel, fresh water and other tanks', consumables), ('Hatch covers left on quay', off_mass)]
    weights = [dict(component=name, mass_kg=mass, mass_t=mass / 1000) for name, mass in entries]
    total = math.fsum(mass for _, mass in entries)
    part = report.parts.get('stability')
    displacement = part.metrics.get('displacement_kg') if part else None
    residual = total - displacement if displacement is not None else None
    closure = residual is not None and math.isclose(total, displacement, rel_tol=1e-10, abs_tol=1e-5)
    # A stored checker displacement inconsistent with the actual plan cannot be presented as closing.
    if residual is not None and not closure:
        raise ValueError(f'Weight table does not close to checker displacement: {residual:g} kg')
    n = len(ship.placements)
    background_text = f'{background_count:,}' if background_count is not None else 'an unrecorded number of'
    if vessel.slice_of:
        banner = (f'Half-ship section: {n:,} boxes planned in {len(planned_bays)} of {len(all_bays)} bays. '
                  f'Other bays hold {background_text} fixed synthetic background boxes ({background_mass / 1000:,.0f} t). '
                  'Stability and strength include them. No ship can sail on this plan alone.')
    else:
        banner = (f'Synthetic section: {n:,} boxes planned in {len(planned_bays)} bays. '
                  f'Fixed synthetic background cargo: {background_mass / 1000:,.0f} t. '
                  'No ship can sail on this plan alone.')
    changed = any(abs(row['departure_mass_kg'] - row['arrival_mass_kg']) > 1e-6 for row in tanks if row['kind'] == 'ballast')
    ballast_line = (f'Ballast {"departure allocation" if changed else "unchanged from arrival"}: {ballast / 1000:,.0f} t. '
                    'Tank contents are listed in tank-plan.csv; no cargo-work transfer programme is checked.')
    ports = {}
    for placement in ship.placements:
        box = boxes[placement.container_id]
        row = ports.setdefault(box.pod, dict(pod=box.pod, boxes=0, mass_kg=0.))
        row['boxes'] += 1
        row['mass_kg'] += box.gross_mass_kg
    return dict(synthetic=True, banner=banner, headline='An empty section, on a ship whose other bays are a fixed synthetic load'
                if not instance.onboard.placements and vessel.slice_of else 'Synthetic cargo section handover',
                planned_boxes=n, planned_bays=planned_bays, background_bays=background_bays,
                all_bays=all_bays, background_boxes=background_count, background_mass_kg=background_mass,
                background_mass_t=background_mass / 1000, background_count_complete=complete,
                weights=weights, weight_total_kg=total, displacement_kg=displacement,
                weight_residual_kg=residual, weight_closes=closure, tanks=tanks, ballast_mass_kg=ballast,
                ballast_line=ballast_line, weight_by_port=[dict(row, mass_t=row['mass_kg'] / 1000)
                                                         for _, row in sorted(ports.items())],
                background_port_note='Fixed background cargo has no declared POD; listed separately, never assigned to a port.')
