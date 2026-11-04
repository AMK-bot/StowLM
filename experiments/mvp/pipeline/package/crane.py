"""Replay-linked cargo work, with an explicitly unmitigated list estimate."""
from __future__ import annotations

import csv
import io
import math

from stowage.checker.full import departure_condition
from stowage.checker.slots import cells_of_slot
from stowage.checker.stability_loading import loading_condition
from stowage.moves import apply_plan


ESTIMATOR = ('ESTIMATE ONLY: list_deg_est = degrees(atan(transverse_moment_kgm / '
             '(instantaneous_displacement_kg * departure_gm_m))); positive = port. '
             'Departure GM is held constant; arrival tank fills use native tank-table centres. '
             'Includes lightship, fixed synthetic background, cargo and cover movements. '
             'No cargo-work hydrostatic or free-surface calculation; no heel-ballast mitigation applied.')
HOLD = ('HEEL-BALLAST-01 — OPERATIONAL STATUS: HOLD. Stop cargo work before every flagged lift. Chief officer must confirm '
        'a heeling-tank programme and safe intermediate conditions before release. '
        'Transfer quantities and timing are not calculated; this is an unresolved operational hold, '
        'not an executed ballast transfer.')
FIELDS = ['lift_order', 'source_move_index', 'box', 'from', 'to', 'kind', 'crane',
          'mass_t', 'pod', 'list_deg_est', 'list_over_1_deg', 'instruction']


