"""Stowage as cutting stock: every ship stack is a page, a page's content is a pattern, and duals price new patterns.

Picture. A pattern says "these boxes, in this order, fill this stack" (a 20-ft pattern uses one stack, a long pattern
uses a pair of stacks in neighbouring bays). The master picks patterns so that every box appears once and no stack is
used twice. This is Gilmore and Gomory's cutting-stock method (1961) with stacks as the rolls, in vector form, because a
stack runs out of tiers, mass and height at once. Measured reason for doing this: at 80 % fill, mass per stack is tight
(1,906 t of cargo on 2,032 t of 40-ft stack rating on the 121-box map), so cell counting says nothing.

Column generation. The linear program gives a price (dual) for each box and each stack. The best new pattern for a stack
is then the set of boxes with the highest total price that fits: a knapsack with a tier count, solved exactly by dynamic
programming in ``price``. If no pattern has a positive reduced cost and boxes stay uncovered, the LP itself is infeasible
for this family of patterns, which is a proof about that family (pure stacks), not about the map.

Cycle 2 adds three things to what a pattern is. A mixed page is a deck pair with ``base`` levels of paired 20-ft boxes under
long boxes. Each deck stack has a roof from the line of sight (``physics.roofs``). A horizontal out-of-gauge box takes a
reserved slot, and the stacks beside it stop below it.

APPROXIMATION: knapsack weights round up, and mixed pages use standard-height 20-ft boxes only. Fore-aft balance is a row in
the master; heel is cancelled by pattern swaps. DG segregation and full physics stay with the checker, which decides every
pass.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import random
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model
from stowage.conventions import parse_slot
from scipy.optimize import linprog
from scipy.sparse import csc_matrix, vstack

RESOLUTION_KG = 250.
HIGH_CUBE_M = 2.7
UNCOVERED = 1000.


@dataclass(frozen=True)
class Page:
    kind: str                # '20', 'long' or 'mixed'
    stacks: tuple            # stack indices
    slots: tuple             # slot per position, bottom up. Mixed: left, right, left, right, ..., then long slots
    limit_kg: float          # per stack, on the box's share
    hmax: float
    deck: bool
    cover: object
    base: int = 0            # mixed: levels of paired 20-ft boxes below the long boxes


@dataclass(frozen=True)
class Pattern:
    page: int
    boxes: tuple             # box indices, bottom up
    cost: float


def build_pages(T, mixed=False):
    """Read the pages off the feasibility tables: real slots and real limits only."""
    pages = []
    twenty = {}
    pairs = defaultdict(dict)
    for s in range(len(T.slot_ids)):
        if T.slot_n[s] == 1:
            twenty[(int(T.slot_stacks[s, 0]), int(T.slot_k[s, 0]))] = s
        else:
            a, b = int(T.slot_stacks[s, 0]), int(T.slot_stacks[s, 1])
            if T.slot_k[s, 0] == T.slot_k[s, 1]:
                pairs[(a, b)][int(T.slot_k[s, 0])] = s
    for j in range(len(T.stack_ids)):
        slots = []
        while (j, len(slots)) in twenty:
            slots.append(twenty[(j, len(slots))])
        if slots:
            pages.append(Page('20', (j,), tuple(slots), float(T.stack_lim_20[j]), float(T.stack_hmax[j]),
                              bool(T.stack_deck[j]), T.stack_cover[j]))
    for (a, b), found in sorted(pairs.items()):
        slots = []
        while len(slots) in found:
            slots.append(found[len(slots)])
        if slots:
            pages.append(Page('long', (a, b), tuple(slots), float(min(T.stack_lim_40[a], T.stack_lim_40[b])),
                              float(min(T.stack_hmax[a], T.stack_hmax[b])), bool(T.stack_deck[a]), T.stack_cover[a]))
            if mixed and T.stack_deck[a]:
                limit = float(min(T.stack_lim_20[a], T.stack_lim_20[b], T.stack_lim_mixed[a], T.stack_lim_mixed[b]))
                for base in (1, 2):
                    if base >= len(slots) or any((j, k) not in twenty for j in (a, b) for k in range(base)):
                        continue
                    bottom = [twenty[(j, k)] for k in range(base) for j in (a, b)]
                    pages.append(Page('mixed', (a, b), tuple(bottom)+tuple(slots[base:]), limit,
                                      float(min(T.stack_hmax[a], T.stack_hmax[b])), True, T.stack_cover[a], base))
    return pages


class Model:
    def __init__(self, problem, *, lift_weight=2e-5, oracle=None, lash_limit=.97, layout=None, lcg_target=None, layout_deck=True,
                 hold_reward=1., roofs=None, mixed=False, reserve=True):
        self.p, self.T = problem, problem.engine.T
        self.lift_weight, self.roofs, self.mixed = lift_weight, roofs or {}, mixed
        self.oracle, self.lash_limit = oracle, lash_limit
        self.layout = layout or {}     # hatch cover -> the one discharge port it carries
        self.layout_deck = layout_deck  # False: only the hold keeps to it, the softer form
        self.lcg_target = lcg_target   # cargo LCG upper bound, m: the trim the ballast can't reach
        self.lcg_price, self.fixed_moment = 0., 0.
        self.banned, self.cleared = set(), set()
        T = self.T
        self.boxes = [T.box_index[b] for b in problem.instance.load_list]
        self.static = self._static()
        self.row = {b: k for k, b in enumerate(self.boxes)}
        self.pod = {b: problem.pod(T.box_ids[b]) for b in self.boxes}
        self.heavy = {b: T.e1_threshold is not None and T.mass[b] >= T.e1_threshold for b in self.boxes}
        self.pages = [page for page in map(self._capped, build_pages(T, mixed=mixed)) if page is not None]
        self.reserved = self._reserve() if reserve else {}
        self.tier_cap = self.tier_cap if reserve else {}
        self.pages = [page for page in map(self._reserved_cap, self.pages) if page is not None]
        self.stacks = sorted({j for page in self.pages for j in page.stacks})
        self.stack_row = {j: k for k, j in enumerate(self.stacks)}
        self.ports = sorted(set(self.pod.values()))
        # Bottom-up order: E1 and E2 first, then the later port lower. Boxes that close a stack go last.
        self.order = sorted(self.boxes, key=lambda b: (bool(T.closes_stack[b]), not self.heavy[b], -int(T.rank[b]),
                                                       -self.pod[b], -float(T.mass[b]), b))
        self.types = defaultdict(list)
        for k, page in enumerate(self.pages):
            sig = self.static[np.ix_(self.boxes, page.slots)].tobytes()
            place = self._place(page) if oracle is not None else None
            self.types[(page.kind, page.base, len(page.slots), page.limit_kg, round(page.hmax, 3), sig, place,
                        self.port_of(page))].append(k)
        self.type_of = {k: key for key, pages in self.types.items() for k in pages}
        self.factor = defaultdict(lambda: 1.)
        self.refused = 0
        self.lash_fit = {}
        self.pool = {}
        self.columns = []
        self.hold_reward = hold_reward
        self.stack_cost = .02     # keeps stacks free for the end of a dive
        self.cost_cache = {}
        self.last_refused = None
        self.rarity = {b: int(self.static[b].sum()) for b in self.boxes}
        self.special = {b for b in self.boxes if T.is_dg[b] or T.fallback[b] or T.closes_stack[b] or T.heat[b]
                        or self.rarity[b] < 60}

    def port_of(self, page):
        return self.layout.get(page.cover) if self.layout_deck or not page.deck else None

    def _static(self):
        """The mask's static table, less slots too close to a heat source. Geometry as in E8, with standard tier heights."""
        from stowage.checker.rules_special import _clearance_parameter
        T, p = self.T, self.p
        if hasattr(p, 'page_static'):
            return p.page_static.copy()
        out = T.static.copy()
        clearance = float(_clearance_parameter(p.instance, 'expert.e8.away_from_heat', 'minimum_clearance', 'heat_clearance_m') or 0.)
        sources = [((h.x_min_m, h.x_max_m), (h.y_min_m, h.y_max_m), (h.z_min_m, h.z_max_m)) for h in p.instance.vessel.heat_sources]
        for b in self.boxes:
            if not T.heat[b]:
                continue
            for s in np.flatnonzero(out[b]):
                cells = [p.cells[T.cell_ids[c]] for c in T.slot_cells[s] if c >= 0]
                j = int(T.slot_stacks[s, 0])
                half = 6.096 if len(cells) == 2 else 3.029
                x, y = sum(c.lcg_m for c in cells)/len(cells), sum(c.tcg_m for c in cells)/len(cells)
                floor = float(T.stack_base_z[j])+int(T.slot_k[s, 0])*2.5
                box = ((x-half, x+half), (y-1.219, y+1.219), (floor, floor+3.2))
                for heat in sources:
                    gap = math.sqrt(sum(max(u[0]-v[1], v[0]-u[1], 0.)**2 for u, v in zip(box, heat)))
                    if gap < clearance:
                        out[b, s] = False
            if not out[b].any():
                out[b] = T.static[b]
        p.page_static = out
        return out.copy()

    def _capped(self, page):
        """Deck stacks stop at their sight roof: the line of sight bounds height by position, not one tier count for all."""
        if not page.deck or not self.roofs:
            return page
        room = min(self.roofs.get(j, 1e9)-float(self.T.stack_base_z[j]) for j in page.stacks)
        return self._cut(page, min(page.hmax, room))

    def _cut(self, page, hmax, tiers=None):
        """The page with no position above ``hmax`` metres or ``tiers`` tiers on any side."""
        side = int((hmax+1e-9)//2.591) if tiers is None else min(tiers, int((hmax+1e-9)//2.591))
        if page.kind == 'mixed':
            if side <= page.base:
                return None
            slots = page.slots[:2*page.base+side-page.base]
        else:
            slots = page.slots[:side]
        if not slots:
            return None
        return Page(page.kind, page.stacks, slots, page.limit_kg, hmax, page.deck, page.cover, page.base)

    def _reserve(self):
        """Give each horizontal out-of-gauge box one deck slot, and stop the stacks it would touch below it.

        E9 is geometry: the box's expanded envelope may not meet a neighbour at the same height, and the hold refuses it
        outright. So the reservation is a slot for the box plus a tier cap on the neighbours, found from real envelopes.
        Slots go for the fewest cells lost, as high as the securing check allows.
        """
        p, T = self.p, self.T
        caps = {}
        for page in self.pages:
            if page.kind in ('20', 'long'):
                for j in page.stacks:
                    caps[j] = max(caps.get(j, 0), len(page.slots))
        out = {}
        self.tier_cap = {}
        rect = {}
        for j in range(len(T.stack_ids)):
            if T.stack_deck[j]:
                cell = p.cells[T.cell_ids[T.stack_cells[j][0]]]
                rect[j] = (cell.lcg_m-3.029, cell.lcg_m+3.029, cell.tcg_m-1.219, cell.tcg_m+1.219)
        for b in sorted(self.boxes, key=lambda b: -float(T.mass[b])):
            oog = p.boxes[T.box_ids[b]].oog
            if oog is None or not any((oog.over_width_port_m, oog.over_width_starboard_m, oog.over_length_fore_m, oog.over_length_aft_m)):
                continue
            best = None
            for page in self.pages:
                if page.kind == 'mixed' or not page.deck or (page.kind == 'long') != (T.units[b] == 2):
                    continue
                port = self.port_of(page)
                if port is not None and port != self.pod[b]:
                    continue
                if set(page.stacks) & {j for s in out.values() for j in T.slot_stacks[s] if j >= 0}:
                    continue
                for tier, slot in enumerate(page.slots):
                    if not self.static[b, slot] or tier == 0:
                        continue
                    if not self._high_enough(b, page, tier):
                        continue
                    x = (min(rect[j][0] for j in page.stacks), max(rect[j][1] for j in page.stacks))
                    y = (min(rect[j][2] for j in page.stacks), max(rect[j][3] for j in page.stacks))
                    fat = (x[0]-oog.over_length_aft_m, x[1]+oog.over_length_fore_m,
                           y[0]-oog.over_width_starboard_m, y[1]+oog.over_width_port_m)
                    near = [j for j, r in rect.items() if j not in page.stacks and fat[0] < r[1] and r[0] < fat[1]
                            and fat[2] < r[3] and r[2] < fat[3]]
                    lost = sum(max(0, min(caps.get(j, 0), self.tier_cap.get(j, 99))-tier) for j in near)
                    rank = (lost-tier*.5, slot)
                    if best is None or rank < best[0]:
                        best = (rank, slot, near, tier)
            if best is None:
                continue
            _, slot, near, tier = best
            out[b] = slot
            self.static[b] = False
            self.static[b, slot] = True
            for j in near:
                self.tier_cap[j] = min(self.tier_cap.get(j, 99), tier)
        return out

    def _high_enough(self, box, page, tier):
        """Would a typical column under this box pass securing? A plain model has no check, so yes."""
        if self.oracle is None:
            return True
        T = self.T
        mass = float(T.mass[box])
        under = [mass*(1+.25*(tier-k)) for k in range(tier)]
        slots = [T.slot_ids[s] for s in page.slots[:tier+1]]
        return self.oracle.ratio_of(under+[mass], [2.896]*(tier+1), [int(T.length[box])]*(tier+1), slots) <= self.lash_limit

    def _reserved_cap(self, page):
        limit = min([self.tier_cap.get(j, 99) for j in page.stacks])
        if limit >= 99:
            return page
        return self._cut(page, page.hmax, limit) if limit > 0 else None

    def _place(self, page):
        """Securing loads depend on where a stack stands, so with the oracle on, pages price by position (mirrors alike)."""
        where = parse_slot(self.T.slot_ids[page.slots[0]])
        return (where.bay, where.row+where.row % 2)

    def lash_ok(self, page, boxes):
        if self.oracle is None or not page.deck:
            return True
        T = self.T
        if page.kind == 'mixed':
            for left, right, longs in [self._sides(page, boxes)]:
                for side in (left, right):
                    column = side+longs
                    if self.oracle.ratio([T.box_ids[b] for b, _ in column], [T.slot_ids[s] for _, s in column]) > self.lash_limit:
                        return False
            return True
        return self.oracle.ratio([T.box_ids[b] for b in boxes],
                                 [T.slot_ids[page.slots[k]] for k in range(len(boxes))]) <= self.lash_limit

    def _sides(self, page, boxes):
        """Mixed pattern as (left 20s, right 20s, longs), each a list of (box, slot) bottom up."""
        pairs = list(zip(boxes, page.slots))
        return pairs[:2*page.base:2], pairs[1:2*page.base:2], pairs[2*page.base:]

    def height_of(self, page, tier):
        """Metres above the keel of the middle of this tier, standard box heights."""
        level = tier
        if page.kind == 'mixed':
            level = tier//2 if tier < 2*page.base else tier-page.base
        return float(self.T.stack_base_z[page.stacks[0]])+(level+.5)*2.6

    def restows(self, boxes, page=None):
        """Boxes above an earlier-leaving box in this stack, the D15 count (a box counts once per later call)."""
        count = 0
        for i, upper in enumerate(boxes):
            lowers = boxes[:i]
            if page is not None and page.kind == 'mixed' and i < 2*page.base:
                lowers = boxes[i % 2:i:2]
            for port in self.ports:
                if port < self.pod[upper] and any(self.pod[lower] == port for lower in lowers):
                    count += 1
        return count

    def legal(self, page, boxes, *, any_port=False):
        T = self.T
        port = None if any_port else self.port_of(page)
        if port is not None and any(self.pod[b] != port for b in boxes):
            return False
        if sum(bool(T.is_dg[b]) for b in boxes) > 1:
            return False
        if any(bool(T.closes_stack[b]) for b in boxes[:-1]):
            return False
        if page.kind == 'mixed':
            return self._mixed_legal(page, boxes)
        if sum(float(T.height[b]) for b in boxes) > page.hmax+1e-9:
            return False
        if sum(float(T.share[b]) for b in boxes) > page.limit_kg+1e-6:
            return False
        ranks = [int(T.rank[b]) for b in boxes]
        for i in range(1, len(boxes)):
            if ranks[i] > min(ranks[:i])+T.e2_tol:
                return False
            if self.heavy[boxes[i]] and not all(self.heavy[b] for b in boxes[:i]):
                return False
        return all(self.static[b, page.slots[k]] for k, b in enumerate(boxes))

    def _mixed_legal(self, page, boxes):
        """Both 20-ft bases complete and level, at least one long box, each side legal on its own."""
        T = self.T
        if len(boxes) <= 2*page.base:
            return False
        left, right, longs = self._sides(page, boxes)
        if any(T.units[b] != 1 or T.height[b] > HIGH_CUBE_M for b, _ in left+right):
            return False
        if any(T.units[b] == 1 for b, _ in longs):
            return False
        for side in (left, right):
            column = [b for b, _ in side]+[b for b, _ in longs]
            shares = sum(float(T.share[b]) for b in column)
            if shares > page.limit_kg+1e-6 or sum(float(T.height[b]) for b in column) > page.hmax+1e-9:
                return False
            ranks = [int(T.rank[b]) for b in column]
            for i in range(1, len(column)):
                if ranks[i] > min(ranks[:i])+T.e2_tol:
                    return False
                if self.heavy[column[i]] and not all(self.heavy[b] for b in column[:i]):
                    return False
        return all(self.static[b, s] for b, s in left+right+longs)

    def add(self, page_index, boxes):
        page = self.pages[page_index]
        if not boxes or not self.legal(page, boxes):
            return False
        if not self.lash_ok(page, boxes):
            # Too much overturning load for this stand: price this tier count with less mass from now on.
            refused = (self.type_of[page_index], len(boxes))
            self.factor[refused] = max(.8, self.factor[refused]*.985)
            self.refused += 1
            return False
        key = (page_index, tuple(boxes))
        if key in self.pool:
            return False
        lifts = 2.*self.restows(boxes, page)
        column = Pattern(page_index, tuple(boxes), lifts)
        self.pool[key] = len(self.columns)
        self.columns.append(column)
        return True

    def knapsack(self, page_index, height):
        """Capacity and tier weight of a box for a pattern ``height`` tiers tall.

        Ratings count mass share. On deck the securing ratio is close to linear in tier masses for one column height, so
        a[k]*mass is a knapsack weight that puts heavy boxes low and refuses what would overload a lashing. Deck ratings
        are then enforced by ``legal`` on each priced pattern.
        """
        page = self.pages[page_index]
        T = self.T
        units = 240
        if self.oracle is None or not page.deck:
            return units, lambda b, k: math.ceil(units*float(T.share[b])/page.limit_kg-1e-9)
        key = (page.kind, page.slots[:height])
        if key not in self.lash_fit:
            if page.kind == 'mixed':
                self.lash_fit[key] = self.oracle.weights_mixed(page.base, [T.slot_ids[s] for s in page.slots[:height]])
            else:
                length = 20 if page.kind == '20' else 40
                self.lash_fit[key] = self.oracle.weights(length, [T.slot_ids[s] for s in page.slots[:height]])
        base, a = self.lash_fit[key]
        room = max(1e-6, self.lash_limit-base)
        return units, lambda b, k: math.ceil(units*a[k]*float(T.mass[b])/room-1e-9)

    def price(self, page_index, value, *, boxes=None, phase=1):
        """Best pattern of each height for one page under box prices ``value``. Exact over ordered subsets of the DP's weights.

        State: tiers used, high cubes used, knapsack units used. Returns {tier count: (total price, boxes bottom up)}.
        """
        T, page = self.T, self.pages[page_index]
        kind_type = self.type_of[page_index]
        fits = lambda b, k: (T.units[b] == 1) == (k < 2*page.base if page.kind == 'mixed' else page.kind == '20')
        items = [b for b in (self.order if boxes is None else boxes)
                 if any(fits(b, k) and self.static[b, s] for k, s in enumerate(page.slots))
                 and not (page.kind == 'mixed' and T.units[b] == 1 and T.height[b] > HIGH_CUBE_M)]
        out = {}
        heights = range(1, len(page.slots)+1) if self.oracle is not None and page.deck else (len(page.slots),)
        for height in heights:
            if page.kind == 'mixed' and height <= 2*page.base:
                continue
            limit, weigh = self.knapsack(page_index, height)
            best = np.full((height+1, height+1, limit+1), -np.inf)
            best[0, 0, 0] = 0.
            taken = []
            for b in items:
                high = int(T.height[b] > HIGH_CUBE_M)
                candidate = np.full(best.shape, -np.inf)
                weights = []
                for k in range(height):
                    weight = weigh(b, k)
                    weights.append(weight)
                    if weight > limit or not fits(b, k) or not self.static[b, page.slots[k]]:
                        continue
                    gain = value[self.row[b]]
                    if self.lcg_target is not None:
                        x = self.p.cells[T.cell_ids[T.slot_cells[page.slots[0], 0]]].lcg_m
                        gain += self.lcg_price*float(T.mass[b])/1000.*(x-self.lcg_target)
                    if phase == 3:
                        gain -= self.moment_cost(page, k, b)-(0. if page.deck else self.hold_gain(b))
                    candidate[k+1, high:, weight:] = best[k, :height+1-high, :limit+1-weight]+gain
                better = candidate > best+1e-12
                taken.append((b, better, high, weights))
                best = np.where(better, candidate, best)
            for k in (range(1, height+1) if height == len(page.slots) and len(heights) == 1 else (height,)):
                view = best[k].copy()
                side = k-page.base if page.kind == 'mixed' else k
                for h in range(height+1):
                    if h > side or (side-h)*2.591+h*2.896 > page.hmax+1e-9:
                        view[h] = -np.inf
                allowed = int(limit*self.factor[(kind_type, k)])
                view[:, allowed+1:] = -np.inf
                if not np.isfinite(view).any():
                    continue
                h, m = np.unravel_index(int(np.argmax(view)), view.shape)
                total = float(view[h, m])
                chosen = []
                at_k, at_h, at_m = k, int(h), int(m)
                for b, flags, high, weights in reversed(taken):
                    if at_k > 0 and flags[at_k, at_h, at_m]:
                        chosen.append(b)
                        at_k, at_h, at_m = at_k-1, at_h-high, at_m-weights[at_k-1]
                out[k] = (total, tuple(reversed(chosen)))
        return out

    def lcg_moment(self, column):
        """Mass × distance forward of the target, tonne-metres. The master keeps the sum at or below zero."""
        page = self.pages[column.page]
        x = self.p.cells[self.T.cell_ids[self.T.slot_cells[page.slots[0], 0]]].lcg_m
        return sum(float(self.T.mass[b]) for b in column.boxes)/1000.*(x-self.lcg_target)

    def column_cost(self, column, phase):
        key = (column.page, column.boxes, phase)
        if key not in self.cost_cache:
            self.cost_cache[key] = self._column_cost(column, phase)
        return self.cost_cache[key]

    def _column_cost(self, column, phase):
        """Phase 1: cover. Phase 2: lifts. Phase 3: fewest stacks, so that room survives for the end of a dive."""
        if phase == 1:
            return 0.
        if phase == 2:
            return column.cost
        page = self.pages[column.page]
        total = .02*column.cost+sum(self.moment_cost(page, tier, b) for tier, b in enumerate(column.boxes))
        if not page.deck:
            # E6: a heavy box may ride on deck only if no compatible hold cell stays empty, so reward filling the hold.
            total -= sum(self.hold_gain(b) for b in column.boxes)
        else:
            total += self.stack_cost*len(page.stacks)
        return total

    def hold_gain(self, box):
        """Filling the hold pays, heavy boxes most: E6 allows them on deck only when no hold cell fits them."""
        return self.hold_reward*float(self.T.units[box])+(4. if self.heavy[box] else 0.)

    def moment_cost(self, page, tier, box):
        """Vertical moment in cost units: low mass raises GM, and stacks stay flat, which helps sight and racking."""
        return self.lift_weight*float(self.T.mass[box])/1000.*self.height_of(page, tier)**2/10.

    def solve_lp(self, *, active_boxes, active_stacks, hold_full, phase):
        """Restricted master LP. Returns (objective, x, box prices, stack prices, uncovered)."""
        boxes = sorted(active_boxes, key=self.row.get)
        rows = {b: k for k, b in enumerate(boxes)}
        stacks = sorted(active_stacks)
        srow = {j: len(boxes)+k for k, j in enumerate(stacks)}
        n_rows = len(boxes)+len(stacks)
        use = [k for k, c in enumerate(self.columns)
               if k not in self.banned and all(b in rows for b in c.boxes) and all(j in srow for j in self.pages[c.page].stacks)]
        data, ri, ci, cost = [], [], [], []
        for col, k in enumerate(use):
            c = self.columns[k]
            for b in c.boxes:
                ri.append(rows[b]); ci.append(col); data.append(1.)
            for j in self.pages[c.page].stacks:
                ri.append(srow[j]); ci.append(col); data.append(1.)
            cost.append(self.column_cost(c, phase))
        col = len(use)
        for b in boxes:
            ri.append(rows[b]); ci.append(col); data.append(1.)
            cost.append(UNCOVERED)
            col += 1
        if self.lcg_target is not None:
            cost.append(1.)     # slack on the fore-aft row, per t·m: soft, so a dive can't make the LP infeasible
            col += 1
        A = csc_matrix((data, (ri, ci)), shape=(n_rows, col))
        # Boxes: exactly once, with a slack. Stacks: at most once.
        upper, rhs = A[len(boxes):], np.ones(len(stacks))
        if self.lcg_target is not None:
            row = np.zeros(col)
            for index, k in enumerate(use):
                row[index] = self.lcg_moment(self.columns[k])
            row[-1] = -1.
            upper = vstack([upper, csc_matrix(row)], format='csc')
            rhs = np.append(rhs, -self.fixed_moment)
        res = linprog(np.array(cost), A_eq=A[:len(boxes)], b_eq=np.ones(len(boxes)),
                      A_ub=upper, b_ub=rhs, bounds=(0, None), method='highs')
        if res.status != 0:
            raise RuntimeError(f'master LP status {res.status}: {res.message}')
        box_price = np.zeros(len(self.boxes))
        for b, k in rows.items():
            box_price[self.row[b]] = res.eqlin.marginals[k]
        stack_price = {j: float(res.ineqlin.marginals[k]) for k, j in enumerate(stacks)}
        self.lcg_price = float(res.ineqlin.marginals[-1]) if self.lcg_target is not None else 0.
        x = {use[k]: float(res.x[k]) for k in range(len(use)) if res.x[k] > 1e-7}
        tail = res.x[len(use):len(use)+len(boxes)]
        uncovered = float(tail.sum())
        self.short = {b: float(v) for b, v in zip(boxes, tail) if v > 1e-6}
        return float(res.fun), x, box_price, stack_price, uncovered

    def generate(self, *, active_boxes, active_stacks, deadline, hold_full=False, phase=1, max_rounds=400, log=None):
        """Column generation. Exhausted pricing with uncovered boxes means the pattern family can't cover them."""
        active = set(active_boxes)
        by_port = {port: [b for b in self.order if self.pod[b] == port and b in active] for port in self.ports}
        everything = [b for b in self.order if b in active]
        rounds, last = 0, None
        while True:
            rounds += 1
            objective, x, value, stack_price, uncovered = self.solve_lp(
                active_boxes=active_boxes, active_stacks=active_stacks, hold_full=hold_full, phase=phase)
            last = dict(objective=objective, x=x, uncovered=uncovered, rounds=rounds, columns=len(self.columns), proved=False)
            if log and rounds % 10 == 1:
                log(f'round {rounds} objective {objective:.3f} uncovered {uncovered:.3f} columns {len(self.columns)}')
            if monotonic() >= deadline or rounds >= max_rounds:
                return last
            added = 0
            refused_before = self.refused
            for pages in self.types.values():
                pages = [k for k in pages if all(j in active_stacks for j in self.pages[k].stacks)]
                if not pages:
                    continue
                # Pages of one type price alike but for their stack price, so one pricing run serves them all.
                pages.sort(key=lambda k: -sum(stack_price[j] for j in self.pages[k].stacks))
                port = self.port_of(self.pages[pages[0]])
                for subset in ((by_port.get(port, []),) if port is not None else (everything, *by_port.values())):
                    if not subset:
                        continue
                    for total, boxes in self.price(pages[0], value, boxes=subset, phase=phase).values():
                        fresh = 0
                        for k in pages:
                            sigma = sum(stack_price[j] for j in self.pages[k].stacks)
                            # Phase 3 costs are already inside the DP's total; phase 2 costs are not.
                            cost = (2.*self.restows(boxes, self.pages[k]) if phase == 2 else
                                    self.stack_cost*len(self.pages[k].stacks) if phase == 3 and self.pages[k].deck else 0.)
                            if total+sigma-cost > 1e-6 and fresh < 4:
                                fresh += self.add(k, list(boxes))
                        added += fresh
                if monotonic() >= deadline:
                    break
            if added == 0 and self.refused == refused_before:
                last['proved'] = True
                return last

    def first_fit(self, boxes, stacks, rng=None, chosen=(), strict=True):
        """Greedy end of a dive: heaviest first, each box on the fullest stack that still takes it.

        Columns already chosen may be topped up, since a box can only go on top in order. Returns the whole list of columns.
        With ``strict`` off, securing isn't tested here: ``polish`` repairs it on the finished ship.
        """
        T = self.T
        free = set(stacks)
        order = [b for b in self.order if b in boxes]
        open_pages = defaultdict(list)     # page -> boxes
        start = {}
        for k in chosen:
            column = self.columns[k]
            open_pages[column.page] = list(column.boxes)
            start[column.page] = k
        pages = sorted(range(len(self.pages)), key=lambda k: (self.pages[k].deck, -len(self.pages[k].slots), k))
        for wanted in ('long', '20'):
            kept = [b for b in order if (T.units[b] == 1) == (wanted == '20')]
            for b in sorted(kept, key=lambda b: b not in self.special):
                best = None
                for k in pages:
                    page = self.pages[k]
                    if len(open_pages[k]) >= len(page.slots):
                        continue
                    if page.kind != wanted and not (page.kind == 'mixed' and wanted == 'long' and open_pages[k]):
                        continue
                    if not open_pages[k] and not set(page.stacks) <= free:
                        continue
                    trial = open_pages[k]+[b]
                    if self.legal(page, trial) and (not strict or self.lash_ok(page, trial)):
                        room = page.limit_kg-sum(float(T.share[x]) for x in trial)
                        # Heavy boxes go to the hold first (E6); others keep the hold's hatch shut if they can.
                        rank = (page.deck == bool(self.heavy[b]), not open_pages[k], room+(rng.uniform(0, 3000) if rng else 0.))
                        if best is None or rank < best[0]:
                            best = (rank, k)
                if best is None:
                    self.last_refused = (T.box_ids[b], int(T.length[b]), float(T.mass[b]), float(T.height[b]), int(self.static[b].sum()), bool(T.closes_stack[b]), bool(T.is_dg[b]),
                                         len(free), sum(1 for k in open_pages if open_pages[k]))
                    return None
                k = best[1]
                if not open_pages[k]:
                    free -= set(self.pages[k].stacks)
                open_pages[k].append(b)
        return [self._pattern(k, tuple(column)) for k, column in open_pages.items() if column]

    def tail(self, boxes, stacks, *, seconds, seed=0, prefer_high=True):
        """Exact end of a dive: cover the remaining boxes with pool patterns on the remaining stacks."""
        model = cp_model.CpModel()
        use = [k for k, c in enumerate(self.columns)
               if set(c.boxes) <= boxes and set(self.pages[c.page].stacks) <= stacks]
        x = {k: model.NewBoolVar(f'x{k}') for k in use}
        cover, on_stack = defaultdict(list), defaultdict(list)
        for k in use:
            c = self.columns[k]
            for b in c.boxes:
                cover[b].append(x[k])
            for j in self.pages[c.page].stacks:
                on_stack[j].append(x[k])
        for b in boxes:
            if not cover[b]:
                return None
            model.AddExactlyOne(cover[b])
        for column in on_stack.values():
            model.AddAtMostOne(column)
        model.Minimize(sum(int(100*self.column_cost(self.columns[k], 3))*x[k] for k in use))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = seconds
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = seed
        if solver.Solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        return [k for k in use if solver.Value(x[k])]

    def dive(self, *, deadline, hold_full=False, log=None, rng=None, rounds=5, tail_boxes=10, retreats=8):
        """Column-generation diving: fix strong patterns, reprice on the rest, and close the end exactly.

        When few boxes remain, CP-SAT finishes them from the pool. If it can't, the latest fixes are undone and tried
        again with a larger tail, a small backtrack in place of branching.
        """
        boxes, stacks = set(self.boxes), set(self.stacks)
        chosen = []
        retreat = 0
        self.fixed_moment = 0.
        while boxes:
            if len(boxes) <= tail_boxes or not stacks:
                ending = self.first_fit(boxes, stacks, rng, chosen)
                if ending is None and retreat >= 2:
                    ending = self.first_fit(boxes, stacks, rng, chosen, strict=False)
                if ending is not None:
                    return ending, dict(left=0, retreats=retreat, tail='first_fit')
                self.generate(active_boxes=boxes, active_stacks=stacks, deadline=deadline, phase=1, max_rounds=rounds)
                ending = self.tail(boxes, stacks, seconds=min(15., max(1., deadline-monotonic())),
                                   seed=retreat) if monotonic() < deadline else None
                if ending is not None:
                    return chosen+ending, dict(left=0, retreats=retreat)
                if log:
                    T = self.T
                    log('tail failed on '+str(len(boxes))+str(sorted(self.rarity[b] for b in boxes)[:8])+f' refused {self.last_refused} stacks {len(stacks)}')
                if monotonic() >= deadline or retreat >= retreats or not chosen:
                    return None, dict(left=len(boxes), reason='tail', retreats=retreat)
                retreat += 1
                # Undo the latest fixes, a growing number each time, and dive again from there.
                target = len(boxes)+10+6*retreat
                while len(boxes) < target and chosen:
                    k = chosen.pop(rng.randrange(max(0, len(chosen)-12), len(chosen)) if rng else -1)
                    if self.lcg_target is not None:
                        self.fixed_moment -= self.lcg_moment(self.columns[k])
                    boxes |= set(self.columns[k].boxes)
                    stacks |= set(self.pages[self.columns[k].page].stacks)
                rng = rng or random.Random(retreat)
            result = self.generate(active_boxes=boxes, active_stacks=stacks, deadline=deadline, phase=3,
                                   hold_full=hold_full, max_rounds=rounds)
            if monotonic() >= deadline:
                return None, dict(left=len(boxes), reason='time')
            if not result['x']:
                return None, dict(left=len(boxes), reason='no column')
            noise = (lambda: rng.uniform(0., .15)) if rng else (lambda: 0.)
            # Rare boxes first: a box with few legal slots can't wait for the end of the dive.
            def strength(item):
                k, value = item
                has_rare = any(b in self.special for b in self.columns[k].boxes)
                in_hold = not self.pages[self.columns[k].page].deck
                return (-(value+noise()+(.6 if has_rare else 0.)+(.4 if in_hold else 0.)), -len(self.columns[k].boxes))
            ranked = sorted(result['x'].items(), key=strength)
            fixed_now = 0
            for k, value in ranked:
                c = self.columns[k]
                page = self.pages[c.page]
                if fixed_now and value < .7 and not (self.special & set(c.boxes) and value > .3):
                    break
                if set(c.boxes) <= boxes and set(page.stacks) <= stacks:
                    chosen.append(k)
                    boxes -= set(c.boxes)
                    stacks -= set(page.stacks)
                    fixed_now += 1
                    if self.lcg_target is not None:
                        self.fixed_moment += self.lcg_moment(c)
            if log:
                log(f'dive fixed {fixed_now}, left {len(boxes)} boxes, {len(stacks)} stacks, uncovered {result["uncovered"]:.2f}')
            if fixed_now == 0:
                return None, dict(left=len(boxes), reason='stuck')
        return chosen, dict(left=0, retreats=retreat)

    def moments(self, chosen):
        """Heel and fore-aft moments of the chosen patterns, t·m."""
        p, T = self.p, self.T
        heel = 0.
        for k in chosen:
            c = self.columns[k]
            page = self.pages[c.page]
            for tier, b in enumerate(c.boxes):
                heel += float(T.mass[b])/1000.*p.cells_tcg(T.slot_ids[page.slots[tier]])
        return heel

    def balance(self, chosen, rng, rounds=4000):
        """Swap whole patterns between deck pages of one type to cancel heel. Each swap keeps every per-stack rule true.

        Picking the swaps is number partitioning: split the stack moments into two near-equal sides.
        """
        p, T = self.p, self.T
        chosen = list(chosen)
        type_of = {}
        for key, pages in self.types.items():
            for k in pages:
                type_of[k] = key

        def tcg(page):
            return p.cells_tcg(T.slot_ids[page.slots[0]])

        def stack_moment(k, page):
            return sum(float(T.mass[b]) for b in self.columns[k].boxes)/1000.*tcg(page)
        used = {self.columns[k].page: i for i, k in enumerate(chosen)}
        heel = self.moments(chosen)
        groups = defaultdict(list)
        for k, page in enumerate(self.pages):
            if page.deck:
                groups[type_of[k]].append(k)
        keys = [g for g in groups.values() if len(g) > 1]
        for _ in range(rounds):
            if abs(heel) < 5. or not keys:
                break
            group = rng.choice(keys)
            one, two = rng.sample(group, 2)
            if one not in used:
                continue
            i = used[one]
            k = chosen[i]
            before = stack_moment(k, self.pages[one])
            after = stack_moment(k, self.pages[two])
            if two in used:
                j = used[two]
                other = chosen[j]
                used_box = other
                before += stack_moment(other, self.pages[two])
                after += stack_moment(other, self.pages[one])
            elif any(j in {x for page in used for x in self.pages[page].stacks} for j in self.pages[two].stacks):
                continue
            if abs(heel-before+after) >= abs(heel):
                continue
            if not self.lash_ok(self.pages[two], self.columns[k].boxes) or (
                    two in used and not self.lash_ok(self.pages[one], self.columns[used_box].boxes)):
                continue
            heel += after-before
            moved = self._moved(k, two)
            if two in used:
                chosen[used[two]] = self._moved(chosen[used[two]], one)
                used[one], used[two] = used[two], used[one]
            else:
                del used[one]
                used[two] = i
            chosen[i] = moved
        return chosen, heel

    def _moved(self, k, page_index):
        c = self.columns[k]
        key = (page_index, c.boxes)
        if key not in self.pool:
            self.pool[key] = len(self.columns)
            self.columns.append(Pattern(page_index, c.boxes, c.cost))
        return self.pool[key]

    def polish(self, chosen, oracle, limit=.995):
        """Clear the last securing excess by swapping single boxes between deck columns, checked on the finished ship.

        The pricing read GM and draught from the stow before this one. Here the oracle reads this stow, so a column that
        fails is real. A box moves to a lighter position in another column only if both columns stay legal and within limit.
        """
        self.oracle, saved = oracle, self.oracle
        self.lash_limit, saved_limit = limit, self.lash_limit
        chosen = list(chosen)
        swaps = 0
        try:
            for _ in range(60):
                bad = [i for i, k in enumerate(chosen) if not self.lash_ok(self.pages[self.columns[k].page], self.columns[k].boxes)]
                if not bad:
                    return chosen, dict(swaps=swaps, left=0)
                moved = False
                for i in bad:
                    moved = self._lighten(chosen, i) or self._shift(chosen, i)
                    if moved:
                        swaps += 1
                        break
                if not moved:
                    return chosen, dict(swaps=swaps, left=len(bad))
            return chosen, dict(swaps=swaps, left=-1)
        finally:
            self.oracle, self.lash_limit = saved, saved_limit

    def _lighten(self, chosen, i):
        T = self.T
        one = self.columns[chosen[i]]
        page_one = self.pages[one.page]
        for k in range(len(one.boxes)-1, -1, -1):
            a = one.boxes[k]
            choices = []
            for j, other in enumerate(chosen):
                column = self.columns[other]
                page_two = self.pages[column.page]
                if j == i or not page_two.deck:
                    continue
                for m, b in enumerate(column.boxes):
                    if T.units[b] == T.units[a] and T.mass[b] < T.mass[a]:
                        choices.append((-float(T.mass[b]), j, m))
            for _, j, m in sorted(choices):
                column = self.columns[chosen[j]]
                page_two = self.pages[column.page]
                first, second = list(one.boxes), list(column.boxes)
                first[k], second[m] = second[m], first[k]
                first, second = tuple(first), tuple(second)
                if (self.legal(page_one, first) and self.legal(page_two, second)
                        and self.lash_ok(page_one, first) and self.lash_ok(page_two, second)):
                    chosen[i] = self._pattern(one.page, first)
                    chosen[j] = self._pattern(column.page, second)
                    return True
        return False

    def _shift(self, chosen, i):
        """Take one box out of a failing column, onto a column with room or an empty page."""
        T = self.T
        one = self.columns[chosen[i]]
        page_one = self.pages[one.page]
        taken = {j for k in chosen for j in self.pages[self.columns[k].page].stacks}
        for k in sorted(range(len(one.boxes)), key=lambda k: -float(T.mass[one.boxes[k]])):
            a = one.boxes[k]
            first = one.boxes[:k]+one.boxes[k+1:]
            if not first or not self.legal(page_one, first) or not self.lash_ok(page_one, first):
                continue
            for j, other in enumerate(chosen):
                column = self.columns[other]
                page_two = self.pages[column.page]
                if j == i or not page_two.deck or len(column.boxes) >= len(page_two.slots):
                    continue
                # Keep the column's order: the moved box goes where the canonical order puts it.
                second = tuple(sorted(column.boxes+(a,), key=self.order.index))
                if page_two.kind == 'mixed':
                    second = column.boxes[:2*page_two.base]+tuple(sorted(column.boxes[2*page_two.base:]+(a,), key=self.order.index))
                if self.legal(page_two, second) and self.lash_ok(page_two, second):
                    chosen[i] = self._pattern(one.page, first)
                    chosen[j] = self._pattern(column.page, second)
                    return True
            for index, page in enumerate(self.pages):
                if not page.deck or page.kind == 'mixed' or set(page.stacks) & taken:
                    continue
                if self.legal(page, (a,)) and self.lash_ok(page, (a,)):
                    chosen[i] = self._pattern(one.page, first)
                    chosen.append(self._pattern(index, (a,)))
                    return True
        return False

    def _pattern(self, page_index, boxes):
        key = (page_index, boxes)
        if key not in self.pool:
            self.pool[key] = len(self.columns)
            self.columns.append(Pattern(page_index, boxes, 2.*self.restows(boxes, self.pages[page_index])))
        return self.pool[key]

    def empty_hold_cells(self, chosen):
        used = sum(len(self.columns[k].boxes)*int(self.T.units[self.columns[k].boxes[0]]) for k in chosen
                   if not self.pages[self.columns[k].page].deck)
        total = sum(len(self.T.stack_cells[j]) for j in self.stacks if not self.T.stack_deck[j])
        return total-used

    def assignment(self, chosen):
        T = self.T
        out = {}
        for k in chosen:
            c = self.columns[k]
            page = self.pages[c.page]
            for tier, b in enumerate(c.boxes):
                out[T.box_ids[b]] = T.slot_ids[page.slots[tier]]
        return out
