"""A multi-port stowage planner for RCSPP, built the way the published frontier works: master plan, then slots, then ballast.

The master plan (Pacino et al. 2011; Maersk patent EP1909221) decides, across the whole voyage, how many boxes of each cargo group go
to each level of each hatch. It is a CP-SAT model: a constraint solver over integer choices. Its central decision is which ports open
each hatch. A hold can only be worked at a port where its hatch opens, and nothing may stay on deck through such a port, so a deck box
must fit between two openings and a hold box must start and end on one. That is what restow-free means once hatch covers count.

The slot stage turns those counts into positions. Every placement goes through `Stow.place`, which tests the exact rules on every
port the box is aboard, so nothing downstream repairs a plan: a box either fits or stays ashore. Run with no master, the same stage is a
plain greedy planner, which seeds the master and stands as a fallback.

The ballast stage is a linear program per port: the least water that meets trim, GM and bending for the cargo as stowed.
No published code exists for STOW, the planner behind the suite's example plans, so this is an emulation of the method family.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
import time

import numpy as np
from ortools.sat.python import cp_model
from scipy.optimize import linprog

from check_rcspp import OUTSIDE_THE_PLAN, Readings, evaluate, interp
from rcspp_io import ALPHA, BETA, GAMMA, RHO, TRIM_HIGH, TRIM_LOW

WEIGHT_CLASSES = (11., 20.)     # tons per cell
TRIM_MARGIN, GM_MARGIN = .1, .05
LASH_MASTER = .95       # later ports can stack heavy on light, which an even-weight estimate misses
SPILL_COST, OVER_COST = 300, 40     # objective units, 1000 per box: per cell outside a port's own stacks, per ton over the lashing estimate


@dataclass
class Unit:
    """What one cell takes: a 40 foot box, or a pair of 20s sharing load and discharge ports."""
    boxes: tuple
    pol: int
    pod: int

    @property
    def twenty(self):
        return self.boxes[0].twenty

    @property
    def weight(self):
        return sum(b.weight for b in self.boxes)

    @property
    def height(self):
        return max(b.height for b in self.boxes)

    @property
    def reefer(self):
        return any(b.reefer for b in self.boxes)

    @property
    def imdg(self):
        return {b.imdg_type for b in self.boxes if b.imdg}

    @property
    def cls(self):
        return sum(self.weight >= x for x in WEIGHT_CLASSES)

    @property
    def group(self):
        return self.pol, self.pod, self.twenty, self.reefer, self.cls


@dataclass
class Area:
    index: int
    bay: int
    block: int
    hatch: tuple | None
    deck: bool
    stacks: list

    @property
    def cells(self):
        return sum(len(s.tiers) for s in self.stacks)


def units_of(instance):
    """Pair 20s within a trip, heaviest with heaviest. An odd one has no partner and stays ashore."""
    twenties, units, left = defaultdict(list), [], []
    for box in instance.load.values():
        if box.twenty:
            twenties[box.pol, box.pod].append(box)
        else:
            units.append(Unit((box,), box.pol, box.pod))
    for (pol, pod), boxes in twenties.items():
        boxes.sort(key=lambda b: (b.reefer, b.weight), reverse=True)
        plain = [b for b in boxes if not b.imdg]
        dangerous = sorted((b for b in boxes if b.imdg), key=lambda b: (b.imdg_type is None, b.imdg_type))
        while dangerous:        # never two IMDG types in one cell
            one = dangerous.pop()
            if plain:
                units.append(Unit((one, plain.pop()), pol, pod))
            elif dangerous and dangerous[-1].imdg_type == one.imdg_type:
                units.append(Unit((one, dangerous.pop()), pol, pod))
            else:
                left.append(one)
        for a, b in zip(plain[0::2], plain[1::2]):
            units.append(Unit((a, b), pol, pod))
        if len(plain) % 2:
            left.append(plain[-1])
    return units, left


def areas_of(vessel, sides=1):
    """One area per level of each hatch; a bay with no hatch cover has one deck area. `sides` splits a level across the centre line."""
    areas = []
    for bay in vessel.bays:
        groups = defaultdict(list)
        for stack in bay.stacks:
            side = 0 if sides == 1 else int(stack.tp > 0)
            groups[stack.block, stack.hatch, stack.level == 'OnDeck', side].append(stack)
        for (block, hatch, deck, _), stacks in sorted(groups.items(), key=str):
            areas.append(Area(len(areas), bay.name, block, (bay.name, hatch) if hatch is not None else None, deck, stacks))
    return areas


class Stow:
    """The plan under construction. All rules that depend only on positions are exact here; ballast comes later."""

    def __init__(self, vessel, instance, gm_estimate, lash_margin=.995, lash_changed_only=False):
        self.vessel, self.instance = vessel, instance
        self.ports = [c for c, _ in instance.ports]
        self.n = len(self.ports)
        self.crane_limit = dict(instance.ports)
        self.gm = gm_estimate
        self.lash_margin = lash_margin
        self.lash_changed_only = lash_changed_only
        self.stacks = {s.index: s for b in vessel.bays for s in b.stacks}
        self.neighbours = defaultdict(list)
        names = [b.name for b in vessel.bays]
        for a, b in zip(names, names[1:]):
            self.neighbours[a].append(b)
            self.neighbours[b].append(a)
        zero = lambda: [0.]*self.n
        self.level = {i: [0]*self.n for i in self.stacks}       # tiers filled at departure from each port
        self.height = {i: zero() for i in self.stacks}
        self.weight = {i: zero() for i in self.stacks}
        self.moment = {i: zero() for i in self.stacks}
        self.forties = {i: [0]*self.n for i in self.stacks}
        self.cells = {i: defaultdict(list) for i in self.stacks}    # tier index -> [(a, b, unit)]
        self.moves = [Counter() for _ in range(self.n)]
        self.hold_work = defaultdict(Counter)                   # hatch -> port -> hold lifts
        self.deck_stay = defaultdict(lambda: [0]*self.n)        # hatch -> port -> deck boxes aboard through that port
        self.pods = defaultdict(lambda: [Counter() for _ in range(self.n)])     # (bay, block) -> port -> pod counts
        self.mixed = [set() for _ in range(self.n)]
        self.dangerous = defaultdict(lambda: [Counter() for _ in range(self.n)])    # (bay, deck) -> port -> types
        self.positions = {}
        self.placed = []
        self._load_arrivals()

    def _load_arrivals(self):
        """Arrival boxes are given. Rules that bind planning count them, but breaches of theirs are not the plan's."""
        by_cell = defaultdict(list)
        for box in self.instance.arrival.values():
            by_cell[box.position[:3]].append(box)
        for (bay, row, tier), boxes in sorted(by_cell.items(), key=lambda kv: kv[0][2]):
            stack = self.vessel.stack_at(bay, row, tier)
            pods = {b.pod for b in boxes}
            # A cell of two 20s with different discharge ports is split into a unit per box.
            for pod in sorted(pods, reverse=True):
                unit = Unit(tuple(b for b in boxes if b.pod == pod), 0, pod)
                self._commit(unit, stack, tier, 0, pod, arrival=True)

    def _commit(self, unit, stack, tier, a, b, arrival=False):
        i, k = stack.index, (tier-stack.start)//2
        base = self.height[i][a]
        for p in range(a, b):
            if self.level[i][p] <= k:
                self.level[i][p] = k+1
                self.height[i][p] += unit.height
            self.weight[i][p] += unit.weight
            self.moment[i][p] += unit.weight*(base+unit.height/2+GAMMA*stack.vp)
            if not unit.twenty:
                self.forties[i][p] += 1
            self.pods[stack.bay, stack.block][p][unit.pod] += len(unit.boxes)
            if len(self.pods[stack.bay, stack.block][p]) > 1:
                self.mixed[p].add(stack.bay)
            if unit.imdg:
                self.dangerous[stack.bay, stack.level == 'OnDeck'][p].update(unit.imdg)
        self.cells[i][k].append((a, b, unit))
        lifts = 1 if not unit.twenty else len(unit.boxes)
        hatch = (stack.bay, stack.hatch) if stack.hatch is not None else None
        for port in (a, b):
            if port or not arrival:
                self.moves[port][stack.bay] += lifts
        if hatch is not None:
            if stack.level == 'OnDeck':
                for p in range(a+1, b):
                    self.deck_stay[hatch][p] += 1
            else:
                for port in (a, b):
                    if port or not arrival:
                        self.hold_work[hatch][port] += 1
        if not arrival:
            sides = (None,) if not unit.twenty else ('Aft', 'Fore')
            for box, side in zip(unit.boxes, sides):
                self.positions[box.id] = {p: (stack.bay, stack.row, tier, side) for p in range(a, b)}
            self.placed.append((unit, stack, tier))

    def fits(self, unit, stack):
        """Returns the tier a unit would take on this stack, or None. Tests every rule on every port it would be aboard."""
        a, b = unit.pol, unit.pod
        i = stack.index
        level = self.level[i]
        k = level[a]
        tier = stack.start+2*k
        if tier > stack.end or (unit.reefer and tier not in stack.plugs):
            return None
        heights, weights, moments, forties = self.height[i], self.weight[i], self.moment[i], self.forties[i]
        h = unit.height
        if heights[a]+h > stack.max_height+1e-9:
            return None
        below = None
        if k:
            for start, end, other in self.cells[i][k-1]:
                if start <= a < end:
                    below = (end, other)
                    break
            if below is None or below[0] < b:
                return None         # the box beneath would leave first: a restow
        lash = stack.level == 'OnDeck'
        limit = self.vessel.lash_limit*self.lash_margin
        new_moment = unit.weight*(heights[a]+h/2+GAMMA*stack.vp)
        for p in range(a, b):
            if level[p] != k or heights[p]+h > stack.max_height+1e-9 or weights[p]+unit.weight > stack.max_weight:
                return None
            if unit.twenty and forties[p]:
                return None
            if lash and (p == a or not self.lash_changed_only):
                w = weights[p]+unit.weight
                value = self.gm[p]**ALPHA*BETA**((moments[p]+new_moment)/w)*w
                if value > limit:
                    return None
        return tier

    def allowed(self, unit, stack):
        """The rules between stacks: hatches, cranes, the paired block limit and the dangerous goods stand-in."""
        a, b = unit.pol, unit.pod
        lifts = 1 if not unit.twenty else len(unit.boxes)
        for port in (a, b):
            mine = self.moves[port][stack.bay]+lifts
            limit = self.crane_limit[port]
            if any(mine+self.moves[port][other] > limit for other in self.neighbours[stack.bay]):
                return False
        if stack.hatch is not None:
            hatch = stack.bay, stack.hatch
            if stack.level == 'OnDeck':
                if any(self.hold_work[hatch][p] for p in range(a+1, b)):
                    return False
            elif self.deck_stay[hatch][a] or (b < self.n and self.deck_stay[hatch][b]):
                return False
        key = stack.bay, stack.block
        for p in range(a, b):
            pods = self.pods[key][p]
            if pods and unit.pod not in pods and stack.bay not in self.mixed[p] and len(self.mixed[p]) >= self.instance.max_mixed_bays:
                return False
        if unit.imdg:
            state = self.dangerous[stack.bay, stack.level == 'OnDeck']
            if any(set(state[p])-unit.imdg for p in range(a, b)):
                return False
        return True

    def top_pod(self, stack, port):
        """Discharge port of the top unit at this departure, or None for an empty stack."""
        k = self.level[stack.index][port]
        if not k:
            return None
        for start, end, _ in self.cells[stack.index][k-1]:
            if start <= port < end:
                return end
        return None

    def place(self, unit, stacks, early=False):
        """Prefer a stack already bound for this port, then an empty one, then the one whose top leaves soonest after it."""
        best = None
        for stack in stacks:
            tier = self.fits(unit, stack)
            if tier is None or not self.allowed(unit, stack):
                continue
            top = self.top_pod(stack, unit.pol)
            rank = 0 if top == unit.pod else 1 if top is None else 2
            key = (rank, (top or 0)-unit.pod, self.height[stack.index][unit.pol], not unit.twenty and self.forties[stack.index][unit.pol] == 0 and top is not None)
            if best is None or key < best[0]:
                best = key, stack, tier
                if early and rank == 0:
                    break
        if best is None:
            return False
        self._commit(unit, best[1], best[2], unit.pol, unit.pod)
        return True


