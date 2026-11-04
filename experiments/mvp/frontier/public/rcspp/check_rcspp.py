"""An evaluator for the RCSPP model (Sivertsen, Reinhardt, Jensen 2025; RCSPP_model.pdf), written from the suite's documents.

It replays a plan port by port and reports every rule separately, with numbered equations from the model. It knows nothing about
any planner and reads only box positions and tank levels. Readings the documents leave open are named in READINGS and tested
against the 73 published plans (`replay.py`), rather than assumed.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import math

from rcspp_io import ALPHA, BETA, ETA, GAMMA, RHO, THETA, TRIM_HIGH, TRIM_LOW


def interp(table, column, x):
    """Piecewise linear in displacement, flat past the ends (the documents give no extrapolation)."""
    xs = [row[0] for row in table]
    if x <= xs[0]:
        return table[0][column]
    if x >= xs[-1]:
        return table[-1][column]
    i = bisect_right(xs, x)
    a, b = table[i-1], table[i]
    return a[column]+(x-a[0])*(b[column]-a[column])/(b[0]-a[0])


# Breaches a plan can't cause or cure: boxes it may not move, or a label swap between a pair of 20s. Everything else is the plan's. Lashing on a stack of
# arrival boxes is deliberately not here: its value depends on the plan's GM, so it is reported as its own line and never counted as cleared.
OUTSIDE_THE_PLAN = {'(13) arrival side differs', 'given: arrival reefer without a plug', 'given: arrival stack height', 'given: arrival stack weight',
                    'arrival-only stack lashing'}


@dataclass
class Readings:
    """Readings the documents leave open. See VALIDATION.md, Readings, for the evidence on each."""
    forty_moves: int = 1            # one lift per 40 foot box in (35); no published plan breaks it, and counting the model's two halves breaks 551
    lonely_twenty_ok: bool = False  # (18)-(19) as printed: 20s always in pairs. No published plan has a lone 20
    lash_changed_only: bool = False # True tests (29) only on stacks changed at that port. Off, as printed; 72 of 73 published plans break it that way
    tol: float = 1e-6


def cells(box, position):
    """The half-cells a box fills: a 40 fills both sides of (bay, row, tier)."""
    bay, row, tier, side = position
    return [(bay, row, tier, 'Aft'), (bay, row, tier, 'Fore')] if not box.twenty else [(bay, row, tier, side)]


def layout(vessel, boxes, positions, port):
    """Everything aboard at departure from a port, grouped by stack."""
    stacks = defaultdict(dict)
    faults = Counter()
    for box_id, where in positions.items():
        if port not in where:
            continue
        box = boxes[box_id]
        bay, row, tier, side = where[port]
        stack = vessel.stack_at(bay, row, tier)
        if stack is None or tier not in stack.tiers:
            faults['position outside the vessel'] += 1
            continue
        if box.twenty != (side in ('Aft', 'Fore')):
            faults['side does not match box size'] += 1
            continue
        for cell in cells(box, where[port]):
            key = cell[2], cell[3]
            if key in stacks[stack.index, stack.bay]:
                faults['(15) two boxes in one slot'] += 1
            stacks[stack.index, stack.bay][key] = box
    return stacks, faults


def untouched(boxes, instance, positions, port):
    """True when every box here is an arrival box still in its arrival cell. Who is in a stack isn't enough: a plan can move arrival boxes."""
    return all(i in instance.arrival and positions[i].get(port, (None,)*3)[:3] == instance.arrival[i].position[:3] for i in boxes)


def stack_columns(stack_cells):
    """Tiers bottom-up, each with its aft and fore occupant."""
    tiers = sorted({tier for tier, _ in stack_cells})
    return [(tier, stack_cells.get((tier, 'Aft')), stack_cells.get((tier, 'Fore'))) for tier in tiers]


