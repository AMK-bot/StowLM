"""Flat synthetic departure cargo declarations; unknown DG fields stay unknown."""
from __future__ import annotations

import csv
import io
from collections import defaultdict

from stowage.conventions import cells_of_slot, parse_slot
from stowage.moves import project_future_moves
from stowage.rules.imdg.dgl import lookup_dgl

from .scope import departure_state


def _csv(rows, fields, banner):
    output = io.StringIO(newline='')
    output.write('# ' + banner.replace('\n', ' ') + '\n')
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def restow_rows(instance, plan, ship=None):
    """Name native fixed-coordinate projection blockers, cross-checking native counts.

    This is a discharge-access projection, not an executable future operation plan.
    New freeze-gated crossing rules must not silently produce unnamed blockers.
    """
    ship = ship or departure_state(instance, plan)
    boxes = {box.id: box for box in instance.containers}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    covers = {cover.id: cover for cover in instance.vessel.hatch_covers}
    placements = {p.container_id: p.slot for p in ship.placements}
    active = set(placements)
    result = [dict(box=move.container_id, call_index=plan.call_index, pod=boxes[move.container_id].pod,
                   slot=getattr(move, 'src', getattr(move, 'dst', '')), kind='executed restow')
              for move in plan.moves if move.kind in ('restow_off', 'restow_shift')]
    native = {row.call_index: row for row in project_future_moves(instance, ship, from_call=plan.call_index)}
    calls = instance.rotation.calls
    first_call = {}
    for index in range(plan.call_index + 1, len(calls)):
        first_call.setdefault(calls[index].locode, index)
    membership = {cid: {cells[cell].stack_id for cell in cells_of_slot(slot)} for cid, slot in placements.items()}
    for index in range(plan.call_index + 1, len(calls)):
        targets = {cid for cid in active if first_call.get(boxes[cid].pod) == index}
        opened = {stacks[sid].hatch_cover_id for cid in targets for sid in membership[cid] if not stacks[sid].on_deck}
        required = set(targets)
        deck_stacks = {sid for cover in opened for sid in covers[cover].deck_stack_ids}
        required.update(cid for cid in active if membership[cid] & deck_stacks)
        above = defaultdict(set)
        for sid in stacks:
            ordered = sorted((parse_slot(placements[cid]).tier, cid) for cid in active if sid in membership[cid])
            for (_, lower), (_, upper) in zip(ordered, ordered[1:]):
                above[lower].add(upper)
        pending = list(required)
        while pending:
            for cid in above[pending.pop()]:
                if cid not in required:
                    required.add(cid)
                    pending.append(cid)
        blockers = required - targets
        if len(blockers) != native[index].restowed_boxes:
            raise ValueError('Restow naming differs from native projection; cannot publish an incomplete list')
        result.extend(dict(box=cid, call_index=index, pod=boxes[cid].pod, slot=placements[cid],
                           kind='projected fixed-coordinate restow; not an executable instruction') for cid in sorted(blockers))
        active.difference_update(targets)
    return result


def _dg(entry):
    try:
        source = lookup_dgl(int(entry.un_number), entry.packing_group)
        psn = source.proper_shipping_name
        source_id = source.id
        segregation = sorted(set(source.segregation_codes) | set(entry.segregation_codes))
    except (KeyError, ValueError):
        psn, source_id, segregation = 'not supplied / not in curated DGL', '', entry.segregation_codes
    return dict(un_number=entry.un_number, imdg_class=entry.imdg_class, psn=psn,
                packing_group=entry.packing_group or 'not assigned', ems='not supplied in source; obtain DG declaration',
                segregation_codes=(';'.join(segregation) if segregation else
                                   'No additional segregation codes supplied; class-based model segregation applies'),
                dgl_row=source_id)