def usable_tiers(stack):
    """Tiers a stack can really fill: its height limit can stop it short of its top tier once high cubes are in the mix."""
    return min(len(stack.tiers), int(stack.max_height/2.75))


def lash_capacity(stack, gm, limit):
    """Most weight a full deck stack may carry at this GM, assuming even weights up the stack."""
    tiers = usable_tiers(stack)
    hm = GAMMA*stack.vp+tiers*2.7/2
    return min(stack.max_weight, LASH_MASTER*limit/(gm**ALPHA*BETA**hm))


def allocation_of(stow, areas):
    """A plan in the master's own terms, so a slot plan can seed the master."""
    area_of = {s.index: a for a in areas for s in a.stacks}
    allocation = defaultdict(Counter)
    for unit, stack, _ in stow.placed:
        allocation[unit.group][area_of[stack.index].index] += 1
    return allocation


def solve_master(vessel, instance, units, areas, gm_estimate, displacement, budget_s, workers, seed, lash_changed_only=False, log=None, hint=None):
    """Counts of each cargo group per hatch level over the voyage. Everything physical is linear here, at the displacement of the last pass."""
    ports = [c for c, _ in instance.ports]
    n = len(ports)
    groups = defaultdict(list)
    for unit in units:
        groups[unit.group].append(unit)
    for key in groups:
        groups[key].sort(key=lambda u: u.weight, reverse=True)
    pods = sorted({u.pod for u in units})
    model = cp_model.CpModel()
    stow = Stow(vessel, instance, gm_estimate)
    arrival_level = stow.level
    bay_index = {b.name: i for i, b in enumerate(vessel.bays)}
    weight_arrival = np.zeros((n, len(vessel.bays)))
    vertical_arrival = np.zeros(n)
    for i, stack in stow.stacks.items():
        for p in range(n):
            weight_arrival[p, bay_index[stack.bay]] += stow.weight[i][p]
            vertical_arrival[p] += stow.weight[i][p]*stack.vp+stow.moment[i][p]-GAMMA*stow.weight[i][p]*stack.vp

    def arrival_leaves(index, leg):
        return min((end for tier in stow.cells[index].values() for start, end, _ in tier if start <= leg < end), default=n)

    def room(area, leg, pod):
        """Tiers free on top of arrival stacks that leave no earlier than this port."""
        return sum(max(0, usable_tiers(s)-arrival_level[s.index][leg]) for s in area.stacks if arrival_level[s.index][leg] and arrival_leaves(s.index, leg) >= pod)

    def empty_stacks(area, leg):
        return sum(arrival_level[s.index][leg] == 0 for s in area.stacks)

    def free_cells(area, leg):
        return sum(len(s.tiers)-arrival_level[s.index][leg] for s in area.stacks)

    # Which ports open each hatch. A hold box starts and ends on an opening; a deck box can't span one.
    hatches = defaultdict(lambda: {'hold': [], 'deck': []})
    for area in areas:
        if area.hatch is not None:
            hatches[area.hatch]['deck' if area.deck else 'hold'].append(area)
    opens = {}
    for hatch, parts in hatches.items():
        if not parts['hold']:
            continue
        for port in range(1, n):
            opens[hatch, port] = model.NewBoolVar('')
            if stow.hold_work[hatch][port]:
                model.Add(opens[hatch, port] == 1)
            elif stow.deck_stay[hatch][port]:
                model.Add(opens[hatch, port] == 0)

    x = {}
    for key, members in groups.items():
        pol, pod, twenty, reefer, cls = key
        for area in areas:
            if reefer and not any(s.plugs for s in area.stacks):
                continue
            has_hold = area.hatch is not None and (area.hatch, 1) in opens
            if area.deck is False and pol and has_hold and stow.deck_stay[area.hatch][pol]:
                continue
            if area.deck and has_hold and any(stow.hold_work[area.hatch][p] for p in range(pol+1, pod)):
                continue
            cap = min(free_cells(area, leg) for leg in range(pol, pod))
            if cap <= 0:
                continue
            var = model.NewIntVar(0, min(cap, len(members)), '')
            x[key, area.index] = var

    per_trip = defaultdict(list)
    for (key, index), var in x.items():
        per_trip[index, key[0], key[1]].append(var)
    for (index, pol, pod), variables in per_trip.items():
        area = areas[index]
        if area.hatch is None or (area.hatch, 1) not in opens:
            continue
        total = sum(variables)
        cap = min(area.cells, sum(len(groups[k]) for k in groups if k[0] == pol and k[1] == pod))
        if area.deck:
            for port in range(pol+1, pod):
                model.Add(total <= cap*(1-opens[area.hatch, port]))
        else:
            model.Add(total <= cap*opens[area.hatch, pol])
            if pod < n:
                model.Add(total <= cap*opens[area.hatch, pod])

    for key, members in groups.items():
        terms = [x[key, a.index] for a in areas if (key, a.index) in x]
        if terms:
            model.Add(sum(terms) <= len(members))

    # Cells, plugs, stack weights and lashing, by leg. Stacks are dedicated to a discharge port once used, which is what keeps them restow-free.
    columns = {}
    soft = []
    for area in areas:
        keys = [k for k in groups if (k, area.index) in x]
        height = max(1, round(sum(usable_tiers(s) for s in area.stacks)/len(area.stacks)))
        for leg in range(n-1):
            aboard = [k for k in keys if k[0] <= leg < k[1]]
            if not aboard:
                continue
            model.Add(sum(x[k, area.index] for k in aboard) <= free_cells(area, leg))
            columns_here = []
            for pod in {k[1] for k in aboard}:
                c = model.NewIntVar(0, len(area.stacks), '')
                columns[area.index, leg, pod] = c
                columns_here.append(c)
                spill = model.NewIntVar(0, area.cells, '')     # a stack may mix ports if they nest, so this is a preference, priced below a box
                soft.append((SPILL_COST, spill))
                model.Add(sum(x[k, area.index] for k in aboard if k[1] == pod) <= height*c+room(area, leg, pod)+spill)
                if (area.index, leg-1, pod) in columns:
                    model.Add(c >= columns[area.index, leg-1, pod])
            model.Add(sum(columns_here) <= empty_stacks(area, leg))
            cold = [k for k in aboard if k[3]]
            if cold:
                model.Add(sum(x[k, area.index] for k in cold) <= sum(len(s.plugs & set(s.tiers)) for s in area.stacks))
            mean = {k: sum(u.weight for u in groups[k])/len(groups[k]) for k in aboard}
            total = sum(int(round(mean[k]))*x[k, area.index] for k in aboard)
            if area.deck:
                cap = sum(max(0., lash_capacity(s, gm_estimate[leg], vessel.lash_limit)-stow.weight[s.index][leg]) for s in area.stacks)
                over = model.NewIntVar(0, 10**5, '')           # the estimate is rough; the slot stage tests the real lashing value
                soft.append((OVER_COST, over))
                model.Add(total <= int(cap)+over)
            else:
                cap = sum(max(0., s.max_weight-stow.weight[s.index][leg]) for s in area.stacks)
                model.Add(total <= int(cap))

    # Long crane per pair of adjacent bays
    bays = [b.name for b in vessel.bays]
    limits = dict(instance.ports)
    for port in range(1, n):
        for one, two in zip(bays, bays[1:]):
            terms, fixed = [], stow.moves[port][one]+stow.moves[port][two]
            for area in areas:
                if area.bay in (one, two):
                    for k in groups:
                        if (k[0] == port or k[1] == port) and (k, area.index) in x:
                            terms.append((2 if k[2] else 1)*x[k, area.index])
            if terms:
                model.Add(sum(terms) <= max(0, limits[port]-fixed))

    # Paired block limit: a block carrying two ports marks its bay as mixed for that leg
    carries = {}
    blocks = defaultdict(list)
    for area in areas:
        blocks[area.bay, area.block].append(area)
    if instance.max_mixed_bays < len({a.bay for a in areas}):
        for leg in range(n-1):
            flags = {}
            for (bay, block), parts in blocks.items():
                flag = flags.setdefault(bay, model.NewBoolVar(''))
                used = []
                aboard = stow.pods[bay, block][leg]
                for pod in pods:
                    if pod <= leg or pod in aboard:     # more cargo for a port already in the block changes nothing
                        continue
                    terms = [x[k, a.index] for a in parts for k in groups if k[0] <= leg < k[1] and k[1] == pod and (k, a.index) in x]
                    if not terms:
                        continue
                    carries[bay, block, leg, pod] = model.NewBoolVar('')
                    model.Add(sum(terms) <= sum(a.cells for a in parts)*carries[bay, block, leg, pod])
                    used.append(carries[bay, block, leg, pod])
                given = len(aboard)
                if used:
                    model.Add(sum(used)+given <= 1+(len(pods)+given)*flag)
            model.Add(sum(flags.values()) <= max(instance.max_mixed_bays, len(stow.mixed[leg])))

    # Trim, GM, bending and displacement at departure, with water in the tanks as integer tons
    tanks = [(bay_index[b.name], t) for b in vessel.bays for t in b.tanks]
    water = {}
    scale = 10
    slacks = []

    def slack():
        """Arrival cargo can leave a port beyond any plan's reach, so physics bends here at a steep price; the checker still judges the result."""
        var = model.NewIntVar(0, 10**6, '')
        slacks.append(var)
        return 1000*var
    for port in range(1, n):
        leg = port
        d = displacement[port]
        lcb, tf, kmt = interp(vessel.hydrostatics, 1, d), interp(vessel.hydrostatics, 3, d), interp(vessel.hydrostatics, 2, d)
        terms_vertical = []
        by_bay = defaultdict(list)
        for area in areas:
            bay = bay_index[area.bay]
            vertical = sum(s.vp+len(s.tiers)*2.7/2 for s in area.stacks)/len(area.stacks)+(1.5 if area.deck else 0.)
            for k in groups:
                if k[0] <= leg < k[1] and (k, area.index) in x:
                    w = int(round(sum(u.weight for u in groups[k])/len(groups[k])))
                    by_bay[bay].append((w, x[k, area.index]))
                    terms_vertical.append((w, vertical, x[k, area.index]))
        for t, tank in enumerate(tanks):
            water[port, t] = model.NewIntVar(0, int(tank[1].capacity), '')
        light = np.array([b.light_weight for b in vessel.bays])+weight_arrival[leg]
        total = sum(w*v for parts in by_bay.values() for w, v in parts)+sum(water[port, t] for t in range(len(tanks)))+int(round(light.sum()))
        model.Add(total-slack() <= int(vessel.max_displacement))
        high, low = TRIM_HIGH-TRIM_MARGIN, TRIM_LOW+TRIM_MARGIN

        def arm(mid, shift):
            return int(round(scale*(lcb-mid-shift/tf)))
        for shift, sense in ((high, -1), (low, 1)):
            expression = sum(arm(vessel.bays[b].mid, shift)*w*v for b, parts in by_bay.items() for w, v in parts)
            expression += sum(arm(vessel.bays[tanks[t][0]].mid, shift)*water[port, t] for t in range(len(tanks)))
            fixed = int(round(sum(scale*(lcb-vessel.bays[b].mid-shift/tf)*light[b] for b in range(len(light)))))
            if sense < 0:
                model.Add(expression+fixed-slack() <= 0)
            else:
                model.Add(expression+fixed+slack() >= 0)

        # GM: keep the mean height of everything aboard below KMT less the limit
        ceiling = kmt-vessel.gm_limit-GM_MARGIN
        expression = sum(int(round(scale*(ceiling-vertical)))*w*v for w, vertical, v in terms_vertical)
        expression += sum(int(round(scale*(ceiling-(t[1].vp_empty+t[1].vp_full)/2)))*water[port, i] for i, t in enumerate(tanks))
        fixed = sum(b.light_weight*(ceiling-b.light_vp) for b in vessel.bays)*scale+scale*(ceiling*weight_arrival[leg].sum()-vertical_arrival[leg])
        model.Add(expression+int(round(fixed))+slack() >= 0)

        # Bending at the bays that have limits
        for i, bay in enumerate(vessel.bays):
            if bay.sagging is None or (bay.sagging == 0 and bay.hogging == 0):
                continue
            expression, fixed = [], 0.
            for f in range(i):
                arm_f = vessel.bays[f].mid-bay.fore
                fixed += arm_f*(light[f]-interp(vessel.bays[f].bonjean, 1, d)*RHO)
                expression += [int(round(arm_f))*w*v for w, v in by_bay.get(f, [])]
                expression += [int(round(arm_f))*water[port, t] for t in range(len(tanks)) if tanks[t][0] == f]
            if expression:
                model.Add(sum(expression)+int(round(fixed))-slack() <= int(.97*bay.hogging))
                model.Add(sum(expression)+int(round(fixed))+slack() >= int(.97*bay.sagging))

    # 20 per point against 1 per ton of water, as in (1): theta is 0.05. Points are the instance's own, 100 for a reefer or dangerous box and 50 otherwise.
    value = {k: round(20*sum(b.points for u in members for b in u.boxes)/len(members)) for k, members in groups.items()}
    goal = [value[k]*var for (k, a), var in x.items()]
    goal += [-var for var in water.values()]
    goal += [-200*var for var in slacks]
    goal += [-cost*var for cost, var in soft]
    model.Maximize(sum(goal))

    started = time.time()
    if hint:
        for (key, index), var in x.items():
            model.AddHint(var, hint['allocation'].get(key, {}).get(index, 0))
        worked = hint['stow'].hold_work
        for (hatch, port), var in opens.items():
            model.AddHint(var, int(bool(worked[hatch][port])))
        for (bay, block, leg, pod), var in carries.items():
            model.AddHint(var, int(pod in hint['stow'].pods[bay, block][leg]))
        # Fix the cargo choices and let the solver work out the rest: stacks per port, water, slack. The result is a complete feasible start, which the
        # search can't lose. Hinting those too would over-constrain it: they follow from the cargo, and a rounded value can make a good plan look infeasible.
        first = cp_model.CpSolver()
        first.parameters.fix_variables_to_their_hinted_value = True
        first.parameters.max_time_in_seconds = min(60., budget_s/3)
        first.parameters.num_workers = workers
        outcome = first.Solve(model)
        if outcome in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            variables = [model.GetIntVarFromProtoIndex(i) for i in range(len(model.Proto().variables))]
            values = [first.Value(v) for v in variables]
            model.ClearHints()
            for var, value in zip(variables, values):
                model.AddHint(var, value)
            if log:
                log(f'master start accepted, objective {first.ObjectiveValue():.0f}')
        elif log:
            log(f'master start not accepted: {first.StatusName(outcome)} in {first.WallTime():.0f} s')

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(10., budget_s-(time.time()-started))
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = seed
    status = solver.Solve(model)
    name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if log:
            log(f'master {name} {solver.WallTime():.0f} s')
        return None
    if log:
        boxes = sum((2 if k[2] else 1)*solver.Value(var) for (k, _), var in x.items())
        log(f'master {name} {solver.WallTime():.0f} s obj {solver.ObjectiveValue():.0f} bound {solver.BestObjectiveBound():.0f} vars {len(model.Proto().variables)} '
            f'boxes {boxes} water {sum(solver.Value(v) for v in water.values())} slack {sum(solver.Value(v) for v in slacks)}')
    allocation = defaultdict(Counter)
    for (key, index), var in x.items():
        value = solver.Value(var)
        if value:
            allocation[key][index] = value
    return {'allocation': allocation, 'status': name, 'bound': solver.BestObjectiveBound(), 'objective': solver.ObjectiveValue()}


