# Copied from experiments/mvp/frontier/lab/c3/c3-layover/pages.py; engine integration changes are local.
"""Arrival-aware stack pages, extending the locally copied C2 layout model.

The onboard bottom prefix is fixed. Columns cover only new/quay boxes; their
slots, mass and height budgets are what remains above the prefix. Page LP
objectives are construction heuristics, never whole-map lower bounds.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import math
import sys

from stowage.conventions import cells_of_slot

from . import page_base as BASE

@dataclass(frozen=True)
class Page(BASE.Page):
    prefix: tuple = ()
    prefix_slots: tuple = ()
    prefix_height: float = 0.
    prefix_share: float = 0.


class Model(BASE.Model):
    def __init__(self, problem, **kwargs):
        self.arrival = {p.container_id: p.slot for p in problem.instance.onboard.placements}
        self.occupied = {cell: p.container_id for p in problem.instance.onboard.placements
                         for cell in cells_of_slot(p.slot)}
        self.arrival_hold_ports = defaultdict(set)
        self.arrival_deck = defaultdict(set)
        for box, slot in self.arrival.items():
            stack = problem.stacks[problem.cells[cells_of_slot(slot)[0]].stack_id]
            if stack.on_deck:
                self.arrival_deck[stack.hatch_cover_id].add(box)
            else:
                self.arrival_hold_ports[stack.hatch_cover_id].add(problem.pod(box))
        super().__init__(problem, **kwargs)
        T = self.T
        self.pod.update({T.box_index[b]: problem.pod(b) for b in self.arrival})
        self.heavy.update({T.box_index[b]: T.e1_threshold is not None and
                           T.mass[T.box_index[b]] >= T.e1_threshold for b in self.arrival})
        self.ports = sorted(set(self.pod.values()))
        # Type sharing must include the fixed prefix; otherwise one page's
        # dual price could be applied to a different arrival geometry.
        self.arrival_heel = sum(problem.boxes[b].gross_mass_kg/1000.*problem.cells_tcg(s)
                                for b, s in self.arrival.items())

    def _capped(self, raw):
        capped = super()._capped(raw)
        T = self.T
        prefix, prefix_slots = [], []
        free = []
        invalid = False
        for s in capped.slots:
            slot = T.slot_ids[s]
            occupants = {self.occupied[c] for c in cells_of_slot(slot) if c in self.occupied}
            if occupants:
                if free or len(occupants) != 1:
                    invalid = True
                    break
                box = next(iter(occupants))
                # Conservatively retain pure pages; mixed arrival bases need
                # their own securing model and are not a proof of infeasibility.
                if self.arrival[box] != slot:
                    invalid = True
                    break
                prefix.append(T.box_index[box]); prefix_slots.append(s)
            else:
                free.append(s)
        height = sum(float(T.height[b]) for b in prefix)
        mass = sum(float(T.share[b]) for b in prefix)
        if invalid or mass >= capped.limit_kg or height >= capped.hmax:
            free = []
        return Page(capped.kind, capped.stacks, tuple(free), max(1e-6, capped.limit_kg-mass),
                    max(0., capped.hmax-height), capped.deck, capped.cover,
                    tuple(prefix), tuple(prefix_slots), height, mass)

    def _static(self):
        cached = getattr(self.p, '_layover_unary', None)
        if cached is None:
            cached = super()._static()
            self.p._layover_unary = cached.copy()
        return cached.copy()

    def price(self, page_index, value, *, boxes=None, phase=1):
        page, T = self.pages[page_index], self.T
        items = self.order if boxes is None else boxes
        if page.prefix:
            rank = min(int(T.rank[b]) for b in page.prefix)+T.e2_tol
            light_below = T.e1_threshold is not None and any(T.mass[b] < T.e1_threshold for b in page.prefix)
            items = [b for b in items if int(T.rank[b]) <= rank
                     and not (light_below and T.mass[b] >= T.e1_threshold)
                     and not any(bool(T.closes_stack[q]) for q in page.prefix)]
        return super().price(page_index, value, boxes=items, phase=phase)

    def height_of(self, page, tier):
        return float(self.T.stack_base_z[page.stacks[0]])+page.prefix_height+(tier+.5)*2.6

    def legal(self, page, boxes, *, any_port=False):
        if not super().legal(page, boxes, any_port=any_port):
            return False
        T = self.T
        combined = page.prefix+tuple(boxes)
        if any(bool(T.closes_stack[b]) for b in combined[:-1]):
            return False
        if sum(bool(T.is_dg[b]) for b in combined) > 1:
            return False
        ranks = [int(T.rank[b]) for b in combined]
        for i in range(1, len(combined)):
            if ranks[i] > min(ranks[:i])+T.e2_tol:
                return False
            heavy = T.e1_threshold is not None and T.mass[combined[i]] >= T.e1_threshold
            if heavy and any(T.mass[b] < T.e1_threshold for b in combined[:i]):
                return False
        return True

    def lash_ok(self, page, boxes):
        if self.oracle is None or not page.deck:
            return True
        T = self.T
        return self.oracle.ratio([T.box_ids[b] for b in page.prefix+tuple(boxes)],
                                 [T.slot_ids[s] for s in page.prefix_slots+page.slots[:len(boxes)]]) <= self.lash_limit

    def knapsack(self, page_index, height):
        page, T = self.pages[page_index], self.T
        if self.oracle is None or not page.deck:
            return super().knapsack(page_index, height)
        slots = page.prefix_slots+page.slots[:height]
        key = (page.kind, slots)
        if key not in self.lash_fit:
            self.lash_fit[key] = self.oracle.weights(20 if page.kind == '20' else 40,
                                                   [T.slot_ids[s] for s in slots])
        base, a = self.lash_fit[key]
        used = sum(a[k]*float(T.mass[b]) for k, b in enumerate(page.prefix))
        room = max(1e-6, self.lash_limit-base-used)
        offset = len(page.prefix)
        units = 240
        return units, lambda b, k: max(math.ceil(units*a[k+offset]*float(T.mass[b])/room-1e-9),
                                      math.ceil(units*float(T.share[b])/page.limit_kg-1e-9))

    def pattern_restow_cost(self, page, boxes):
        combined = page.prefix+tuple(boxes)
        # Only increments above the fixed arrival pattern influence pricing.
        cost = 2.*(self.restows(combined)-self.restows(page.prefix))
        if page.deck:
            # Existing hold PODs create cover crossings even with no new hold.
            for b in boxes:
                cost += 2.*sum(p < self.pod[b] for p in self.arrival_hold_ports[page.cover])
        elif boxes:
            # Opening a blocked cover clears existing deck cargo now. This
            # deliberately overcharges if several chosen pages share a cover.
            cost += 2.*len(self.arrival_deck[page.cover])
            new_ports = {self.pod[b] for b in boxes}
            cost += 2.*sum(sum(p < self.p.pod(b) for p in new_ports)
                          for b in self.arrival_deck[page.cover])
        return cost

    def moments(self, chosen):
        return self.arrival_heel+super().moments(chosen)

    def empty_hold_cells(self, chosen):
        used = sum(len(cells_of_slot(s)) for s in self.arrival.values()
                   if not self.p.stacks[self.p.cells[cells_of_slot(s)[0]].stack_id].on_deck)
        return super().empty_hold_cells(chosen)-used