FIELDS = ('record_kind', 'box', 'slot', 'length_ft', 'mass_t', 'pol', 'pod', 'dg_un', 'dg_class',
          'dg_psn', 'dg_pg', 'dg_ems', 'dg_segregation', 'dg_dgl_row', 'reefer', 'reefer_setpoint_c',
          'oog', 'oog_over_height_m', 'oog_over_width_port_m', 'oog_over_width_starboard_m',
          'oog_over_length_fore_m', 'oog_over_length_aft_m', 'restow_calls', 'remarks')


def manifest_rows(instance, plan, report=None):
    ship = departure_state(instance, plan)
    boxes = {box.id: box for box in instance.containers}
    restows = restow_rows(instance, plan, ship)
    by_box = defaultdict(list)
    for row in restows:
        by_box[row['box']].append(str(row['call_index']))
    rows = []
    for placement in sorted(ship.placements, key=lambda p: p.slot):
        box = boxes[placement.container_id]
        dg = [_dg(entry) for entry in ([box.dg] + box.dg.additional if box.dg else [])]
        row = dict(record_kind='planned section cargo', box=box.id, slot=placement.slot, length_ft=box.length_ft,
                   mass_t=box.gross_mass_kg / 1000, pol=box.pol, pod=box.pod, reefer=box.reefer,
                   reefer_setpoint_c=box.reefer_setpoint_c if box.reefer else '', oog=box.oog is not None,
                   restow_calls=';'.join(by_box[box.id]), remarks='Synthetic booking and stow; DG declaration fields may be incomplete')
        for dest, source in [('dg_un', 'un_number'), ('dg_class', 'imdg_class'), ('dg_psn', 'psn'),
                             ('dg_pg', 'packing_group'), ('dg_ems', 'ems'), ('dg_segregation', 'segregation_codes'),
                             ('dg_dgl_row', 'dgl_row')]:
            row[dest] = ' | '.join(str(entry[source]) for entry in dg)
        for key in ('over_height_m', 'over_width_port_m', 'over_width_starboard_m', 'over_length_fore_m', 'over_length_aft_m'):
            row['oog_' + key] = getattr(box.oog, key) if box.oog else ''
        rows.append(row)
    for index, load in enumerate(instance.vessel.fixed_loads):
        if load.kind != 'cargo':
            continue
        if load.cargo_boxes is None:
            rows.append(dict(record_kind='fixed synthetic background aggregate; individual boxes unavailable',
                             box=f'BACKGROUND-AGGREGATE-{index:03d}', mass_t=load.mass_kg / 1000,
                             remarks='Aggregate only; box count, slot, booking and cargo declarations unavailable'))
        for ordinal, box in enumerate(load.cargo_boxes or []):
            ids = sorted(box.cell_ids)
            first = parse_slot(ids[0])
            bay = first.bay if box.length_ft == 20 else first.bay + 1
            rows.append(dict(record_kind='fixed synthetic background cargo', box=f'BACKGROUND-{index:03d}-{ordinal:03d}',
                             slot=f'{bay:03d}{first.row:02d}{first.tier:02d}', length_ft=box.length_ft,
                             mass_t=box.gross_mass_kg / 1000, remarks='Synthetic geometry identifier; booking, POD, DG, reefer and OOG declarations not supplied'))
    return rows


def render_manifest_csv(instance, plan, report=None, *, banner):
    return _csv(manifest_rows(instance, plan, report), FIELDS, banner)


def render_weight_by_port_csv(scope, *, banner):
    rows = [dict(pod=row['pod'], boxes=row['boxes'], mass_t=row['mass_t'], scope='planned section') for row in scope['weight_by_port']]
    rows.append(dict(pod='not declared', boxes=scope['background_boxes'], mass_t=scope['background_mass_t'], scope='fixed synthetic background'))
    return _csv(rows, ('pod', 'boxes', 'mass_t', 'scope'), banner)


def render_tank_csv(scope, *, banner):
    return _csv(scope['tanks'], ('tank', 'kind', 'adjustable', 'arrival_mass_kg', 'departure_mass_kg', 'capacity_kg'), banner)


def render_restow_csv(instance, plan, *, banner):
    return _csv(restow_rows(instance, plan), ('box', 'call_index', 'pod', 'slot', 'kind'), banner)