def stow_cargo(vessel, instance, units, areas, allocation, gm_estimate, lash_margin=.995, lash_changed_only=False, banned=frozenset()):
    """Ports in order; inside a port, reefers first (they need plugs), then pairs of 20s (they can't sit on 40s), then heaviest first."""
    stow = Stow(vessel, instance, gm_estimate, lash_margin, lash_changed_only)
    groups = defaultdict(list)
    for unit in units:
        if unit.boxes[0].id not in banned:
            groups[unit.group].append(unit)
    for members in groups.values():
        members.sort(key=lambda u: u.weight, reverse=True)
    by_port = defaultdict(list)
    for key in groups:
        by_port[key[0]].append(key)
    left = []
    every = [s for a in areas for s in a.stacks]
    for port in sorted(by_port):
        queue = []
        for key in by_port[port]:
            members = list(groups[key])
            spots = sorted(allocation.get(key, {}).items(), key=lambda kv: areas[kv[0]].deck)   # the hold takes the heaviest of a group
            for index, count in spots:
                for unit in members[:count]:
                    queue.append((unit, areas[index]))
                members = members[count:]
            queue += [(unit, None) for unit in members]
        queue.sort(key=lambda t: (t[1] is None, -t[0].pod, not t[0].reefer, not t[0].twenty, -t[0].weight))
        missed = [unit for unit, area in queue if area is None or not stow.place(unit, area.stacks)]
        left += [unit for unit in missed if not stow.place(unit, every, early=True)]    # after the port's planned cargo, so they take nobody's cell
    return stow, left


