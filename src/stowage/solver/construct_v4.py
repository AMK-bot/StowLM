"""One-path, fail-first construction with optimistic slot reservations.

MRV schedules the cargo with the fewest remaining destinations first; the
least-constraining-value preference uses space other cargo needs least.
Unlike a beam, one legal child is expanded at a time and its siblings remain
available for bounded depth-first backtracking. Sources: Russell & Norvig,
Artificial Intelligence: A Modern Approach, fourth edition, chapter 6,
https://aima.cs.berkeley.edu/ .

APPROXIMATION: future domains optimistically fill missing supports with the
shortest remaining box and ignore future DG interactions and pair overlap.
APPROXIMATION: search retains placements permanently and declines actions
that force ship restows. This trims the action domain, never any hard rule.
These are search hints/necessary screens, never acceptance or infeasibility
certificates. Real placements still pass the unchanged public feasibility
oracle and securing screen; finalized full checking remains mandatory.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import time

import numpy as np

from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.solver.securing_geometry import securing_geometry_violation


@dataclass
class _Frame:
    node: object
    choices: list | None = None
    next_choice: int = 0


class FutureDomains:
    """Optimistic empty-slot domains, including existing immutable supports.

    The domain count deliberately overestimates complete packings. A zero or
    class cardinality deficit rules out only this monotone construction branch:
    discharges/restows can change supports, so those states do not prune.
    """
    def __init__(self, instance, tables):
        self.instance, self.t = instance, tables
        self.boxes = {b.id: b for b in instance.containers}
        self.parts = [cells_of_slot(s) for s in tables.slot_ids]
        self.address = [parse_slot(s) for s in tables.slot_ids]
        self.cells_to_slots = [[] for _ in tables.cell_ids]
        for si, parts in enumerate(self.parts):
            for cell in parts:
                self.cells_to_slots[tables.cell_index[cell]].append(si)
        self.by_cell = np.zeros((len(tables.slot_ids), len(tables.cell_ids)), dtype=bool)
        for si, parts in enumerate(self.parts):
            for cell in parts:
                self.by_cell[si, tables.cell_index[cell]] = True
        self.visibility = np.minimum(
            tables.stack_hmax,
            np.asarray(tables.extra.get('stack_visibility_top', np.full(len(tables.stack_ids), np.inf)))
            - tables.stack_base_z)
        self.signatures = {}
        self.representative_signatures = {}
        from stowage.solver.construct_v4_capacity import CapacityPools
        self.pools = CapacityPools(tables, self.parts)
        for bi, cid in enumerate(tables.box_ids):
            box = self.boxes[cid]
            self.representative_signatures[cid] = (
                box.model_dump_json(exclude={'id', 'category'}) if hasattr(box, 'model_dump_json')
                else repr(sorted(vars(box).items())))
            self.signatures[cid] = (box.length_ft, float(tables.height[bi]),
                                    int(tables.rank[bi]), tables.static[bi].tobytes())

    def evaluate(self, ex, remaining):
        t = self.t
        indices = np.array([t.box_index[c] for c in remaining], dtype=np.int64)
        J = len(t.stack_ids)
        fill = np.zeros(J, dtype=np.int64)
        heights = np.zeros(J)
        masses = np.zeros(J)
        ranks = np.full(J, 1000000, dtype=np.int64)
        nonheavy = np.zeros(J, dtype=bool)
        n20 = np.zeros(J, dtype=bool)
        nlong = np.zeros(J, dtype=bool)
        closed = np.zeros(J, dtype=bool)
        occupied = np.zeros(len(t.cell_ids), dtype=bool)
        for cid, (where, slot) in ex.where.items():
            if where != 'ship' or cid not in t.box_index:
                continue
            bi = t.box_index[cid]
            for cell in cells_of_slot(slot):
                ci = t.cell_index[cell]
                j = int(t.cell_stack[ci])
                occupied[ci] = True
                fill[j] += 1
                heights[j] += t.height[bi]
                masses[j] += t.share[bi]
                ranks[j] = min(ranks[j], t.rank[bi])
                nonheavy[j] |= bool(t.e1_threshold is not None and t.mass[bi] < t.e1_threshold)
                n20[j] |= t.length[bi] == 20
                nlong[j] |= t.length[bi] != 20
                closed[j] |= bool(t.closes_stack[bi])
        free = ~np.any(self.by_cell & occupied, axis=1)
        domain = t.static[indices].copy()
        domain &= free[None, :]
        minimum_height = float(np.min(t.height[indices])) if len(indices) else 0.
        for side in (0, 1):
            valid = t.slot_stacks[:, side] >= 0
            js = np.maximum(t.slot_stacks[:, side], 0)
            missing = np.maximum(0, t.slot_k[:, side] - fill[js])
            roof = heights[js] + missing * minimum_height
            hroom = self.visibility[js] - roof
            allowed = (~closed[js])[None, :] & (t.height[indices, None] <= hroom[None, :] + 1e-9)
            allowed &= t.rank[indices, None] <= ranks[js][None, :] + t.e2_tol
            if t.e1_threshold is not None:
                allowed &= ~( (t.mass[indices, None] >= t.e1_threshold) & nonheavy[js][None, :])
            allowed &= ~((t.length[indices, None] == 20) & nlong[js][None, :])
            allowed &= ~((t.length[indices, None] != 20) & n20[js][None, :] & (~t.stack_deck[js])[None, :])
            optimistic_mass_cap = np.maximum.reduce([t.stack_lim_20[js], t.stack_lim_40[js], t.stack_lim_mixed[js]])
            allowed &= t.share[indices, None] <= (optimistic_mass_cap - masses[js])[None, :] + 1e-6
            domain &= allowed | (~valid)[None, :]
        cap = np.minimum(
            np.array([len(c) for c in t.stack_cells]) - fill,
            np.maximum(0, np.floor((self.visibility-heights)/max(minimum_height, 1e-12)+1e-9)).astype(np.int64))
        cap[closed] = 0
        self.last_fill, self.last_heights, self.last_cap = fill, heights, cap
        self.minimum_height = minimum_height
        self.last_capacity = dict(demand=int(t.units[indices].sum()), capacity=int(cap.sum()),
                                  min_height_m=minimum_height)
        return indices, domain


def _choices(instance, node, future, rng, metrics, attempt, deadline=float('inf'), *,
             heavy_first_ordering=False):
    """Rank actual legal pairs using future-domain scarcity and reservation."""
    from stowage.solver.scalable import _moment_preference
    if time.monotonic() >= deadline:
        return []
    ex, engine, t = node.ex, node.engine, future.t
    discharges = [Discharge(cid) for cid in instance.discharge_list
                  if ex.where.get(cid, (None,))[0] == 'ship']
    if discharges:
        return [a for a in discharges if engine.is_legal(a)]
    remaining = [cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] != 'ship']
    remaining += [cid for cid in ex.buffer if cid not in remaining]
    if not remaining:
        return []
    indices, domains = future.evaluate(ex, remaining)
    potential_counts = domains.sum(axis=1)
    groups = Counter(future.signatures[c] for c in remaining)
    # Group domains also contain mass-specific restrictions. Union rather than
    # intersection keeps this count optimistic for unequal masses of one class.
    union = {}
    for ri, cid in enumerate(remaining):
        sig = future.signatures[cid]
        if sig not in union:
            union[sig] = domains[ri].copy()
        else:
            union[sig] |= domains[ri]
    deficits = [dict(boxes=groups[sig], slots=int(mask.sum()))
                for sig, mask in union.items() if groups[sig] > int(mask.sum())]
    # APPROXIMATION: keep placements fixed and decline forced-restow successors;
    # this is a search-domain restriction, not a physical infeasibility claim.
    monotone = True
    deficits.extend(future.pools.evaluate(indices, domains))
    if (deficits or future.last_capacity['demand'] > future.last_capacity['capacity']) and monotone:
        metrics['v4_reservation_prunes'] = metrics.get('v4_reservation_prunes', 0) + 1
        metrics['v4_last_deficits'] = deficits[:8]
        metrics['v4_visibility_capacity'] = dict(future.last_capacity)
        return []
    if time.monotonic() >= deadline:
        return []
    box_indices, slot_indices = engine.legal_pairs()
    metrics['candidate_probes'] += len(box_indices)
    metrics['legal_candidates'] += len(box_indices)
    counts = np.bincount(box_indices, minlength=len(t.box_ids))
    by_box = {cid: slot_indices[box_indices == t.box_index[cid]]
              for cid in remaining if counts[t.box_index[cid]]}
    if not by_box:
        return []
    row = {cid: ri for ri, cid in enumerate(remaining)}
    # Full physical/booking facts define representatives; coarse reservation
    # groups above deliberately use unions and are never equivalence proofs.
    def blockers(cid):
        location, key = ex.where.get(cid, ('', ''))
        return len(ex.yard[key])-ex.yard[key].index(cid)-1 if location == 'yard' else 0
    def box_key(cid):
        ri, bi = row[cid], t.box_index[cid]
        demand = groups[future.signatures[cid]]
        scarcity = potential_counts[ri] / demand
        box = ex.boxes[cid]
        # Heavy supports precede lighter bookings when space is plentiful;
        # truly scarce domains take priority over that preference.
        no_hold = not np.any(~t.slot_deck[by_box[cid]])
        common = (int(no_hold), int(scarcity > 2.0))
        profiles = ((-int(t.rank[bi]), -float(t.height[bi]), -box.length_ft, scarcity),
                    (-float(t.height[bi]), -int(t.rank[bi]), -box.length_ft, scarcity),
                    (-box.length_ft, -int(t.rank[bi]), -float(t.height[bi]), scarcity),
                    (scarcity, -int(t.rank[bi]), -float(t.height[bi]), -box.length_ft))
        # APPROXIMATION: when heavy-first ordering is opted in, use exact mass to order
        # similarly abundant bookings heavy first. This is search steering,
        # never a substitute for the unchanged force or feasibility checks.
        heavy_first = (-float(t.mass[bi]),) if heavy_first_ordering else ()
        return (*common, *heavy_first, *profiles[attempt % 4], len(by_box[cid]), blockers(cid), cid)

    ordered = sorted(by_box, key=box_key)
    reps, used = [], set()
    for cid in ordered:
        sig = future.representative_signatures[cid]
        if sig not in used:
            reps.append(cid)
            used.add(sig)
    # APPROXIMATION: branch on at most three fully distinct box profiles per
    # state. Bounded restarts diversify ordering; this is not exhaustive search.
    selected = reps[:min(3, len(reps))]
    # A slot needed by many scarce classes is expensive to consume. 20-foot
    # footprints count once; long footprints naturally reserve both cells.
    weights = np.array([1. / max(1., float(n)) for n in potential_counts])
    slot_pressure = weights @ domains
    cell_pressure = future.by_cell.T @ slot_pressure
    actions = []
    for order, cid in enumerate(selected):
        bi = t.box_index[cid]
        ranked = []
        for si in by_box[cid]:
            if time.monotonic() >= deadline:
                break
            si = int(si)
            action = Place(cid, t.slot_ids[si])
            addr = future.address[si]
            if engine.forces_restow(action):
                continue
            pressure = sum(cell_pressure[t.cell_index[c]] for c in future.parts[si])
            # Prefer filling low/middle constrained-height columns before
            # consuming columns that can still support the tallest bookings.
            js = t.slot_stacks[si]
            tightness = min(future.visibility[j] for j in js if j >= 0)
            lost = 0
            for j in js[js >= 0]:
                after = min(len(t.stack_cells[j])-future.last_fill[j]-1,
                            max(0, int(np.floor((future.visibility[j]-future.last_heights[j]-t.height[bi])/future.minimum_height+1e-9))))
                lost += max(0, int(future.last_cap[j])-after-1)

            # Holds preserve future deck/hatch access and reduce E6 pressure.
            key = (int(addr.on_deck), lost, pressure, tightness, addr.tier, _moment_preference(node, cid, future.parts[si]), rng.random(), action.slot)
            if attempt % 3 == 2:
                key = (int(addr.on_deck), lost, tightness, pressure, addr.tier, _moment_preference(node, cid, future.parts[si]), rng.random(), action.slot)
            ranked.append((key, action))
        ranked.sort(key=lambda item: item[0])
        actions.extend((order, key, action) for key, action in ranked)
    actions.sort(key=lambda item: (item[1][0], item[0], item[1]))
    return [a for _, _, a in actions]


def construct(instance, start, slots, cfg, deadline, rng, metrics, pods, progress=None):
    """Drop-in opt-in ``scalable._construct`` implementation; one CPU core."""
    from stowage.solver.scalable import (
        _Node, _rank, _securing_complete, _securing_placement,
        _construct as legacy_construct,
    )
    if cfg.securing_screen and start.securing_screen is None:
        from stowage.solver.securing_screen import DeckSecuringScreen
        start.securing_screen = DeckSecuringScreen(instance)
    # Activated rules and custom search oracles retain their established path;
    # this MVP constructor only targets the legacy native feasibility engine.
    backend = getattr(start.engine, 'backend', None)
    if rule_freeze_v1_enabled() or backend is None or not hasattr(backend, 'T'):
        from dataclasses import replace
        return legacy_construct(instance, start, slots, replace(cfg, constructor_v4=False), deadline, rng, metrics, pods, progress)
    metrics['constructor'] = 'v4-reservation-dfs'
    metrics['v4_search_scope'] = 'bounded monotone placements; no forced ship restows; at most three full-fact box profiles per state'
    metrics['v4_capacity_certificate'] = False
    future = FutureDomains(instance, backend.T)
    deepest = start
    base_loaded = sum(start.ex.where.get(cid, (None,))[0] == 'ship' for cid in instance.load_list)
    deepest_loaded = base_loaded
    attempts = 1 + (cfg.max_restarts if cfg.backtrack_limit else 0)
    for attempt in range(attempts):
        now = time.monotonic()
        if now >= deadline:
            break
        attempt_deadline = now + (deadline-now)/(attempts-attempt)
        root = _Node(start.engine.copy(), start.ex.copy(), classes=start.classes,
                     securing_screen=start.securing_screen)
        stack = [_Frame(root)]
        backtracks = 0
        metrics.setdefault('restart_strategies', []).append(attempt)
        if attempt:
            metrics['restarts'] += 1
        while stack and time.monotonic() < attempt_deadline:
            frame = stack[-1]
            node = frame.node
            if node.engine.is_complete():
                if _securing_complete(instance, node, metrics):
                    metrics.setdefault('construction_attempts', []).append(dict(strategy=attempt, outcome='complete', elapsed_s=time.monotonic()-now, deepest_depth=node.engine.depth))
                    return [node], deepest
                frame.choices = []
            if frame.choices is None:
                frame.choices = _choices(instance, node, future, rng, metrics, attempt, attempt_deadline,
                                         heavy_first_ordering=cfg.heavy_first_ordering)
            if frame.next_choice >= len(frame.choices):
                metrics['search_dead_ends'] += 1
                stack.pop()
                backtracks += 1
                metrics['backtracks'] += 1
                if backtracks >= max(1, cfg.backtrack_limit):
                    break
                continue
            action = frame.choices[frame.next_choice]
            frame.next_choice += 1
            if time.monotonic() >= attempt_deadline:
                break
            if securing_geometry_violation(instance, node.ex, action, include_existing=True):
                metrics['securing_geometry_rejections'] = metrics.get('securing_geometry_rejections', 0) + 1
                continue
            if not _securing_placement(node, action, metrics):
                continue
            engine, ex = node.engine.copy(), node.ex.copy()
            engine.push(action)
            ex.step(action)
            child = _Node(engine, ex, classes=node.classes,
                          securing_screen=node.securing_screen)
            child.score = _rank(instance, child, pods)
            loaded = sum(ex.where.get(cid, (None,))[0] == 'ship' for cid in instance.load_list)
            metrics['deepest_depth'] = max(metrics.get('deepest_depth', 0), engine.depth)
            if loaded > deepest_loaded:
                deepest, deepest_loaded = child, loaded
            if progress is not None:
                progress(child)
            stack.append(_Frame(child))
        metrics.setdefault('construction_attempts', []).append(dict(strategy=attempt, outcome='timeout' if time.monotonic() >= attempt_deadline else 'exhausted', elapsed_s=time.monotonic()-now, deepest_depth=deepest.engine.depth))
    return [], deepest
