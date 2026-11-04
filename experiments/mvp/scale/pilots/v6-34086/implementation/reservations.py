"""D199 search ordering: preserve future short/long stack modes.

Only the order of construct_v4._choices' original actions changes. All actions,
domains, capacity screens, legality, securing and full checking stay unchanged.
Union-domain counts are optimistic search scores, never feasibility claims.
"""
from __future__ import annotations

from functools import wraps
import time

import numpy as np


def _rank(instance, node, future, actions, metrics, deadline):
    if not actions or any(not hasattr(action, 'slot') for action in actions):
        return actions
    ex, t = node.ex, future.t
    remaining = [cid for cid in instance.load_list
                 if ex.where.get(cid, (None,))[0] != 'ship']
    remaining += [cid for cid in ex.buffer if cid not in remaining]
    short = [cid for cid in remaining if t.length[t.box_index[cid]] == 20]
    long = [cid for cid in remaining if t.length[t.box_index[cid]] != 20]
    if not short or not long or time.monotonic() >= deadline:
        return actions
    indices, domains = future.evaluate(ex, remaining)
    if time.monotonic() >= deadline:
        return actions
    row = {cid: index for index, cid in enumerate(remaining)}
    slot_index = {slot: index for index, slot in enumerate(t.slot_ids)}
    adjacent = getattr(future, 'cells_to_slots', None)
    if adjacent is None:  # Small analytic table adapters need no full class.
        adjacent = [np.flatnonzero(future.by_cell[:, ci])
                    for ci in range(len(t.cell_index))]
    by_box = {}
    ranked = []
    for original_order, action in enumerate(actions):
        if time.monotonic() >= deadline:
            # Never return a partly ranked/truncated legal action list.
            metrics['scale_reservation_deadline_fallbacks'] = metrics.get(
                'scale_reservation_deadline_fallbacks', 0)+1
            return actions
        cid = action.box
        if cid not in by_box:
            masks = []
            demand = []
            for group in (short, long):
                rows = [row[other] for other in group if other != cid]
                masks.append(np.any(domains[rows], axis=0) if rows
                             else np.zeros(len(t.slot_ids), dtype=bool))
                demand.append(len(rows))
            per_stack = []
            for mask in masks:
                stacks = t.slot_stacks[mask].ravel()
                per_stack.append(np.bincount(stacks[stacks >= 0],
                                             minlength=len(t.stack_deck)))
            by_box[cid] = masks, demand, per_stack
        (short_mask, long_mask), (short_demand, long_demand), per_stack = by_box[cid]
        si = slot_index[action.slot]
        js = t.slot_stacks[si]
        js = js[js >= 0]
        cells = [t.cell_index[cell] for cell in future.parts[si]]
        overlaps = np.array(sorted({int(other) for ci in cells
                                    for other in adjacent[ci]}), dtype=int)
        short_overlap = int(short_mask[overlaps].sum())
        long_overlap = int(long_mask[overlaps].sum())
        short_blocked, long_blocked = short_overlap, long_overlap
        if t.length[t.box_index[cid]] != 20:
            # 20-over-long is forbidden even on deck. One long placement closes
            # BOTH physical stacks' remaining higher cells to every short box.
            short_blocked = int(per_stack[0][js].sum())
        else:
            # The existing rules allow supported long-over-paired-20 on deck;
            # only hold stacks lose their future long mode after a short box.
            holds = js[~t.stack_deck[js]]
            if len(holds):
                # A legal 20-foot action occupies exactly one physical stack.
                # Keep generic adapters safe without double-counting pairs.
                long_blocked = (int(per_stack[1][holds[0]]) if len(holds) == 1
                    else int(np.count_nonzero(long_mask & np.any(
                        np.isin(t.slot_stacks, holds), axis=1))))
        capacities = (int(short_mask.sum()), int(long_mask.sum()))
        after = (capacities[0]-short_blocked, capacities[1]-long_blocked)
        demands = (short_demand, long_demand)
        deficits = tuple(max(0, need-cap) for need, cap in zip(demands, after))
        # Charge only extra future mode loss here; unavoidable same-tier
        # occupancy remains represented in the post-placement deficit above.
        lost = (short_blocked-short_overlap, long_blocked-long_overlap)
        risk = tuple(loss/max(1, capacity-need)
                     for loss, capacity, need in zip(lost, capacities, demands))
        key = (sum(deficits), max(deficits), max(risk), sum(risk), sum(lost), original_order)
        ranked.append((key, action))
    ranked.sort(key=lambda item: item[0])
    result = [action for _, action in ranked]
    metrics['scale_reservation_calls'] = metrics.get('scale_reservation_calls', 0)+1
    metrics['scale_reservation_actions_scored'] = metrics.get(
        'scale_reservation_actions_scored', 0)+len(actions)
    metrics['scale_reservation_first_changed'] = metrics.get(
        'scale_reservation_first_changed', 0)+int(result[0] is not actions[0])
    return result


def install():
    """Install after frozen-runtime loading; return an idempotent restore hook."""
    from stowage.solver import construct_v4
    original = construct_v4._choices
    if getattr(original, '_scale_mode_reservations', False):
        return lambda: None

    @wraps(original)
    def choices(instance, node, future, rng, metrics, attempt,
                deadline=float('inf'), *, heavy_first_ordering=False):
        started = time.monotonic()
        actions = original(instance, node, future, rng, metrics, attempt, deadline,
                           heavy_first_ordering=heavy_first_ordering)
        original_elapsed = time.monotonic()-started
        try:
            return _rank(instance, node, future, actions, metrics, deadline)
        finally:
            metrics['scale_reservation_order_s'] = metrics.get(
                'scale_reservation_order_s', 0.)+time.monotonic()-started-original_elapsed
    choices._scale_mode_reservations = True
    construct_v4._choices = choices

    def restore():
        if construct_v4._choices is choices:
            construct_v4._choices = original
    return restore