def fill_in(stow, left, stacks):
    """Second chance for boxes left ashore, far discharge ports first since they are hardest to place late."""
    still = []
    for unit in sorted(left, key=lambda u: (u.pol, -u.pod, -u.weight)):
        if not stow.place(unit, stacks, early=True):
            still.append(unit)
    return still


def departure_weights(stow):
    """Cargo tons per bay and vertical moment at each departure, from the exact layout."""
    vessel = stow.vessel
    names = [b.name for b in vessel.bays]
    bay_index = {n: i for i, n in enumerate(names)}
    weights = np.zeros((stow.n, len(names)))
    vertical = np.zeros(stow.n)
    for i, stack in stow.stacks.items():
        for p in range(stow.n):
            w = stow.weight[i][p]
            if w:
                weights[p, bay_index[stack.bay]] += w
                vertical[p] += stow.moment[i][p]+(1-GAMMA)*stack.vp*w
    return weights, vertical


def ballast(vessel, weights, vertical, ports, margin_trim=.02, margin_gm=.02):
    """Least water per port meeting trim, GM and bending, as a linear program redone as displacement settles."""
    tanks = [(i, t) for i, b in enumerate(vessel.bays) for t in b.tanks]
    plan, failed = {}, []
    mids = np.array([b.mid for b in vessel.bays])
    light = np.array([b.light_weight for b in vessel.bays])
    light_v = float(sum(b.light_weight*b.light_vp for b in vessel.bays))
    for port in ports[1:]:
        cargo = weights[port]+light
        d = float(cargo.sum())
        fill = None
        for _ in range(5):
            lcb, tf, kmt = (interp(vessel.hydrostatics, c, d) for c in (1, 3, 2))
            mids_t = np.array([mids[i] for i, _ in tanks])
            caps = [(0., t.capacity) for _, t in tanks]
            high, low = (TRIM_HIGH-margin_trim)/tf, (TRIM_LOW+margin_trim)/tf
            base_m = float((mids*cargo).sum())
            base_d = float(cargo.sum())
            A, b = [], []
            # lcb - lcg <= high and >= low, multiplied through by displacement
            A.append(list(lcb-mids_t-high))
            b.append(-(lcb*base_d-base_m-high*base_d))
            A.append(list(-(lcb-mids_t-low)))
            b.append(lcb*base_d-base_m-low*base_d)
            vp = np.array([t.vp_full for _, t in tanks])    # the height of a full tank: a safe bound, since a tank's fill can jump between solves
            ceiling = kmt-vessel.gm_limit-margin_gm
            A.append(list(vp-ceiling))
            b.append(ceiling*base_d-(vertical[port]+light_v))
            for i, bay in enumerate(vessel.bays):
                if bay.sagging is None or (bay.sagging == 0 and bay.hogging == 0):
                    continue
                arms = mids[:i]-bay.fore
                fixed = float(sum(arms[f]*(cargo[f]-interp(vessel.bays[f].bonjean, 1, d)*RHO) for f in range(i)))
                row = [float(arms[t_b]) if t_b < i else 0. for t_b, _ in tanks]
                A.append(row)
                b.append(.995*bay.hogging-fixed)
                A.append([-v for v in row])
                b.append(-.995*bay.sagging+fixed)
            A.append([1.]*len(tanks))
            b.append(vessel.max_displacement-base_d)
            result = linprog(np.ones(len(tanks)), A_ub=np.array(A), b_ub=np.array(b), bounds=caps, method='highs')
            if result.status != 0:
                fill = None
                break
            fill = result.x
            d = base_d+float(fill.sum())
        if fill is None:
            failed.append(port)
            plan[port] = {t.name: 0. for _, t in tanks}
        else:
            plan[port] = {t.name: float(100*min(1., f/t.capacity)) for (_, t), f in zip(tanks, fill)}
    return plan, failed


