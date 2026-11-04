"""Opt-in joint fixed-prefix and target-support completion relaxations.

Subclass preserves height-only setup/guards, but copies its state calculation
locally so dynamic eligibility NEVER feeds the earlier deck-recovery decision.
Original broad immutable geometry determines recourse first. Future domains
then add only existing legacy constraints from unreleased fixed cargo.

Policy logic matches the independently audited experiment prototype. Broad
recourse comes first; unknown policy data widens eligibility. The default
dispatcher explicitly disables long matching. See experiments/mvp/lookahead/
prototype/PROPOSAL.md and SUPPORT_PROPOSAL.md for proof and counterexamples.
"""
from __future__ import annotations

import math

import numpy as np

from llm.lookahead.bounds import BoundResult, OptimisticCapacity
from stowage.conventions import cells_of_slot, parse_slot


def _positive(value):
    return (not isinstance(value,bool) and isinstance(value,(int,float))
            and math.isfinite(value) and value > 0.)


class JointCapacity(OptimisticCapacity):
    """E1/E2/size/mass + optimistic height-domain intersection."""

    def __init__(self,*args,long_matching=True,**kwargs):
        super().__init__(*args,**kwargs)
        from stowage.feasibility.tables import class_params,class_rank
        from stowage.checker.rules import _parameter
        self.long_matching = long_matching
        self.parameter_notes = []
        self.e2 = None
        self.rank = {}
        try:
            parameters = class_params(self.instance)
            if parameters['valid']:
                self.e2 = parameters
                self.rank = {cid:class_rank(parameters,box.gross_mass_kg)
                    for cid,box in self.boxes.items() if _positive(getattr(box,'gross_mass_kg',None))}
        except (ValueError,KeyError,TypeError,AttributeError):
            self.parameter_notes.append('E2 unavailable: no rank pruning')
        self.e1 = None
        try:
            threshold = _parameter(self.instance,'expert.e1.heavy_bottom','heavy_threshold')
            if _positive(threshold):
                self.e1 = float(threshold)
        except (ValueError,KeyError,TypeError,AttributeError):
            self.parameter_notes.append('E1 unavailable: no threshold pruning')
        self.mass_limit = []
        for stack in self.stacks:
            general = getattr(stack,'max_mass_kg',None)
            limits = [general]
            for name in ('max_mass_20_kg','max_mass_40_kg'):
                value = getattr(stack,name,None)
                limits.append(general if value is None else value)
            self.mass_limit.append(max(limits) if all(_positive(value) for value in limits) else math.inf)
        self.mass_limit = np.array(self.mass_limit,dtype=float)
        self.long_edges = []
        self.path_geometry = True
        for si,parts in enumerate(self.parts):
            if len(parts) != 2:
                continue
            a,b = map(parse_slot,parts)
            self.path_geometry &= a.row == b.row and a.tier == b.tier and abs(a.bay-b.bay) == 2
            self.long_edges.append(((a.row,a.tier,min(a.bay,b.bay)),si))
        self.long_edges.sort()

    def _long_upper_bound(self,allowed):
        if not self.path_geometry:
            return sum(bool(allowed[si]) for _,si in self.long_edges)
        used,count = set(),0
        for _,si in self.long_edges:
            if allowed[si] and not used.intersection(self.parts[si]):
                used.update(self.parts[si])
                count += 1
        return count

    def _support_domain(self,domain,remaining,boxes,heights,units,fill,used):
        """Separate experimental extension; fixed-only variant does nothing."""
        return domain,{}

    def _check(self,ex,remaining):
        # This guard is intentionally identical to frozen v1. NEVER use rank,
        # mass, length-mix or current legal-slot tests to shrink future_hold:
        # a future support can repair those restrictions and permit deck undo.
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
        J = len(self.stacks)
        fill = np.zeros(J,dtype=int)
        used = np.zeros(J)
        occupied = np.zeros(len(self.cells),dtype=bool)
        fixed_long = np.zeros(J,dtype=bool)
        fixed_nonheavy = np.zeros(J,dtype=bool)
        fixed_rank = np.full(J,np.inf)
        fixed_mass = np.zeros(J)
        for cid,(where,slot) in ex.where.items():
            if where != 'ship' or cid in released:
                continue
            fixed = self.boxes[cid]
            parts = cells_of_slot(slot)
            gross = getattr(fixed,'gross_mass_kg',None)
            for part in parts:
                ci = self.cell_index[part]
                j = self.cell_stack[ci]
                occupied[ci] = True
                fill[j] += 1
                used[j] += fixed.height_m  # ignore OOG/synchronized roof: optimistic
                fixed_long[j] |= fixed.length_ft != 20
                if cid in self.rank:
                    fixed_rank[j] = min(fixed_rank[j],self.rank[cid])
                if _positive(gross):
                    fixed_mass[j] += gross/len(parts)
                    if self.e1 is not None:
                        fixed_nonheavy[j] |= gross < self.e1
        domain = np.array([self.geometry[cid] for cid in remaining])
        units = np.array([1 if box.length_ft == 20 else 2 for box in boxes])
        ranks = np.array([self.rank.get(cid,-np.inf) for cid in remaining])
        gross = np.array([getattr(box,'gross_mass_kg',np.nan) for box in boxes],dtype=float)
        valid_mass = np.isfinite(gross) & (gross > 0.)
        before_joint = int(domain.sum())
        for side in (0,1):
            valid = self.foot[:,side] >= 0
            ci = np.maximum(self.foot[:,side],0)
            js = np.maximum(self.js[:,side],0)
            missing = np.maximum(self.ks[:,side]-fill[js],0)
            room = self.roof_room[js]-used[js]-missing*hmin
            allowed = (~occupied[ci])[None,:] & (heights[:,None] <= room[None,:]+1e-8)
            # Additional constraints apply only AFTER the broad recourse guard.
            allowed &= ~((units[:,None] == 1) & fixed_long[js][None,:])
            if self.e2 is not None:
                allowed &= ranks[:,None] <= fixed_rank[js][None,:]+self.e2['tol']
            if self.e1 is not None:
                allowed &= ~((valid_mass & (gross >= self.e1))[:,None]
                             & fixed_nonheavy[js][None,:])
            share = np.divide(gross,units)
            mass_room = self.mass_limit[js]-fixed_mass[js]
            allowed &= ((~valid_mass)[:,None]
                        | (share[:,None] <= mass_room[None,:]+1e-6))
            domain &= allowed | (~valid)[None,:]
        domain,support_details = self._support_domain(domain,remaining,boxes,heights,units,fill,used)
        base = dict(prototype=type(self).__name__,demand_units=int(units.sum()),
            relaxed_boxes=len(boxes),reset_deck_boxes=len(released),
            min_support_height_m=hmin,geometry_domain_entries_removed=before_joint-int(domain.sum()),
            **support_details)
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
        for label,selected in pools[:self.config.max_pools]:
            identity = selected.tobytes()
            if identity in seen or not selected.any():
                continue
            seen.add(identity)
            slots = domain[selected].any(axis=0)
            cells = np.unique(self.foot[slots])
            cells = cells[cells >= 0]
            counts = np.bincount(self.cell_stack[cells],minlength=J)
            selected_height = float(heights[selected].min())
            room = np.maximum(self.roof_room-used,0.)
            capacity = int(np.minimum(counts,np.floor(room/selected_height+1e-8)).sum())
            demand = int(units[selected].sum())
            if demand > capacity:
                return BoundResult('reject','pool_cell_height_deficit',dict(base,pool=label,
                    pool_units=demand,pool_capacity_units=capacity,pool_min_height_m=selected_height))
            if self.long_matching and np.all(units[selected] == 2):
                long_capacity = self._long_upper_bound(slots)
                if int(selected.sum()) > long_capacity:
                    return BoundResult('reject','pool_disjoint_long_deficit',dict(base,pool=label,
                        pool_boxes=int(selected.sum()),pool_long_capacity=long_capacity))
            volume = float(np.sum(units[selected]*heights[selected]))
            available = float(room[counts > 0].sum())
            if volume > available+1e-7:
                return BoundResult('reject','pool_height_volume_deficit',dict(base,pool=label,
                    required_column_height_m=volume,available_column_height_m=available))
        return BoundResult('admit',certificate=base)