def _csv(fields, rows, banner, *, estimate=False):
    stream = io.StringIO(newline='')
    stream.write('# ' + banner.replace('\n', ' ') + (' | ' + ESTIMATOR if estimate else '') + '\n')
    writer = csv.DictWriter(stream, fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def cargo_work(instance, plan, report, *, banner):
    """Return CSV files and JSON evidence; never change or silently reorder a plan.

    Native replay must succeed. The caller supplies its fresh full checker report;
    neither this estimate nor the operational hold extends that checker verdict.
    Original rows retain their 1-based source index across the split CSV files.
    """
    replay = apply_plan(instance, plan)
    if not replay.legal or replay.executed != len(plan.moves):
        raise ValueError('cargo-work move replay failed: ' + '; '.join(v.message for v in replay.violations))
    final_ship = departure_condition(instance, plan)
    initial = loading_condition(instance, instance.onboard)
    mass = float(initial.mass_kg)
    moment = mass * initial.tcg_m
    part = report.parts.get('stability')
    gm = part.metrics.get('gm_m') if part is not None else None
    if not isinstance(gm, (int, float)) or not math.isfinite(gm) or gm <= 0:
        gm = None
    cells = {c.id: c for c in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    covers = {c.id: c for c in instance.vessel.hatch_covers}
    cover_positions = dict(instance.onboard.covers_off)
    stack_covers = {cell: stack.hatch_cover_id for stack in instance.vessel.stacks
                    for cell in stack.cell_ids}
    cover_order = {cid: i for i, cid in enumerate(sorted(covers))}
    ncranes = instance.port.quay_crane.count if instance.port.quay_crane else 0
    quay, yard, flagged, unavailable = [], [], [], []
    peak = 0.0
    hold_added = False

    def transverse(slot):
        footprint = cells_of_slot(slot)
        return sum(cells[cid].tcg_m for cid in footprint) / len(footprint)

    for order, move in enumerate(plan.moves, 1):
        kind = move.kind
        cid = getattr(move, 'container_id', None)
        cover_id = getattr(move, 'cover_id', None)
        box = boxes.get(cid)
        source = getattr(move, 'src', '')
        target = getattr(move, 'dst', '')
        if kind == 'gate_in':
            source = 'gate'
        elif kind == 'gate_out':
            target = 'gate'
        move_mass = box.gross_mass_kg if box else covers[cover_id].mass_kg
        ship_slot = None
        if kind in {'load', 'restow_on'}:
            source = source or 'quay'
            mass += move_mass
            moment += move_mass * transverse(target)
            ship_slot = target
        elif kind in {'discharge', 'restow_off'}:
            target = target or 'quay'
            mass -= move_mass
            moment -= move_mass * transverse(source)
            ship_slot = source
        elif kind == 'restow_shift':
            moment += move_mass * (transverse(target) - transverse(source))
            ship_slot = target
        elif kind == 'hatch_cover_off':
            cover = covers[cover_id]
            source = cover_id
            mass -= move_mass
            moment -= move_mass * cover.tcg_m
            if target != 'quay':
                mass += move_mass
                moment += move_mass * covers[target].tcg_m
            cover_positions[cover_id] = target
        elif kind == 'hatch_cover_on':
            cover = covers[cover_id]
            source = cover_positions.pop(cover_id)
            target = cover_id
            if source != 'quay':
                mass -= move_mass
                moment -= move_mass * covers[source].tcg_m
            mass += move_mass
            moment += move_mass * cover.tcg_m
        if not math.isfinite(mass) or not math.isfinite(moment) or mass <= 0:
            raise ValueError('cargo-work mass moment is invalid at source move ' + str(order))
        estimate = math.degrees(math.atan(moment / (mass * gm))) if gm else None
        over = abs(estimate) > 1.0 if estimate is not None else None
        if estimate is not None:
            peak = max(peak, abs(estimate))
        cover = cover_id or (stack_covers.get(cells_of_slot(ship_slot)[0]) if ship_slot else None)
        crane = ('QC-' + str(1 + cover_order.get(cover, 0) % ncranes)) if ncranes else 'UNASSIGNED'
        if kind in {'relocate', 'gate_in', 'gate_out'}:
            crane = 'YARD-UNASSIGNED'
        row = dict(lift_order=order, source_move_index=order, box=cid or cover_id,
                   **{'from': source, 'to': target}, kind=kind, crane=crane,
                   mass_t=f'{move_mass / 1000:.6f}', pod=box.pod if box else '',
                   list_deg_est=f'{estimate:.9f}' if estimate is not None else '',
                   list_over_1_deg=('YES' if over else 'NO') if over is not None else 'ESTIMATE_UNAVAILABLE',
                   instruction='See HEEL-BALLAST-01; estimate is before mitigation.' if over else '')
        if kind in {'relocate', 'gate_in', 'gate_out'}:
            yard.append(row)
            continue
        if over:
            flagged.append(order)
        if estimate is None:
            unavailable.append(order)
        if (over or estimate is None) and not hold_added:
            quay.append(dict(kind='heel_ballast_hold', crane='CHIEF-OFFICER',
                             instruction=HOLD, **{'from': 'cargo work', 'to': 'hold before move ' + str(order)}))
            hold_added = True
        quay.append(row)

    tank_rows = []
    ballast_arrival = ballast_departure = 0.0
    for tank in sorted(instance.vessel.tanks, key=lambda t: t.id):
        arrival = instance.onboard.tank_masses_kg.get(tank.id, 0.0)
        departure = final_ship.tank_masses_kg.get(tank.id, 0.0)
        if tank.kind == 'ballast':
            ballast_arrival += arrival
            ballast_departure += departure
        tank_rows.append(dict(record_type='tank', tank=tank.id, kind=tank.kind,
                              arrival_t=f'{arrival / 1000:.6f}', departure_t=f'{departure / 1000:.6f}',
                              capacity_t=f'{tank.capacity_m3 * tank.content_density_kg_m3 / 1000:.6f}',
                              adjustable=str(tank.adjustable).lower(), instruction=''))
    unchanged = all(instance.onboard.tank_masses_kg.get(t.id, 0.0) ==
                    final_ship.tank_masses_kg.get(t.id, 0.0) for t in instance.vessel.tanks)
    ballast_line = (f'Ballast unchanged from arrival: {ballast_departure / 1000:,.0f} t '
                    '(rounded; exact tank inventory in ballast-plan.csv).' if unchanged else
                    f'Departure ballast: {ballast_departure / 1000:,.0f} t '
                    '(rounded; exact tank inventory in ballast-plan.csv); transfer sequence is not provided.')
    quay.append(dict(kind='ballast_condition', crane='CHIEF-OFFICER',
                     mass_t=f'{ballast_departure / 1000:.6f}', instruction=ballast_line))
    tank_rows.append(dict(record_type='ballast_summary', tank='ALL_BALLAST', kind='ballast',
                          arrival_t=f'{ballast_arrival / 1000:.6f}',
                          departure_t=f'{ballast_departure / 1000:.6f}', instruction=ballast_line))
    if hold_added:
        tank_rows.append(dict(record_type='unresolved_hold', tank='HEEL-BALLAST-01', instruction=HOLD))
    return {
        'files': {
            'crane-sequence.csv': _csv(FIELDS, quay, banner, estimate=True),
            'yard-sequence.csv': _csv(FIELDS, yard, banner, estimate=True),
            'ballast-plan.csv': _csv(['record_type', 'tank', 'kind', 'arrival_t', 'departure_t',
                                      'capacity_t', 'adjustable', 'instruction'], tank_rows, banner),
        },
        'evidence': {
            'estimator': ESTIMATOR, 'estimate_is_safety_verdict': False,
            'gm_m_source': 'checker-report.parts.stability.metrics.gm_m', 'departure_gm_m': gm,
            'tank_centres_source': 'stowage.checker.stability_loading.loading_condition (native tank tables)',
            'replay_legal': True, 'source_move_count': len(plan.moves),
            'quay_lifts': len(plan.moves) - len(yard), 'yard_lifts': len(yard),
            'list_threshold_deg': 1.0, 'max_abs_list_deg_est': peak if gm else None,
            'flagged_source_move_indices': flagged, 'unavailable_source_move_indices': unavailable,
            'heel_ballast_hold': HOLD if hold_added else None, 'cargo_work_stability_checked': False,
            'operational_status': 'HOLD — chief officer heeling-tank programme required' if hold_added else
                                  'No estimated 1-degree trigger; cargo-work stability remains unchecked',
            'plan_reordered': False, 'checker_passed': report.passed,
            'crane_allocation': 'Suggested hatch-based allocation; source order is serial. No simultaneous-crane schedule checked.',
            'ballast_line': ballast_line, 'arrival_ballast_t': ballast_arrival / 1000,
            'departure_ballast_t': ballast_departure / 1000, 'ballast_unchanged': unchanged,
        },
    }