def estimate_gm(vessel, stow, tanks_plan):
    """GM at each departure for the layout as it stands. Used only to set the lashing limit of the next pass."""
    weights, vertical = departure_weights(stow)
    gm, disp = [2.5]*stow.n, [0.]*stow.n
    light = sum(b.light_weight for b in vessel.bays)
    light_v = sum(b.light_weight*b.light_vp for b in vessel.bays)
    named = {t.name: t for b in vessel.bays for t in b.tanks}
    for p in range(stow.n):
        water = moment = 0.
        for name, percent in tanks_plan.get(p, {}).items():
            tank = named[name]
            y = percent/100*tank.capacity
            water += y
            moment += y*(tank.vp_empty+percent/100*(tank.vp_full-tank.vp_empty))
        d = float(weights[p].sum())+light+water
        gm[p] = interp(vessel.hydrostatics, 2, d)-(float(vertical[p])+light_v+moment)/d
        disp[p] = d
    return gm, disp


def worst_lifted(stow, port, count):
    """The units whose removal lowers the ship's centre of gravity most at this departure: heavy boxes high on deck."""
    aboard = [(unit.weight*(stack.vp+(tier-stack.start)*1.35), unit) for unit, stack, tier in stow.placed if unit.pol <= port < unit.pod and stack.level == 'OnDeck']
    aboard.sort(key=lambda t: t[0], reverse=True)
    return [unit for _, unit in aboard[:count]]


