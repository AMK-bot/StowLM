"""Optimistic completion capacity, independent of today's legal slot set.

For any selected pool of boxes, every real completion injects its one/two
20-foot cell units into that pool's union of future footprints. A deficit in
an *upper bound* on those cells proves failure (Hall's necessary matching
condition; https://doi.org/10.1112/jlms/s1-10.37.26). Individual height domains
include unsupported upper tiers: missing supports use the shortest remaining
box, including boxes outside the tested pool. Mass, DG, weight order, OOG,
pair alignment and instructions are deliberately relaxed, never guessed.

Future hold access may undo deck placements. In that case ALL current deck
cargo is optimistically removed and added once to future demand. Holds stay
fixed only under the native Place/not-aboard contract with no discharges.
Unknown contracts and unsupported scope fail open. These are necessary
relaxations, never certificates that a completion or ballast solution exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from stowage.conventions import cells_of_slot, format_slot, parse_slot
from stowage.rules.catalogue import rule_freeze_v1_enabled


@dataclass(frozen=True)
class LookaheadConfig:
    # APPROXIMATION: bounded tail/action/pool work leaves untested states and
    # actions admitted. These caps limit coverage, never relax a rejection.
    max_remaining_boxes: int = 12
    max_action_probes_per_state: int = 128
    max_relaxed_boxes: int = 128
    max_pools: int = 16
    support_conditioned: bool = True

    def __post_init__(self):
        if min(self.max_remaining_boxes, self.max_action_probes_per_state,
               self.max_relaxed_boxes, self.max_pools) < 0:
            raise ValueError('lookahead work caps must be nonnegative')


@dataclass(frozen=True)
class BoundResult:
    status: str  # reject, admit (relaxation only), unknown
    reason: str | None = None
    certificate: dict = field(default_factory=dict)


class OptimisticCapacity:
    """Immutable geometry shared by copies; no engine-private state is read."""

    def __init__(self, instance, config=None, *, visibility_caps=None):
        from stowage.feasibility.visibility_bounds import compute_visibility_caps
        self.instance, self.config = instance, config or LookaheadConfig()
        self.boxes = {box.id:box for box in instance.containers}
        self.stacks = tuple(sorted(instance.vessel.stacks, key=lambda stack:stack.id))
        self.stack_index = {stack.id:i for i,stack in enumerate(self.stacks)}
        self.cells = tuple(sorted(instance.vessel.cells, key=lambda cell:cell.id))
        self.cell_index = {cell.id:i for i,cell in enumerate(self.cells)}
        self.cell_stack = np.array([self.stack_index[cell.stack_id] for cell in self.cells], dtype=int)
        self.cell_position = {cell:j for stack in self.stacks for j,cell in enumerate(stack.cell_ids)}
        caps = visibility_caps if visibility_caps is not None else compute_visibility_caps(instance).top_by_stack
        self.roof_room = np.array([min(stack.max_height_m,
            caps.get(stack.id, math.inf)-stack.base_z_m) for stack in self.stacks])
        slots = set(self.cell_index)
        for cell in self.cells:
            address = parse_slot(cell.id)
            candidate = format_slot(address.bay+1, address.row, address.tier)
            if all(part in self.cell_index for part in cells_of_slot(candidate)):
                slots.add(candidate)
        self.slots = tuple(sorted(slots))
        self.parts = [cells_of_slot(slot) for slot in self.slots]
        self.foot = np.full((len(self.slots),2), -1, dtype=int)
        self.js = np.full_like(self.foot,-1)
        self.ks = np.zeros_like(self.foot)
        self.deck = np.zeros(len(self.slots),dtype=bool)
        self.compat = {length:np.zeros(len(self.slots),dtype=bool) for length in (20,40,45)}
        self.plug = np.zeros(len(self.slots),dtype=bool)
        for si, parts in enumerate(self.parts):
            for side, part in enumerate(parts):
                ci = self.cell_index[part]
                self.foot[si,side] = ci
                self.js[si,side] = self.cell_stack[ci]
                self.ks[si,side] = self.cell_position[part]
            physical = [self.cells[self.cell_index[part]] for part in parts]
            self.deck[si] = parse_slot(self.slots[si]).on_deck
            self.plug[si] = any(cell.reefer_plug for cell in physical)
            for length in (20,40,45):
                self.compat[length][si] = (len(parts) == (1 if length == 20 else 2)
                    and all(getattr(cell,f'accepts_{length}') for cell in physical))
        # These domains depend only on permanent physical facts. In particular
        # DG empty-ship static tables can omit future supported destinations.
        self.geometry = {}
        for box in self.boxes.values():
            domain = self.compat.get(box.length_ft, np.zeros(len(self.slots),dtype=bool)).copy()
            if box.reefer:
                domain &= self.plug
            if 'under_deck' in box.special_stowage:
                domain &= ~self.deck
            if 'on_deck' in box.special_stowage:
                domain &= self.deck
            self.geometry[box.id] = domain

    def remaining(self, ex):
        return sorted({cid for cid in self.instance.load_list
                       if ex.where.get(cid,(None,))[0] != 'ship'} | set(ex.buffer))

    def check(self, ex, *, native_contract=False):
        """Test only a certified native restricted executor's current state."""
        if not native_contract:
            return BoundResult('unknown','unsupported_executor')
        if (rule_freeze_v1_enabled() or self.instance.stage != 'A'
                or self.instance.onboard.placements or self.instance.plan_calls):
            return BoundResult('unknown','unsupported_scope')
        if any(ex.where.get(cid,(None,))[0] == 'ship' for cid in self.instance.discharge_list):
            return BoundResult('unknown','pending_discharge')
        remaining = self.remaining(ex)
        if len(remaining) > self.config.max_remaining_boxes:
            return BoundResult('unknown','tail_work_cap')
        if not remaining:
            return BoundResult('admit',certificate={'demand_units':0})
        try:
            return self._check(ex, remaining)
        except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as error:
            return BoundResult('unknown',f'calculation:{type(error).__name__}')

    def _check(self, ex, remaining):
        # Include every free hold footprint, even unsupported upper tiers. Fixed
        # hold cargo cannot leave under this guarded executor contract, so an
        # occupied hold footprint cannot become a future hatch access.
        # Resetting more deck than actually recoverable enlarges the completion
        # set. Removed cargo is NOT lost: every ID is unioned into demand once.
        all_occupied = {part for _,(where,slot) in ex.where.items() if where == 'ship'
                        for part in cells_of_slot(slot)}
        free_hold = np.array([not any(part in all_occupied for part in parts)
                              for parts in self.parts]) & ~self.deck
        future_hold = any(np.any(self.geometry[cid] & free_hold) for cid in remaining)
        released = {cid for cid,(where,slot) in ex.where.items()
                    if future_hold and where == 'ship' and parse_slot(slot).on_deck}
        remaining = sorted(set(remaining)|released)
        if len(remaining) > self.config.max_relaxed_boxes:
            return BoundResult('unknown','relaxed_box_work_cap')
        boxes = [self.boxes[cid] for cid in remaining]
        heights = np.array([box.height_m for box in boxes])
        if not len(boxes) or not np.isfinite(heights).all() or np.any(heights <= 0.):
            return BoundResult('unknown','height_unavailable')
        hmin = float(heights.min())
        fill = np.zeros(len(self.stacks),dtype=int)
        used = np.zeros(len(self.stacks))
        occupied = np.zeros(len(self.cells),dtype=bool)
        for cid,(where,slot) in ex.where.items():
            if where != 'ship' or cid in released:
                continue
            for part in cells_of_slot(slot):
                ci = self.cell_index[part]
                j = self.cell_stack[ci]
                occupied[ci] = True
                fill[j] += 1
                # Ignoring overheight and synchronizing long-slot roofs can
                # only LOWER this necessary roof estimate, hence is optimistic.
                used[j] += self.boxes[cid].height_m
        domain = np.array([self.geometry[cid] for cid in remaining])
        for side in (0,1):
            valid = self.foot[:,side] >= 0
            ci = np.maximum(self.foot[:,side],0)
            js = np.maximum(self.js[:,side],0)
            missing = np.maximum(self.ks[:,side]-fill[js],0)
            room = self.roof_room[js]-used[js]-missing*hmin
            allowed = (~occupied[ci])[None,:] & (heights[:,None] <= room[None,:]+1e-8)
            domain &= allowed | (~valid)[None,:]
        units = np.array([1 if box.length_ft == 20 else 2 for box in boxes])
        base = dict(demand_units=int(units.sum()), relaxed_boxes=len(boxes),
                    reset_deck_boxes=len(released), min_support_height_m=hmin)
        empty = np.flatnonzero(~domain.any(axis=1))
        if len(empty):
            return BoundResult('reject','future_domain_empty',dict(base,box=remaining[int(empty[0])]))
        pools = [('all',np.ones(len(boxes),dtype=bool))]
        for length in (20,40,45):
            pools.append((f'length_{length}',np.array([box.length_ft == length for box in boxes])))
        pools.append(('reefer',np.array([box.reefer for box in boxes])))
        for height in sorted(set(heights)):
            tall = heights >= height-1e-9
            pools.append((f'height_ge_{height:g}',tall))
            pools.append((f'long_height_ge_{height:g}',tall & (units == 2)))
        seen = set()
        for label, selected in pools[:self.config.max_pools]:
            identity = selected.tobytes()
            if identity in seen or not selected.any():
                continue
            seen.add(identity)
            slots = domain[selected].any(axis=0)
            cells = np.unique(self.foot[slots])
            cells = cells[cells >= 0]
            counts = np.bincount(self.cell_stack[cells],minlength=len(self.stacks))
            selected_height = float(heights[selected].min())
            room = np.maximum(self.roof_room-used,0.)
            height_cap = np.floor(room/selected_height+1e-8)
            capacity = int(np.minimum(counts,height_cap).sum())
            demand = int(units[selected].sum())
            if demand > capacity:
                return BoundResult('reject','pool_cell_height_deficit',dict(base,pool=label,
                    pool_units=demand,pool_capacity_units=capacity,pool_min_height_m=selected_height))
            volume = float(np.sum(units[selected]*heights[selected]))
            available = float(room[counts > 0].sum())
            if volume > available+1e-7:
                return BoundResult('reject','pool_height_volume_deficit',dict(base,pool=label,
                    required_column_height_m=volume,available_column_height_m=available))
        return BoundResult('admit',certificate=base)