def evaluate(vessel, instance, plan_positions, plan_tanks, readings=None):
    """Returns (violations, measures). violations counts each breach by rule; measures holds objective terms and per-port physics."""
    readings = readings or Readings()
    boxes = {**instance.load, **instance.arrival}
    faults = Counter()
    ports = [code for code, _ in instance.ports]
    long_crane = dict(instance.ports)
    index = {s.index: s for bay in vessel.bays for s in bay.stacks}
    tanks = {t.name: (bay, t) for bay in vessel.bays for t in bay.tanks}
    position = {int(i): {int(p): tuple(at) for p, at in w.items()} for i, w in plan_positions.items()}     # a JSON round trip turns keys into strings
    plan_tanks = {int(p): levels for p, levels in plan_tanks.items()}
    unknown = [i for i in position if i not in boxes]
    if unknown:
        faults['unknown box'] += len(unknown)
    for i in unknown:
        del position[i]

    # Which boxes appear, and (13), (20), (21)
    for box_id, where in position.items():
        box = boxes[box_id]
        due = {p for p in ports if box.pol <= p < box.pod}
        if set(where) != due:
            faults['(20)(21) aboard exactly from load to discharge port'] += 1
        if box_id in instance.arrival:
            if where.get(ports[0]) != box.position:
                same_cell = where.get(ports[0], (None,)*3)[:3] == box.position[:3]
                faults['(13) arrival position differs' if not same_cell else '(13) arrival side differs'] += 1
    missing = [i for i in instance.arrival if i not in position]
    if missing:
        faults['(16) arrival box missing from plan'] += len(missing)

    measures = {'ports': {}, 'loaded': sum(i in instance.load for i in position), 'moves': 0, 'restows': 0, 'hatch_restows': 0}
    before, fault0 = layout(vessel, boxes, position, ports[0])
    faults.update(fault0)
    ballast_total = 0.

    for previous, port in zip(ports, ports[1:]):
        now, fault = layout(vessel, boxes, position, port)
        faults.update(fault)

        # Moves at this port, stack by stack: (30)-(34). A box that keeps its slot above a moved slot is lifted off and put back.
        moves_by_bay = Counter()
        moved_cells = defaultdict(set)
        restowed = set()
        for key in set(before) | set(now):
            a, b = before.get(key, {}), now.get(key, {})
            for cell in set(a) | set(b):
                if a.get(cell) is not b.get(cell):
                    moved_cells[key].add(cell)
        leaving, arriving = [], []
        for key, cells_moved in moved_cells.items():
            a, b = before.get(key, {}), now.get(key, {})
            lowest = min(tier for tier, _ in cells_moved)
            leaving += list({a[cell].id: key for cell in cells_moved if cell in a}.items())
            arriving += list({b[cell].id: key for cell in cells_moved if cell in b}.items())
            for cell, box in b.items():
                if a.get(cell) is box and cell[0] > lowest and cell not in cells_moved:
                    restowed.add((box.id, key))
        for box_id, key in leaving+arriving:    # a box shifted inside one stack is two lifts
            box = boxes[box_id]
            moves_by_bay[key[1]] += readings.forty_moves if not box.twenty else 1
        for box_id, key in restowed:
            box = boxes[box_id]
            moves_by_bay[key[1]] += 2*(readings.forty_moves if not box.twenty else 1)
        moved_boxes = {box_id for box_id, _ in leaving+arriving}
        changed_slot = {box_id for box_id in moved_boxes
                        if boxes[box_id].pol < port < boxes[box_id].pod}
        measures['restows'] += len(changed_slot)+len({box_id for box_id, _ in restowed})
        measures['moves'] += len(moved_boxes)+len(changed_slot)+2*len({box_id for box_id, _ in restowed})

        # Hatch restows: a deck box staying through a port where the hold under its cover is worked. (33)'s "slots beneath" reads as covering
        # this, since the vessel files name each stack's cover, and no published plan has one. Counted as restows in the objective below.
        worked_hatches = {index[k[0]].hatch for k, cs in moved_cells.items() if index[k[0]].level != 'OnDeck' and index[k[0]].hatch is not None}
        hatch_lifted = {box.id: k[1] for k, cs in now.items() if index[k[0]].level == 'OnDeck' and index[k[0]].hatch in worked_hatches
                        for cell, box in cs.items() if before.get(k, {}).get(cell) is box}
        measures['hatch_restows'] += len(hatch_lifted)
        measures['restows'] += len(hatch_lifted)
        for box_id, bay in hatch_lifted.items():
            moves_by_bay[bay] += 2*(readings.forty_moves if not boxes[box_id].twenty else 1)

        # Long crane (35): any two adjacent bays
        limit = long_crane[port]
        names = [b.name for b in vessel.bays]
        worst = max((moves_by_bay[a]+moves_by_bay[b] for a, b in zip(names, names[1:])), default=0)
        if worst > limit:
            faults['(35) long crane'] += 1

        # Stacking, per stack
        for key, stack_cells in now.items():
            stack = index[key[0]]
            column = stack_columns(stack_cells)
            weight, height = 0., 0.
            seen = set()
            forty_below = False
            for depth, (tier, aft, fore) in enumerate(column):
                if tier != stack.start+2*depth:
                    faults['(22) gap below a box'] += 1
                for box in (aft, fore):
                    if box is not None and box.id not in seen:
                        seen.add(box.id)
                        weight += box.weight
                        if box.reefer and tier not in stack.plugs:
                            faults['given: arrival reefer without a plug' if untouched({box.id}, instance, position, port) else '(26) reefer without a plug'] += 1
                if (aft is None) != (fore is None) and not readings.lonely_twenty_ok:
                    faults['(18)(19) lone 20'] += 1
                if aft is not None and fore is not None and aft is not fore and (aft.pol, aft.pod) != (fore.pol, fore.pod):
                    faults['(18)(19) paired 20s differ in load or discharge port'] += 1
                if forty_below and any(b is not None and b.twenty for b in (aft, fore)):
                    faults['(23) 20 on a 40'] += 1
                forty_below = forty_below or any(b is not None and not b.twenty for b in (aft, fore))
                height += max(b.height for b in (aft, fore) if b is not None)
            given = untouched(seen, instance, position, port)
            if height > stack.max_height+readings.tol:
                faults['given: arrival stack height' if given else '(24) stack height'] += 1
            if weight > stack.max_weight+readings.tol:
                faults['given: arrival stack weight' if given else '(25) stack weight'] += 1

        # Weight, trim, GM, bending at departure: (2)-(12)
        tank_state = plan_tanks.get(port, {})
        weight_of_bay = {b.name: b.light_weight for b in vessel.bays}
        vertical = sum(b.light_weight*b.light_vp for b in vessel.bays)
        ballast = 0.
        for name, percent in tank_state.items():
            if name not in tanks:
                faults['unknown tank'] += 1
                continue
            bay, tank = tanks[name]
            if not -1e-6 <= percent <= 100+1e-6:
                faults['(2) tank outside 0 to 100 %'] += 1
            y = percent/100*tank.capacity
            ballast += y
            weight_of_bay[bay.name] += y
            vertical += y*(tank.vp_empty+percent/100*(tank.vp_full-tank.vp_empty))
        ballast_total += ballast

        lash_inputs = []
        for key, stack_cells in now.items():
            stack = index[key[0]]
            height = 0.
            moment_gm = moment_lash = weight = 0.
            for tier, aft, fore in stack_columns(stack_cells):
                occupants = [aft, fore] if aft is not fore else [aft]
                base = height
                for box in occupants:
                    if box is None:
                        continue
                    mid_height = base+box.height/2
                    moment_gm += box.weight*(mid_height+stack.vp)
                    moment_lash += box.weight*(mid_height+GAMMA*stack.vp)
                    weight += box.weight
                height += (aft or fore).height if aft is not None else fore.height
            weight_of_bay[key[1]] += weight
            vertical += moment_gm
            if stack.level == 'OnDeck' and weight:
                given = untouched({b.id for b in stack_cells.values()}, instance, position, port)
                changed = key in moved_cells
                if not (readings.lash_changed_only and not changed):
                    lash_inputs.append((key, moment_lash/weight, weight, given))

        d = sum(weight_of_bay.values())
        if d > vessel.max_displacement+readings.tol:
            faults['(6) displacement'] += 1
        lcg = sum(b.mid*weight_of_bay[b.name] for b in vessel.bays)/d
        trim = interp(vessel.hydrostatics, 3, d)*(interp(vessel.hydrostatics, 1, d)-lcg)
        gm = interp(vessel.hydrostatics, 2, d)-vertical/d
        if not TRIM_LOW-readings.tol <= trim <= TRIM_HIGH+readings.tol:
            faults['(7) trim'] += 1
        if gm < vessel.gm_limit-readings.tol:
            faults['(11) GM'] += 1
        worst_bending = 0.
        for i, bay in enumerate(vessel.bays):
            if bay.sagging is None:
                continue
            bending = sum((f.mid-bay.fore)*(weight_of_bay[f.name]-interp(f.bonjean, 1, d)*RHO) for f in vessel.bays[:i])
            limit_here = bay.hogging if bending > 0 else bay.sagging
            worst_bending = max(worst_bending, bending/limit_here if limit_here else 0.)
            if not bay.sagging-readings.tol <= bending <= bay.hogging+readings.tol:
                faults['(12) bending'] += 1
                worst_bending = max(worst_bending, 1.01)
        worst_lash = 0.
        for key, hm, weight, given in lash_inputs:
            value = (max(gm, 1e-9)**ALPHA)*(BETA**hm)*weight
            if not given:
                worst_lash = max(worst_lash, value)
            if value > vessel.lash_limit+readings.tol:
                faults['arrival-only stack lashing' if given else '(29) lashing'] += 1

        # (27): the documents don't define the blocked slots, so the real rule is not enforced here. Stand-in: different IMDG types never share a
        # level of a bay, counting arrival boxes beside load boxes. The published plans do share one, so the true rule is looser in that respect, but
        # it may forbid something this allows, such as adjacent bays. Any claim must say segregation is approximated.
        dangerous, loaded_here = defaultdict(set), set()
        for key, stack_cells in now.items():
            stack = index[key[0]]
            for box in stack_cells.values():
                if box.imdg:
                    dangerous[stack.bay, stack.level == 'OnDeck'].add(box.imdg_type)
                    if box.id not in instance.arrival:
                        loaded_here.add((stack.bay, stack.level == 'OnDeck'))
        mixed_dangerous = sum(len(types) > 1 and where in loaded_here for where, types in dangerous.items())
        if mixed_dangerous:
            faults['(27*) different IMDG types on one level of a bay'] += mixed_dangerous

        # Paired block (36)-(37): a block location holding two discharge ports breaks the pattern for that bay
        mixed = set()
        ports_in_block = defaultdict(set)
        for key, stack_cells in now.items():
            stack = index[key[0]]
            for box in stack_cells.values():
                ports_in_block[stack.bay, stack.block].add(box.pod)
        for (bay, block), pods in ports_in_block.items():
            if len(pods) > 1:
                mixed.add(bay)
        if len(mixed) > instance.max_mixed_bays:
            faults['(37) too many mixed bays'] += 1

        measures['ports'][port] = {'displacement': d, 'trim': trim, 'gm': gm, 'bending': worst_bending, 'lashing': worst_lash,
                                   'ballast': ballast, 'mixed_bays': len(mixed), 'long_crane': worst, 'long_crane_limit': limit}
        before = now

    measures['ballast'] = ballast_total
    measures['points_flat'] = 50*measures['loaded']
    measures['points_file'] = sum(instance.load[i].points for i in position if i in instance.load)
    measures['objective_flat'] = measures['points_flat']-THETA*ballast_total-ETA*measures['restows']
    measures['objective_file'] = measures['points_file']-THETA*ballast_total-ETA*measures['restows']
    return faults, measures