def settle(vessel, instance, units, areas, allocation, gm, every, lash_changed_only, rounds=14):
    """Stow, ballast, and stow again at the GM that layout really has, until the checker finds nothing. Lashing depends on GM, which depends on
    the stowage, so a slot plan can only be judged after its ballast is known. If a departure still fails, leave ashore the fewest boxes that
    clear it, which is what RCSPP lets a plan do. Slots are cheap, so this loop is the place to settle it."""
    readings = Readings(lash_changed_only=lash_changed_only)
    ports = [c for c, _ in instance.ports]
    banned, best = set(), None
    for _ in range(rounds):
        stow, left = stow_cargo(vessel, instance, units, areas, allocation, gm, lash_changed_only=lash_changed_only, banned=banned)
        left = fill_in(stow, left, every)
        weights, vertical = departure_weights(stow)
        tanks, failed = ballast(vessel, weights, vertical, ports)
        real_gm, real_d = estimate_gm(vessel, stow, tanks)
        faults, measures = evaluate(vessel, instance, full_positions(stow, instance), tanks, readings)
        blocking = sum(v for k, v in faults.items() if k not in OUTSIDE_THE_PLAN)
        loaded = sum(len(u.boxes) for u, _, _ in stow.placed)
        record = {'stow': stow, 'tanks': tanks, 'failed': failed, 'loaded': loaded, 'blocking': blocking, 'faults': dict(faults), 'gm': real_gm, 'd': real_d,
                  'left_ashore': len(banned)}
        if best is None or (blocking, -loaded) < (best['blocking'], -best['loaded']):
            best = record
        if not blocking:
            break
        gm = [max(a, b*1.01) for a, b in zip(gm, real_gm)]
        bad = set(failed)
        for port, x in measures['ports'].items():
            if x['gm'] < vessel.gm_limit or abs(x['trim']) > TRIM_HIGH or x['displacement'] > vessel.max_displacement or x['bending'] > 1:
                bad.add(port)
        for port in bad:
            banned.update(unit.boxes[0].id for unit in worst_lifted(stow, port, 3))
    return best


