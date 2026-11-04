# Provenance: copied from experiments/mvp/frontier/lab/c2/c2-cgscale/pages.py.
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

APPROXIMATION: knapsack weights round up, which can only refuse a pattern. Only pure stacks are generated: no 20-ft boxes
under long boxes on deck. Fore-aft balance is a row in the master; heel is cancelled by pattern swaps. DG segregation, OOG
reach and full physics stay with the checker, which decides every pass.
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
from scipy.sparse import csc_matrix, vstack, hstack, eye

RESOLUTION_KG = 250.
HIGH_CUBE_M = 2.7
UNCOVERED = 1000.


@dataclass(frozen=True)
class Page:
    kind: str                # '20' or 'long'
    stacks: tuple            # stack indices
    slots: tuple             # slot index per tier, bottom up
    limit_kg: float          # per stack, on the box's share
    hmax: float
    deck: bool
    cover: object


@dataclass(frozen=True)
class Pattern:
    page: int
    boxes: tuple             # box indices, bottom up
    cost: float


def build_pages(T):
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
    return pages


class Model:
    def __init__(self, problem, *, deck_tiers=None, deck_mass_factor=1., lift_weight=2e-5, oracle=None, lash_limit=.97, layout=None, lcg_target=None, layout_deck=True, hold_reward=1.):
        self.p, self.T = problem, problem.engine.T
        self.deck_tiers, self.deck_mass_factor, self.lift_weight = deck_tiers, deck_mass_factor, lift_weight
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
        self.pages = [self._capped(page) for page in build_pages(T)]
        self.stacks = sorted({j for page in self.pages for j in page.stacks})
        self.stack_row = {j: k for k, j in enumerate(self.stacks)}
        self.pod = {b: problem.pod(T.box_ids[b]) for b in self.boxes}
        self.ports = sorted(set(self.pod.values()))
        self.heavy = {b: T.e1_threshold is not None and T.mass[b] >= T.e1_threshold for b in self.boxes}
        # Bottom-up order: E1 and E2 first, then the later port lower. Boxes that close a stack go last.
        self.order = sorted(self.boxes, key=lambda b: (bool(T.closes_stack[b]), not self.heavy[b], -int(T.rank[b]),
                                                       -self.pod[b], -float(T.mass[b]), b))
        self.types = defaultdict(list)
        for k, page in enumerate(self.pages):
            sig = self.static[np.ix_(self.boxes, page.slots)].tobytes()
            place = self._place(page) if oracle is not None else None
            self.types[(page.kind, len(page.slots), page.limit_kg, round(page.hmax, 3), sig, place,
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
        self.pricing_cache = {}
        self.seed_chosen = None
        self.master_matrix = None
        self.master_matrix_size = 0
        self.rarity = {b: int(self.static[b].sum()) for b in self.boxes}
        self.special = {b for b in self.boxes if T.is_dg[b] or T.fallback[b] or T.closes_stack[b] or T.heat[b]
                        or self.rarity[b] < 60}

    def port_of(self, page):
        return self.layout.get(page.cover) if self.layout_deck or not page.deck else None

    def _static(self):
        """The mask's static table, less the slots E8 refuses a heat-shy box. That rule is state-free but not in the table."""
        from stowage.checker.rules import placement_violations, _geometry_index, _usable_ship
        from stowage.checker.rules_special import special_violations
        from stowage.conventions import cells_of_slot
        from stowage.model.ship import Placement
        T, p = self.T, self.p
        # Every ladder rung shares the same immutable problem/arrival state. E8's
        # empty-ship probe does not depend on any rung setting; retain its exact
        # result instead of repeating whole-vessel checker calls.
        out = T.static.copy()
        ship = p.instance.onboard
        _, _, usable_cells, geometry_problems = _geometry_index(p.instance)
        direct_special = not ship.placements and not geometry_problems
        valid_ids = {box.id for box in p.instance.containers}
        for b in self.boxes:
            if not T.heat[b]:
                continue
            box = p.boxes[T.box_ids[b]]
            for s in np.flatnonzero(out[b]):
                slot = T.slot_ids[s]
                if direct_special and all(cid in usable_cells for cid in cells_of_slot(slot)):
                    # E8 heat is emitted only by this checker function. On an empty
                    # valid arrival, its candidate ship is exactly the same as the
                    # one placement_violations passes after its unrelated core rules.
                    candidate = ship.model_copy(update={'placements': [
                        Placement.model_construct(container_id=box.id, slot=slot)]})
                    usable = _usable_ship(candidate, valid_ids, usable_cells)
                    found = special_violations(p.instance, usable, call_index=p.instance.call_index,
                                               focus_container_id=box.id)
                else:
                    found = placement_violations(p.instance, ship, box, slot)
                if any('away_from_heat' in str(v.rule_id) for v in found):
                    out[b, s] = False
            if not out[b].any():
                # The empty-ship probe refused everything, so it says nothing here; the full checker still decides.
                out[b] = T.static[b]
        return out

    def _capped(self, page):
        """Measured gates: sight needs lower deck roofs, racking needs less mass on a deck stack."""
        if not page.deck:
            return page
        slots = page.slots
        if self.deck_tiers:
            # An int caps every deck stack; a tuple caps the forward, middle and aft thirds of the deck bays.
            caps = self.deck_tiers if isinstance(self.deck_tiers, (tuple, list)) else (self.deck_tiers,)*3
            bays = sorted({parse_slot(s).bay for s in self.T.slot_ids if self.T.slot_deck[self.T.slot_index[s]] and self.T.slot_n[self.T.slot_index[s]] == 1})
            bay = parse_slot(self.T.slot_ids[page.slots[0]]).bay
            place = sum(1 for b in bays if b < bay)*3//max(1, len(bays))
            slots = page.slots[:caps[min(place, 2)]]
        return Page(page.kind, page.stacks, slots, page.limit_kg*self.deck_mass_factor, page.hmax, True, page.cover)

    def _place(self, page):
        """Securing loads depend on where a stack stands, so with the oracle on, pages price by position (mirrors alike)."""
        where = parse_slot(self.T.slot_ids[page.slots[0]])
        return (where.bay, where.row+where.row % 2)

    def lash_ok(self, page, boxes):
        if self.oracle is None or not page.deck:
            return True
        T = self.T
        return self.oracle.ratio([T.box_ids[b] for b in boxes],
                                 [T.slot_ids[page.slots[k]] for k in range(len(boxes))]) <= self.lash_limit

    def height_of(self, page, tier):
        """Metres above the keel of the middle of this tier, standard box heights."""
        return float(self.T.stack_base_z[page.stacks[0]])+(tier+.5)*2.6

    def restows(self, boxes):
        """Boxes above an earlier-leaving box in this stack, the D15 count (a box counts once per later call)."""
        count = 0
        for i, upper in enumerate(boxes):
            for port in self.ports:
                if port < self.pod[upper] and any(self.pod[lower] == port for lower in boxes[:i]):
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
        lifts = 2.*self.restows(boxes)
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
            length = 20 if page.kind == '20' else 40
            self.lash_fit[key] = self.oracle.weights(length, [T.slot_ids[s] for s in page.slots[:height]])
        base, a = self.lash_fit[key]
        room = max(1e-6, self.lash_limit-base)
        return units, lambda b, k: math.ceil(units*a[k]*float(T.mass[b])/room-1e-9)

    def _pricing(self, page_index, height):
        """Cache immutable eligibility and weights, shared by equivalent stack types."""
        key = (self.type_of[page_index], height)
        if key not in self.pricing_cache:
            T, page = self.T, self.pages[page_index]
            limit, weigh = self.knapsack(page_index, height)
            items = [b for b in self.order if (T.units[b] == 1) == (page.kind == '20')
                     and self.static[b, list(page.slots[:height])].any()]
            # Group tiers with the same integer weight so one NumPy operation serves them together.
            cached = {}
            for b in items:
                by_weight = defaultdict(list)
                for k in range(height):
                    weight = weigh(b, k)
                    if 0 <= weight <= limit and self.static[b, page.slots[k]]:
                        by_weight[weight].append(k)
                cached[b] = (int(T.height[b] > HIGH_CUBE_M), tuple(weigh(b, k) for k in range(height)),
                             tuple((weight, np.asarray(tiers, dtype=np.intp))
                                   for weight, tiers in by_weight.items()))
            self.pricing_cache[key] = (limit, cached)
        return self.pricing_cache[key]

    def _groups(self, groups):
        if getattr(self, 'aggregate_tiers', True):
            return groups
        return tuple((weight, np.asarray([tier], dtype=np.intp))
                     for weight, tiers in groups for tier in tiers)

    def _flat_price(self, height, limit, items, cached, gains, kind_type, heights):
        """When even all high cubes fit, count is a tie-break, not a DP resource.

        For each tier and weight retain the greatest value, and on equal value retain
        the fewest high cubes. This is precisely the original DP's final argmax order.
        """
        best = np.full((height+1, limit+1), -np.inf)
        cubes = np.full(best.shape, height+1, dtype=np.int16)
        best[0, 0], cubes[0, 0] = 0., 0
        taken = []
        for i, b in enumerate(items):
            high, weights, groups = cached[b]
            flags = np.zeros(best.shape, dtype=bool)
            updates = []
            for weight, tiers in self._groups(groups):
                candidate = best[tiers, :limit+1-weight]+gains[i, tiers, None]
                hc = cubes[tiers, :limit+1-weight]+high
                destination = best[tiers+1, weight:]
                old_hc = cubes[tiers+1, weight:]
                better = ((candidate > destination+1e-12) |
                          ((candidate == destination) & (hc < old_hc)))
                updates.append((weight, tiers, candidate, hc, better))
            for weight, tiers, candidate, hc, better in updates:
                best[tiers+1, weight:] = np.where(better, candidate, best[tiers+1, weight:])
                cubes[tiers+1, weight:] = np.where(better, hc, cubes[tiers+1, weight:])
                flags[tiers+1, weight:] = better
            taken.append((b, flags, weights))
        out = {}
        for k in (range(1, height+1) if height == max(heights) and len(heights) == 1 else (height,)):
            allowed = int(limit*self.factor[(kind_type, k)])
            view = best[k, :allowed+1]
            if not np.isfinite(view).any():
                continue
            total = float(np.max(view))
            tied = np.flatnonzero(view == total)
            m = int(tied[np.argmin(cubes[k, tied])])
            chosen, at_k, at_m = [], k, m
            for b, flags, weights in reversed(taken):
                if at_k and flags[at_k, at_m]:
                    chosen.append(b)
                    at_k, at_m = at_k-1, at_m-weights[at_k-1]
            out[k] = (total, tuple(reversed(chosen)))
        return out

    def price(self, page_index, value, *, boxes=None, phase=1):
        """Exact ordered-subset DP, updating only reachable slices and batching equal-weight tiers.

        The predecessor tape records the same strict improvements as the original DP. A temporary
        source slice prevents using a box twice even when several tiers update in one operation.
        """
        T, page = self.T, self.pages[page_index]
        kind_type = self.type_of[page_index]
        order = self.order if boxes is None else boxes
        out = {}
        heights = range(1, len(page.slots)+1) if self.oracle is not None and page.deck else (len(page.slots),)
        for height in heights:
            limit, cached = self._pricing(page_index, height)
            items = [b for b in order if b in cached]
            best = np.full((height+1, height+1, limit+1), -np.inf)
            best[0, 0, 0] = 0.
            taken = []
            gains = np.asarray([value[self.row[b]] for b in items])[:, None]*np.ones((1, height))
            if self.lcg_target is not None:
                x = self.p.cells[T.cell_ids[T.slot_cells[page.slots[0], 0]]].lcg_m
                gains += np.asarray([self.lcg_price*float(T.mass[b])/1000.*(x-self.lcg_target)
                                     for b in items])[:, None]
            if phase == 3:
                masses = np.asarray([float(T.mass[b])/1000. for b in items])
                heights2 = np.asarray([self.height_of(page, k)**2/10. for k in range(height)])
                gains -= self.lift_weight*masses[:, None]*heights2[None, :]
                if not page.deck:
                    gains += np.asarray([self.hold_gain(b) for b in items])[:, None]
            if height*2.896 <= page.hmax+1e-9:
                out.update(self._flat_price(height, limit, items, cached, gains, kind_type, heights))
                continue
            for i, b in enumerate(items):
                high, weights, groups = cached[b]
                flags = np.zeros(best.shape, dtype=bool)
                # Materialise every source before writing any destination: a box is used once.
                updates = []
                for weight, tiers in self._groups(groups):
                    source = best[tiers, :height+1-high, :limit+1-weight]
                    candidate = source+gains[i, tiers, None, None]
                    destination = best[tiers+1, high:, weight:]
                    better = candidate > destination+1e-12
                    updates.append((weight, tiers, candidate, better))
                for weight, tiers, candidate, better in updates:
                    destination = best[tiers+1, high:, weight:]
                    np.copyto(destination, candidate, where=better)
                    best[tiers+1, high:, weight:] = destination
                    flags[tiers+1, high:, weight:] = better
                taken.append((b, flags, high, weights))
            for k in (range(1, height+1) if height == len(page.slots) and len(heights) == 1 else (height,)):
                view = best[k].copy()
                for h in range(height+1):
                    if h > k or (k-h)*2.591+h*2.896 > page.hmax+1e-9:
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
        # Incidence never changes. Extend it only for newly generated columns, rather
        # than rebuilding every old nonzero at every LP and every dive decision.
        if self.master_matrix_size < len(self.columns) or self.master_matrix is None:
            ri, ci = [], []
            for index in range(self.master_matrix_size, len(self.columns)):
                c = self.columns[index]
                col_index = index-self.master_matrix_size
                for b in c.boxes:
                    ri.append(self.row[b]); ci.append(col_index)
                for j in self.pages[c.page].stacks:
                    ri.append(len(self.boxes)+self.stack_row[j]); ci.append(col_index)
            extra = csc_matrix((np.ones(len(ri)), (ri, ci)),
                               shape=(len(self.boxes)+len(self.stacks), len(self.columns)-self.master_matrix_size))
            self.master_matrix = extra if self.master_matrix is None else hstack([self.master_matrix, extra], format='csc')
            self.master_matrix_size = len(self.columns)
        selected_rows = [self.row[b] for b in boxes]+[len(self.boxes)+self.stack_row[j] for j in stacks]
        inactive = np.ones(len(self.boxes)+len(self.stacks))
        inactive[selected_rows] = 0.
        # A sparse product filters columns touching an already fixed box or stack.
        compatible = np.asarray(self.master_matrix.T@inactive).ravel() == 0.
        if self.banned:
            compatible[list(self.banned)] = False
        use = np.flatnonzero(compatible).tolist()
        A = self.master_matrix[selected_rows, :][:, use]
        slack = vstack([eye(len(boxes), format='csc'), csc_matrix((len(stacks), len(boxes)))], format='csc')
        A = hstack([A, slack], format='csc')
        cost = [self.column_cost(self.columns[k], phase) for k in use]+[UNCOVERED]*len(boxes)
        col = len(cost)
        if self.lcg_target is not None:
            cost.append(1.)
            col += 1
            A = hstack([A, csc_matrix((n_rows, 1))], format='csc')
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
        # The cold all-slack master has equal duals for every box. Give it diversified,
        # fully checked columns instead of repeatedly pricing the same few identities.
        if not self.columns and phase == 1 and monotonic() < deadline:
            self.seed_chosen = self.first_fit(active, set(active_stacks))
            if self.seed_chosen is None and monotonic() < deadline:
                self.seed_chosen = self.first_fit(active, set(active_stacks), random.Random(0))
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
            if (phase == 1 and uncovered < 1e-7) or monotonic() >= deadline or rounds >= max_rounds:
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
                    remaining = subset
                    # Initial batches use disjoint identities. Later rounds use the exact
                    # original oracle once; this retains its termination test.
                    for batch in range(4 if rounds == 1 and phase == 1 else 1):
                        priced = self.price(pages[0], value, boxes=remaining, phase=phase)
                        if not priced:
                            break
                        for total, boxes in priced.values():
                            fresh = 0
                            for k in pages:
                                sigma = sum(stack_price[j] for j in self.pages[k].stacks)
                                cost = (2.*self.restows(boxes) if phase == 2 else
                                        self.stack_cost*len(self.pages[k].stacks) if phase == 3 and self.pages[k].deck else 0.)
                                if total+sigma-cost > 1e-6 and fresh < 4:
                                    fresh += self.add(k, list(boxes))
                            added += fresh
                        used = set(max(priced.values(), key=lambda item: len(item[1]))[1])
                        remaining = [b for b in remaining if b not in used]
                        if not remaining or monotonic() >= deadline:
                            break
                if monotonic() >= deadline:
                    break
            if added == 0 and self.refused == refused_before:
                last['proved'] = True
                return last

    def integer(self, *, seconds, hold_full, seed, fixed=()):
        """Choose patterns among the pool with CP-SAT: every box once, each stack once, cheapest lifts."""
        T = self.T
        model = cp_model.CpModel()
        x = [model.NewBoolVar(f'x{k}') for k in range(len(self.columns))]
        cover, stack_use = defaultdict(list), defaultdict(list)
        for k, c in enumerate(self.columns):
            for b in c.boxes:
                cover[b].append(x[k])
            for j in self.pages[c.page].stacks:
                stack_use[j].append(x[k])
        for b in self.boxes:
            if not cover[b]:
                return None
            model.AddExactlyOne(cover[b])
        for j, column in stack_use.items():
            if hold_full and not T.stack_deck[j]:
                # Hold full: one pattern using the whole stack, the sufficient form of E6.
                full = [x[k] for k, c in enumerate(self.columns) if j in self.pages[c.page].stacks
                        and len(c.boxes) == len(self.pages[c.page].slots)]
                model.AddExactlyOne(full) if full else model.AddAtMostOne(column)
                for k, c in enumerate(self.columns):
                    if j in self.pages[c.page].stacks and len(c.boxes) != len(self.pages[c.page].slots):
                        model.Add(x[k] == 0)
            else:
                model.AddAtMostOne(column)
        cost = [int(2*c.cost) * x[k] for k, c in enumerate(self.columns)]
        covers = sorted({page.cover for page in self.pages if page.cover is not None})
        late = max(self.ports)
        terms = list(cost)
        for cover_id in covers:
            hold = [k for k, c in enumerate(self.columns)
                    if self.pages[c.page].cover == cover_id and not self.pages[c.page].deck]
            deck = [k for k, c in enumerate(self.columns)
                    if self.pages[c.page].cover == cover_id and self.pages[c.page].deck]
            used = model.NewBoolVar(f'used{cover_id}')
            edge = {}
            for port in self.ports:
                edge[port] = model.NewBoolVar(f'edge{cover_id}{port}')
                carrying = [k for k in hold if any(self.pod[b] == port for b in self.columns[k].boxes)]
                for k in carrying:
                    model.AddImplication(x[k], edge[port])
                model.AddImplication(edge[port], used)
                terms.append(4*edge[port])   # 2 lifts later, doubled for integer coefficients
            terms.append(4*used)
            # A deck box over a hold box that leaves earlier is lifted off and back on.
            over = sum(2*sum(1 for b in self.columns[k].boxes if self.pod[b] == late)*x[k] for k in deck)
            early_hold = sum(edge[port] for port in self.ports if port < late)
            restow = model.NewIntVar(0, 4*len(self.boxes), f'restow{cover_id}')
            model.Add(restow >= 2*over-4*len(self.boxes)*(1-early_hold)) if len(self.ports) == 2 else None
            terms.append(restow)
        model.Minimize(sum(terms))
        for k, c in enumerate(self.columns):
            if k in fixed:
                model.AddHint(x[k], 1)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = seconds
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = seed
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        return dict(status=solver.StatusName(status), objective=solver.ObjectiveValue()/2.,
                    bound=solver.BestObjectiveBound()/2., chosen=[k for k in range(len(x)) if solver.Value(x[k])])

    def first_fit(self, boxes, stacks, rng=None):
        """Greedy end of a dive for when there is room: heaviest first, each box on the fullest stack that still takes it.

        Longs choose their pair first, 20-ft boxes share what is left. Every result passes ``legal`` and enters the pool.
        """
        T = self.T
        free = set(stacks)
        order = [b for b in self.order if b in boxes]
        open_pages = defaultdict(list)     # page -> boxes
        pages = sorted(range(len(self.pages)), key=lambda k: (self.pages[k].deck, -len(self.pages[k].slots), k))
        for wanted in ('long', '20'):
            kept = [b for b in order if (T.units[b] == 1) == (wanted == '20')]
            for b in sorted(kept, key=lambda b: b not in self.special):
                best = None
                for k in pages:
                    page = self.pages[k]
                    if page.kind != wanted or len(open_pages[k]) >= len(page.slots):
                        continue
                    if not open_pages[k] and not set(page.stacks) <= free:
                        continue
                    trial = open_pages[k]+[b]
                    if self.legal(page, trial):
                        room = page.limit_kg-sum(float(T.share[x]) for x in trial)
                        # Heavy boxes go to the hold first (E6); others keep the hold's hatch shut if they can.
                        rank = (page.deck == bool(self.heavy[b]), not open_pages[k], room+(rng.uniform(0, 3000) if rng else 0.))
                        if best is None or rank < best[0]:
                            best = (rank, k)
                if best is None:
                    for page_index, partial in open_pages.items():
                        if partial:
                            self.add(page_index, partial)
                    self.last_refused = (T.box_ids[b], int(T.length[b]), float(T.mass[b]), float(T.height[b]), int(self.static[b].sum()), bool(T.closes_stack[b]), bool(T.is_dg[b]),
                                         len(free), sum(1 for k in open_pages if open_pages[k]))
                    return None
                k = best[1]
                if not open_pages[k]:
                    free -= set(self.pages[k].stacks)
                open_pages[k].append(b)
        out = []
        for k, column in open_pages.items():
            if column:
                self.add(k, column)
                index = self.pool.get((k, tuple(column)))
                if index is None or index in self.banned or not self.lash_ok(self.pages[k], tuple(column)):
                    self.last_refused = ('lashing', k, len(column))
                    return None
                out.append(index)
        return out

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
        # Retain the complete cold-start incumbent instead of spending its remaining
        # budget rediscovering feasibility through fractional fixes.
        if self.seed_chosen is not None:
            seed_boxes, seed_stacks = set(), set()
            valid = True
            for k in self.seed_chosen:
                column = self.columns[k]
                page = self.pages[column.page]
                if (k in self.banned or seed_boxes.intersection(column.boxes) or
                        seed_stacks.intersection(page.stacks) or not self.legal(page, column.boxes) or
                        not self.lash_ok(page, column.boxes)):
                    valid = False
                    break
                seed_boxes.update(column.boxes)
                seed_stacks.update(page.stacks)
            if valid and seed_boxes == boxes:
                return list(self.seed_chosen), dict(left=0, retreats=0, tail='seed_first_fit')
        chosen = []
        retreat = 0
        self.fixed_moment = 0.
        while boxes:
            if len(boxes) <= tail_boxes or not stacks:
                ending = self.first_fit(boxes, stacks, rng)
                if ending is not None:
                    return chosen+ending, dict(left=0, retreats=retreat, tail='first_fit')
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

    def column_fine(self, assignment, slot):
        """Is the whole column holding ``slot`` legal? Used by repairs that swap single boxes."""
        T = self.T
        index = T.slot_index[slot]
        for page_index, page in enumerate(self.pages):
            if index in page.slots:
                by_slot = {T.slot_index[s]: T.box_index[b] for b, s in assignment.items()}
                boxes = tuple(by_slot[s] for s in page.slots if s in by_slot)
                return self.legal(page, boxes, any_port=True) and self.lash_ok(page, boxes)
        return False

    def clear_oog(self, assignment):
        """Swap an out-of-gauge box with a same-length, same-class box until E9 clearance holds. Rules checked exactly."""
        from stowage.checker.rules import placement_violations
        from stowage.model.ship import Placement
        p, T = self.p, self.T
        out = dict(assignment)
        ship = p.instance.onboard
        for b in self.boxes:
            box_id = T.box_ids[b]
            box = p.boxes[box_id]
            if box.oog is None:
                continue

            def clear(table):
                others = [Placement(container_id=k, slot=v) for k, v in table.items() if k != box_id]
                found = placement_violations(p.instance, ship.model_copy(update={'placements': others}), box, table[box_id])
                return not any('e9' in str(v.rule_id) for v in found)
            if clear(out):
                continue
            candidates = sorted((c for c in self.boxes if c != b and T.units[c] == T.units[b] and c not in self.special),
                                key=lambda c: -parse_slot(out[T.box_ids[c]]).tier)
            for c in candidates:
                other = T.box_ids[c]
                trial = dict(out)
                trial[box_id], trial[other] = out[other], out[box_id]
                if self.column_fine(trial, trial[box_id]) and self.column_fine(trial, trial[other]) and clear(trial):
                    out = trial
                    break
        return out

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
