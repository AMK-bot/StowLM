"""Indexed, transactional crane replay used by the public move engine.

A 40/45 foot lift reserves both 20 foot cells: consequently both supporting columns
must be reachable and have matching free tiers. Yard footprints follow the single
length stack convention in ``model.yard``. Source: project architecture conventions
and the physical-access rules in ``rules/catalogue.toml`` (expert provisional rules).
Weight, reefer, stowage DG and vessel-height constraints belong to the departure
checker; this module checks crane access and equipment tier limits, plus activated
port DG handling/direct-delivery rules and C6 crossing paths.
"""
from __future__ import annotations

import re

from stowage.conventions import cells_of_slot, parse_slot, parse_yard
from stowage.model import Instance, Move, Violation
from stowage.model.ship import Placement, ShipState, WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves._paths import crossing_paths
from stowage.moves._port_rules import port_move_violation
from stowage.ports import effective_yard_tiers, yard_blocking_tiers
from stowage.rules.catalogue import rule_freeze_v1_enabled


class Replay:
    """Private mutable indices; each move validates completely before committing."""

    def __init__(self, instance: Instance, state: WorldState, *, observe_crossing: bool = False):
        self.instance = instance
        self.call_index = state.call_index
        self.yard_id = state.yard.yard_id
        self.time_s = state.yard.time_s
        self.vessel_id = state.ship.vessel_id
        self.tanks = dict(state.ship.tank_masses_kg)
        self.boxes = {box.id: box for box in instance.containers}
        self.cells = {cell.id: cell for cell in instance.vessel.cells}
        self.stacks = {stack.id: stack for stack in instance.vessel.stacks}
        self.covers = {cover.id: cover for cover in instance.vessel.hatch_covers}
        self.crossing_enabled = rule_freeze_v1_enabled()
        self.crane_height = (instance.port.max_crane_height_tiers
                             if self.crossing_enabled else None)
        self.crossing_stacks = {**self.stacks, **{stack.id: stack for stack in instance.vessel.fixed_load_stacks}}
        self.fixed_occupied = {cell for load in instance.vessel.fixed_loads for box in load.cargo_boxes or []
                               for cell in box.cell_ids}
        self.blocks = {block.id: block for block in instance.port.yard.blocks}
        self.yard_limits = {
            block.id: effective_yard_tiers(instance.port, block)
            for block in instance.port.yard.blocks
        }
        self.yard_blocking_limits = {
            block.id: yard_blocking_tiers(instance.port, block)
            for block in instance.port.yard.blocks
        }
        self.cell_positions = {
            cid: (stack, index) for stack in instance.vessel.stacks
            for index, cid in enumerate(stack.cell_ids)
        }
        self.yard = {stack.key: list(stack.boxes) for stack in state.yard.stacks}
        self.yard_lengths = {stack.key: stack.length_ft for stack in state.yard.stacks}
        self.footprints = {}
        self.locations = {}
        for stack in state.yard.stacks:
            addr = parse_yard(stack.key + '-01')
            for offset in range(1 if stack.length_ft == 20 else 2):
                self.footprints[(addr.block, addr.bay + offset, addr.row)] = stack.key
            for index, cid in enumerate(stack.boxes, 1):
                self.locations[cid] = ('yard', f'{stack.key}-{index:02d}')
        self.ship = {p.container_id: p.slot for p in state.ship.placements}
        self.occupied = {}
        for cid, slot in self.ship.items():
            self.locations[cid] = ('ship', slot)
            for cell in cells_of_slot(slot):
                self.occupied[cell] = cid
        self.buffer = dict.fromkeys(state.yard.quay_buffer)
        for cid in self.buffer:
            self.locations[cid] = ('quay', 'quay')
        self.covers_off = dict(state.ship.covers_off)
        self.load_list = set(instance.load_list)
        self.discharge_list = set(instance.discharge_list)
        self._move = None
        self._move_index = None
        self.crossing_observer = None
        if observe_crossing:
            from stowage.moves._observations import CrossingObserver
            self.crossing_observer = CrossingObserver(self)

    def fail(self, rule: str, message: str, *, measured=None, limit=None):
        from stowage.moves.engine import IllegalMoveError
        move = self._move
        cid = getattr(move, 'container_id', None)
        refs = [getattr(move, side, None) for side in ('src', 'dst')]
        raise IllegalMoveError(Violation(
            rule_id=rule, severity='hard', call_index=self.instance.call_index,
            move_index=self._move_index, containers=[cid] if cid else [],
            slots=[ref for ref in refs if isinstance(ref, str) and len(ref) == 7 and ref.isdigit()],
            yard_addresses=[ref for ref in refs if isinstance(ref, str) and
                            re.fullmatch(r'[A-Z0-9]{1,4}-\d{3}-\d{2}-\d{2}', ref)],
            message=message, measured=measured, limit=limit,
            unit='1' if measured is not None else None,
        ))

    def _yard_source(self, cid, src):
        if self.locations.get(cid) != ('yard', src):
            self.fail('expert.moves.top_access', 'The container is not at the stated yard source.')
        addr = parse_yard(src)
        key = src.rsplit('-', 1)[0]
        if self.yard[key][-1] != cid:
            self.fail('expert.moves.top_access', 'Only the top container of a yard stack can be lifted.')
        if addr.tier > self.yard_limits[addr.block]:
            self.fail('port.e3.yard_height', 'Yard equipment cannot reach the source tier.',
                      measured=addr.tier, limit=self.yard_limits[addr.block])
        return key

    def _yard_destination(self, cid, dst, removing=None):
        try:
            addr = parse_yard(dst)
        except ValueError:
            self.fail('expert.physical.size_fit', 'The yard destination address is invalid.')
        key = dst.rsplit('-', 1)[0]
        box = self.boxes[cid]
        block = self.blocks.get(addr.block)
        width = 1 if box.length_ft == 20 else 2
        if (block is None or addr.bay + width - 1 > block.n_bays or
                addr.row > block.n_rows or box.length_ft not in block.allowed_lengths_ft):
            self.fail('expert.physical.size_fit', 'The container does not fit the target yard block.')
        remaining = len(self.yard.get(key, ())) - int(key == removing)
        if remaining and self.yard_lengths[key] != box.length_ft:
            self.fail('expert.physical.size_fit', 'A yard stack holds only one container length class.')
        for offset in range(width):
            owner = self.footprints.get((addr.block, addr.bay + offset, addr.row))
            if owner is not None and owner != key:
                # A vacated one-box source no longer reserves its footprint.
                if owner != removing or len(self.yard[owner]) > 1:
                    self.fail('expert.physical.slot_occupancy', 'The target yard footprint overlaps another stack.')
        if addr.tier != remaining + 1:
            self.fail('expert.moves.bottom_up', 'The yard destination must be the next free tier.')
        if addr.tier > self.yard_limits[addr.block]:
            self.fail('port.e3.yard_height', 'The target yard tier exceeds the equipment limit.',
                      measured=addr.tier, limit=self.yard_limits[addr.block])
        return key

    def _ship_access(self, cell_id):
        stack, index = self.cell_positions[cell_id]
        cover = stack.hatch_cover_id
        if not stack.on_deck and cover not in self.covers_off:
            self.fail('expert.moves.hatch_clear', 'The hatch cover must be off before accessing the hold.')
        if stack.on_deck:
            if cover is not None and (cover in self.covers_off or cover in self.covers_off.values()):
                self.fail('expert.moves.hatch_clear', 'The deck stack has no available closed hatch cover.')
            limit = (self.crane_height if self.crane_height is not None
                     else self.instance.port.quay_crane.max_on_deck_tiers)
            if index + 1 > limit:
                self.fail('port.e3.quay_height', 'The quay crane cannot reach this deck tier.',
                          measured=index + 1, limit=limit)
        return stack, index

    def _ship_source(self, cid, src):
        if self.locations.get(cid) != ('ship', src):
            self.fail('expert.moves.top_access', 'The container is not at the stated ship source.')
        for cell_id in cells_of_slot(src):
            stack, index = self._ship_access(cell_id)
            if any(cell in self.occupied for cell in stack.cell_ids[index + 1:]):
                self.fail('expert.moves.top_access', 'Only the top container of every occupied ship stack can be lifted.')

    def _ship_destination(self, cid, dst, removing=None):
        box = self.boxes[cid]
        try:
            addr = parse_slot(dst)
            footprint = cells_of_slot(dst)
        except ValueError:
            self.fail('expert.physical.size_fit', 'The ship destination is not a valid slot.')
        if (box.length_ft == 20) != addr.is_20ft_bay:
            self.fail('expert.physical.size_fit', 'The slot bay does not match the container length.')
        for cell_id in footprint:
            cell = self.cells.get(cell_id)
            if cell is None or not getattr(cell, f'accepts_{box.length_ft}'):
                self.fail('expert.physical.size_fit', 'The target physical cells do not accept this container length.')
            stack, index = self._ship_access(cell_id)
            occupant = self.occupied.get(cell_id)
            if occupant is not None and occupant != removing:
                self.fail('expert.physical.slot_occupancy', 'A target physical cell is already occupied.')
            for below in stack.cell_ids[:index]:
                supporting = self.occupied.get(below)
                if supporting is None or supporting == removing:
                    self.fail('expert.moves.bottom_up', 'Ship stacks must fill from the lowest cell without gaps.')
            if index:
                support = self.occupied.get(stack.cell_ids[index - 1])
                if box.length_ft == 20 and self.boxes[support].length_ft != 20:
                    self.fail('expert.physical.no_20_on_40', 'A 20 foot container cannot rest on a 40/45 foot container.')
            if any(self.occupied.get(above) not in (None, removing) for above in stack.cell_ids[index + 1:]):
                self.fail('expert.moves.top_access', 'The target slot cannot be reached through cargo above it.')
        return footprint

    def _remove_yard(self, key):
        cid = self.yard[key].pop()
        del self.locations[cid]
        if not self.yard[key]:
            addr = parse_yard(key + '-01')
            for offset in range(1 if self.yard_lengths[key] == 20 else 2):
                del self.footprints[(addr.block, addr.bay + offset, addr.row)]
            del self.yard[key]
            del self.yard_lengths[key]

    def _add_yard(self, cid, key, dst):
        if key not in self.yard:
            self.yard[key] = []
            self.yard_lengths[key] = self.boxes[cid].length_ft
            addr = parse_yard(dst)
            for offset in range(1 if self.boxes[cid].length_ft == 20 else 2):
                self.footprints[(addr.block, addr.bay + offset, addr.row)] = key
        self.yard[key].append(cid)
        self.locations[cid] = ('yard', dst)

    def _remove_ship(self, cid):
        for cell in cells_of_slot(self.ship.pop(cid)):
            del self.occupied[cell]
        del self.locations[cid]

    def _add_ship(self, cid, dst, footprint):
        self.ship[cid] = dst
        for cell in footprint:
            self.occupied[cell] = cid
        self.locations[cid] = ('ship', dst)

    def _cover_clear(self, cover_id):
        if cover_id in self.covers_off.values():
            self.fail('expert.moves.hatch_clear', 'Another hatch cover is resting on this cover.')
        cover = self.covers[cover_id]
        if any(cell in self.occupied for sid in cover.deck_stack_ids for cell in self.stacks[sid].cell_ids):
            self.fail('expert.moves.hatch_clear', 'Every deck stack on the hatch cover must be clear.')
        if self.crane_height is not None and any(
                cell in self.fixed_occupied for fixed_cover in self.instance.vessel.fixed_load_hatch_covers
                if fixed_cover.id == cover_id for sid in fixed_cover.deck_stack_ids
                for cell in self.crossing_stacks[sid].cell_ids):
            self.fail('expert.moves.hatch_clear', 'Fixed deck cargo on the hatch cover cannot be lifted away.')

    def _crossing(self, move):
        """Screen the shared geometry against the pre-lift occupancy atomically."""
        if not self.crossing_enabled:
            return
        from stowage.moves.engine import IllegalMoveError

        try:
            path = (self.crossing_observer.path(self, move) if self.crossing_observer is not None
                    else crossing_paths(self.instance, move))
        except IllegalMoveError as error:
            # Geometry is shared and stateless; replay supplies its move index.
            raise IllegalMoveError(error.violation.model_copy(
                update={'move_index': self._move_index})) from error
        ship_blocker = yard_blocker = None
        for sid in path.ship_stack_ids:
            stack = self.crossing_stacks[sid]
            height = max((i + 1 for i, cell in enumerate(stack.cell_ids)
                          if cell in self.occupied or cell in self.fixed_occupied), default=0)
            if self.crane_height is not None and height >= self.crane_height and ship_blocker is None:
                ship_blocker = height
        for ground in path.yard_ground_slots:
            owner = self.footprints.get(ground)
            height = len(self.yard[owner]) if owner is not None else 0
            limit = self.yard_blocking_limits[ground[0]]
            if height >= limit and yard_blocker is None:
                yard_blocker = (height, limit)
        if self.crossing_observer is not None:
            self.crossing_observer.exposure(ship=ship_blocker is not None, yard=yard_blocker is not None)
        if ship_blocker is not None:
            self.fail('port.e3.quay_crossing', 'The quay lift crosses an on-deck stack at crane height.',
                      measured=ship_blocker, limit=self.crane_height)
        if yard_blocker is not None:
            self.fail('port.e3.yard_crossing', 'The yard lift crosses a stack at its effective yard limit.',
                      measured=yard_blocker[0], limit=yard_blocker[1])

    def move(self, move: Move, move_index: int | None = None):
        """Apply a single lift atomically, raising the public IllegalMoveError on rejection."""
        self._move, self._move_index = move, move_index
        if move.call_index != self.instance.call_index or self.call_index != self.instance.call_index:
            self.fail('expert.moves.top_access', 'The move and world must belong to the current port call.')
        kind = move.kind
        if kind in ('hatch_cover_off', 'hatch_cover_on'):
            cover = move.cover_id
            if cover not in self.covers:
                self.fail('expert.moves.hatch_clear', 'The stated hatch cover does not exist.')
            self._cover_clear(cover)
            if self.crane_height is not None and kind == 'hatch_cover_off' and move.dst != 'quay':
                self.fail('expert.moves.hatch_clear', 'Under C6 hatch covers land on the quay.')
            if (self.crane_height is not None and kind == 'hatch_cover_on'
                    and cover in self.covers_off and self.covers_off[cover] != 'quay'):
                self.fail('expert.moves.hatch_clear', 'Under C6 hatch covers return from the quay.')
            self._crossing(move)
            if kind == 'hatch_cover_off':
                if cover in self.covers_off:
                    self.fail('expert.moves.hatch_clear', 'The hatch cover is already off.')
                if move.dst != 'quay':
                    if move.dst not in self.covers or move.dst == cover or move.dst in self.covers_off:
                        self.fail('expert.moves.hatch_clear', 'A lifted cover must rest on the quay or another closed cover.')
                    self._cover_clear(move.dst)
                self.covers_off[cover] = move.dst
            else:
                if cover not in self.covers_off:
                    self.fail('expert.moves.hatch_clear', 'The hatch cover is already on.')
                del self.covers_off[cover]
            return
        cid = move.container_id
        if cid not in self.boxes:
            self.fail('expert.moves.top_access', 'The container is not registered in the instance.')
        if kind == 'load' and cid not in self.load_list:
            self.fail('expert.moves.top_access', 'A productive load must name a container on the load list.')
        # D11 fixes productive lifts by each call's lists: a repeated port code
        # does not make through cargo due at this call. Use counted restows.
        if kind == 'discharge' and cid not in self.discharge_list:
            self.fail('expert.moves.top_access', 'A productive discharge must name a container on the discharge list; use restow_off otherwise.')
        if kind == 'discharge' and self.boxes[cid].pod != self.instance.port.locode:
            self.fail('expert.moves.top_access', 'A productive discharge must have this port as its destination; use restow_off otherwise.')
        violation = port_move_violation(self.instance, move, move_index=move_index, box=self.boxes[cid])
        if violation is not None:
            from stowage.moves.engine import IllegalMoveError
            raise IllegalMoveError(violation)
        direct = rule_freeze_v1_enabled() and self.boxes[cid].direct_delivery
        source_key = None
        if kind == 'load' and direct and move.src == 'truck':
            if cid in self.locations:
                self.fail('expert.physical.slot_occupancy', 'The truck export is already located in the world.')
        elif kind in ('relocate', 'load', 'gate_out'):
            source_key = self._yard_source(cid, move.src)
        elif kind in ('discharge', 'restow_shift', 'restow_off'):
            self._ship_source(cid, move.src)
        elif kind == 'restow_on':
            if cid not in self.buffer:
                self.fail('expert.moves.top_access', 'Only a container in the quay restow buffer can be put back aboard.')
        elif kind == 'gate_in' and cid in self.locations:
            self.fail('expert.physical.slot_occupancy', 'The arriving container is already located in the world.')
        target_key = footprint = None
        if kind == 'discharge' and direct and move.dst == 'truck':
            pass  # One ship-to-truck lift; no yard placement or rehandle.
        elif kind in ('relocate', 'discharge', 'gate_in'):
            target_key = self._yard_destination(cid, move.dst, removing=source_key)
        elif kind in ('load', 'restow_shift', 'restow_on'):
            footprint = self._ship_destination(cid, move.dst, removing=cid if kind == 'restow_shift' else None)
        self._crossing(move)
        # Every possible rejection is above this boundary: commit only validated changes.
        if source_key is not None:
            self._remove_yard(source_key)
        elif kind in ('discharge', 'restow_shift', 'restow_off'):
            self._remove_ship(cid)
        elif kind == 'restow_on':
            del self.buffer[cid]
            del self.locations[cid]
        if target_key is not None:
            self._add_yard(cid, target_key, move.dst)
        elif footprint is not None:
            self._add_ship(cid, move.dst, footprint)
        elif kind == 'restow_off':
            self.buffer[cid] = None
            self.locations[cid] = ('quay', 'quay')

    def state(self) -> WorldState:
        """Return a fresh, independent model snapshot in deterministic canonical order."""
        return WorldState(
            call_index=self.call_index,
            yard=YardState(yard_id=self.yard_id, time_s=self.time_s,
                           stacks=[YardStack(key=key, length_ft=self.yard_lengths[key], boxes=list(boxes))
                                   for key, boxes in self.yard.items()], quay_buffer=list(self.buffer)),
            ship=ShipState(vessel_id=self.vessel_id,
                           placements=[Placement(slot=slot, container_id=cid) for cid, slot in self.ship.items()],
                           tank_masses_kg=dict(self.tanks), covers_off=dict(self.covers_off)),
        )