def plan(vessel, instance, budget_s=240., passes=3, workers=4, seed=0, sides=1, lash_changed_only=False, log=None):
    """Greedy slots first, which seed the master; then master, slots and ballast again at the GM and displacement the last pass really had.
    Keeps the cleanest plan that loads most, so the master can only improve on the greedy plan."""
    started = time.time()
    units, odd = units_of(instance)
    areas = areas_of(vessel, sides)
    every = [s for a in areas for s in a.stacks]
    rounds = max(3, min(14, 20000//len(units)))     # a round on the largest ship takes minutes
    best = settle(vessel, instance, units, areas, {}, [3.]*len(instance.ports), every, lash_changed_only, rounds)
    again = settle(vessel, instance, units, areas, {}, [max(g*1.02, .8) for g in best['gm']], every, lash_changed_only, rounds)  # at the real GM, not a guess
    if (again['blocking'], -again['loaded']) < (best['blocking'], -best['loaded']):
        best = again
    best.update(master=None, **{'pass': 'greedy'})
    if log:
        log(f'greedy: placed {best["loaded"]}, blocking faults {best["blocking"]}, {best["faults"]}, {time.time()-started:.0f} s')
    gm, displacement = [max(g*1.02, .8) for g in best['gm']], best['d']
    seed_plan = best
    for number in range(passes):
        hint = {'allocation': allocation_of(seed_plan['stow'], areas), 'stow': seed_plan['stow'], 'tanks': seed_plan['tanks']}
        master = solve_master(vessel, instance, units, areas, gm, displacement, budget_s/passes, workers, seed+number, lash_changed_only, log, hint)
        if master is None:
            continue
        record = settle(vessel, instance, units, areas, master['allocation'], gm, every, lash_changed_only, rounds)
        record.update(master=master, **{'pass': number})
        if log:
            log(f'pass {number}: master {master["status"]}, placed {record["loaded"]}, blocking faults {record["blocking"]}, '
                f'ballast failed {record["failed"]}, {time.time()-started:.0f} s')
        if (record['blocking'], -record['loaded']) < (best['blocking'], -best['loaded']):
            best = record
        seed_plan = best
        gm = [max(g*1.02, .8) for g in record['gm']]
        displacement = record['d']
    return best, odd


def full_positions(stow, instance):
    """The plan in the published file's shape: for each box aboard, its position at every departure."""
    positions = dict(stow.positions)
    for box in instance.arrival.values():
        positions[box.id] = {p: box.position for p in range(box.pol, box.pod)}
    return positions