class SupportCapacity(JointCapacity):
    """Target-conditional future support count/height extension.

    Broad recourse selection and fixed eligibility run first in JointCapacity.
    No support test may alter that earlier deck-reset decision. Each target
    excludes itself; unknown ranks/masses remain eligible lower supports.
    """

    def _support_domain(self,domain,remaining,boxes,heights,units,fill,used):
        before = int(domain.sum())
        known_rank = np.array([cid in self.rank for cid in remaining])
        rank = np.array([self.rank.get(cid,0) for cid in remaining])
        gross = np.array([getattr(box,'gross_mass_kg',np.nan) for box in boxes],dtype=float)
        known_mass = np.isfinite(gross) & (gross > 0.)
        missing = []
        for side in (0,1):
            valid = self.foot[:,side] >= 0
            js = np.maximum(self.js[:,side],0)
            missing.append(np.where(valid,np.maximum(self.ks[:,side]-fill[js],0),0))
        needed = missing[0]+missing[1]
        examples = []
        for bi,target in enumerate(boxes):
            eligible = np.ones(len(boxes),dtype=bool)
            eligible[bi] = False  # a target cannot be its own lower support
            if self.e2 is not None and known_rank[bi]:
                eligible &= (~known_rank) | (rank >= rank[bi]-self.e2['tol'])
            if self.e1 is not None and known_mass[bi] and gross[bi] >= self.e1:
                eligible &= (~known_mass) | (gross >= self.e1)
            if target.length_ft == 20:
                eligible &= units == 1  # no 20-foot target above a long box
            supply = int(units[eligible].sum())
            rejected = domain[bi] & (needed > supply)
            if np.any(rejected) and len(examples) < 3:
                si = int(np.flatnonzero(rejected)[0])
                examples.append(dict(box=remaining[bi],slot=self.slots[si],
                    reason='eligible_other_support_units',needed_units=int(needed[si]),
                    available_units=supply))
            domain[bi] &= needed <= supply
            if not eligible.any():
                continue  # only bottom-tier footprints with zero missing survive
            # All heights have already passed the base finite-positive guard.
            # Unknown eligibility widened the set above; its minimum therefore
            # remains an optimistic lower bound on actual support height.
            support_minimum = float(heights[eligible].min())
            for side in (0,1):
                js = np.maximum(self.js[:,side],0)
                room = self.roof_room[js]-used[js]-missing[side]*support_minimum
                enough = (missing[side] == 0) | (heights[bi] <= room+1e-8)
                rejected = domain[bi] & ~enough
                if np.any(rejected) and len(examples) < 3:
                    si = int(np.flatnonzero(rejected)[0])
                    examples.append(dict(box=remaining[bi],slot=self.slots[si],
                        reason='eligible_other_support_height',
                        support_min_height_m=support_minimum,missing_layers=int(missing[side][si])))
                domain[bi] &= enough
        return domain,dict(support_conditioned=True,
            support_domain_entries_removed=before-int(domain.sum()),support_examples=examples)
