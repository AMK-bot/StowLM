"""Wider native constructor choices and booking-pool ordering, search only."""
from __future__ import annotations

import ast
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
import inspect
import time

import numpy as np

VERSION = "scale-reservations-4-six-profiles-booking-pools-512"


def widen_choices(original, *, profiles=6):
    """Copy the native function, changing only its literal profile shortlist.

    Fail closed if the pinned source no longer has the expected assignment.
    No native rule, domain, placement screen or checker is copied/modified.
    """
    original = inspect.unwrap(original)
    tree = ast.parse(inspect.getsource(original))
    changed = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not any(isinstance(t, ast.Name) and t.id == "selected" for t in node.targets):
            continue
        for child in ast.walk(node.value):
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "min"
                    and len(child.args) == 2 and isinstance(child.args[0], ast.Constant)
                    and child.args[0].value == 3):
                child.args[0].value = profiles
                changed += 1
    if changed != 1:
        raise RuntimeError(f"Pinned native shortlist mismatch: expected one three-profile assignment, found {changed}")
    namespace = dict(original.__globals__)
    exec(compile(ast.fix_missing_locations(tree), "<scale/reservations_v4/native_choices>", "exec"), namespace)
    return namespace[original.__name__]


def _pools(instance, future, remaining, domains, *, limit=12):
    """Bounded optimistic 20ft booking pools, thresholds and scarce unions."""
    t = future.t
    short = [i for i, cid in enumerate(remaining) if t.length[t.box_index[cid]] == 20]
    if not short:
        return []
    signatures = defaultdict(list)
    for ri in short:
        cid = remaining[ri]
        box = future.boxes[cid]
        key = (future.signatures[cid], bool(box.reefer), tuple(sorted(box.special_stowage)))
        signatures[key].append(ri)
    candidates = [set(short), *(set(rows) for rows in signatures.values())]
    for attribute in (t.rank, t.height):
        values = sorted({attribute[t.box_index[remaining[ri]]] for ri in short})
        for value in values[1:]:
            candidates.append({ri for ri in short if attribute[t.box_index[remaining[ri]]] >= value})
    for name in {request for ri in short for request in future.boxes[remaining[ri]].special_stowage}:
        candidates.append({ri for ri in short if name in future.boxes[remaining[ri]].special_stowage})
    candidates.append({ri for ri in short if future.boxes[remaining[ri]].reefer})
    seen, ranked = set(), []

    def add(rows):
        key = tuple(sorted(rows))
        if not key or key in seen:
            return
        seen.add(key)
        mask = np.any(domains[list(key)], axis=0)
        demand, capacity = len(key), int(mask.sum())
        ranked.append(((capacity - demand) / max(1, demand), capacity - demand, key, mask))

    for rows in candidates:
        add(rows)
    scarce = sorted(ranked, key=lambda x: (x[0], x[1], x[2]))[:3]
    for i in range(len(scarce)):
        for j in range(i + 1, len(scarce)):
            add(set(scarce[i][2]) | set(scarce[j][2]))
    if len(scarce) == 3:
        add(set().union(*(set(pool[2]) for pool in scarce)))
    selected = sorted(ranked, key=lambda x: (x[0], x[1], x[2]))[:limit]
    # Always preserve the all-short pool in addition to booking subsets.
    all_key = tuple(short)
    if all(pool[2] != all_key for pool in selected):
        selected[-1:] = [next(pool for pool in ranked if pool[2] == all_key)]
    result = []
    for _, _, rows, mask in selected:
        stacks = t.slot_stacks[mask].ravel()
        counts = np.bincount(stacks[stacks >= 0], minlength=len(t.stack_deck))
        result.append((set(remaining[ri] for ri in rows), mask, int(mask.sum()), counts))
    return result


def rank_bookings(instance, node, future, actions, metrics, deadline):
    """Reorder every input action; optimistic deficits never prune anything."""
    if len(actions) < 2 or any(not hasattr(action, "slot") for action in actions):
        return actions
    if time.monotonic() >= deadline:
        return actions
    ex, t = node.ex, future.t
    remaining = [cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] != "ship"]
    remaining += [cid for cid in ex.buffer if cid not in remaining]
    _, domains = future.evaluate(ex, remaining)
    pools = _pools(instance, future, remaining, domains)
    if not pools or time.monotonic() >= deadline:
        return actions
    slot_index = t.slot_index if hasattr(t, "slot_index") else {s: i for i, s in enumerate(t.slot_ids)}
    ranked = []
    for order, action in enumerate(actions):
        if time.monotonic() >= deadline:
            metrics["scale_v4_booking_deadline_fallbacks"] = metrics.get("scale_v4_booking_deadline_fallbacks", 0) + 1
            return actions
        si, cid = slot_index[action.slot], action.box
        islong = t.length[t.box_index[cid]] != 20
        stacks = t.slot_stacks[si]
        stacks = stacks[stacks >= 0]
        deficits, risks = [], []
        for members, mask, capacity, counts in pools:
            need = len(members) - int(cid in members)
            if need <= 0:
                continue
            # Long cargo irreversibly removes every higher 20ft destination
            # on both columns. Short cargo consumes only its physical cell.
            lost = int(counts[stacks].sum()) if islong else int(mask[si])
            after = capacity - lost
            deficits.append(max(0, need - after))
            risks.append(lost / max(1, capacity - need))
        key = (sum(deficits), max(deficits, default=0), max(risks, default=0.), sum(risks), order)
        ranked.append((key, action))
    ranked.sort(key=lambda item: item[0])
    metrics["scale_v4_booking_pool_calls"] = metrics.get("scale_v4_booking_pool_calls", 0) + 1
    metrics["scale_v4_booking_pool_max"] = max(metrics.get("scale_v4_booking_pool_max", 0), len(pools))
    return [action for _, action in ranked]


@contextmanager
def constructor_search(*, profiles=6, backtrack_limit=512, max_restarts=3):
    """Scope widened search/config to one carrier constructor invocation."""
    from stowage.solver import construct_v4, twostep_construct
    import reservations

    previous_choices, previous_config = construct_v4._choices, twostep_construct.ScalableConfig
    wider = widen_choices(previous_choices, profiles=profiles)

    def choices(instance, node, future, rng, metrics, attempt, deadline=float("inf"), *, heavy_first_ordering=False):
        started = time.monotonic()
        actions = wider(instance, node, future, rng, metrics, attempt, deadline,
                        heavy_first_ordering=heavy_first_ordering)
        metrics["scale_v4_search_version"] = VERSION
        metrics["scale_v4_box_profiles"] = profiles
        metrics["scale_v4_backtrack_limit"] = backtrack_limit
        metrics["v4_search_scope"] = (f"bounded monotone placements; no forced ship restows; "
                                      f"at most {profiles} full-fact box profiles per state")
        actions = reservations._rank(instance, node, future, actions, metrics, deadline)
        result = rank_bookings(instance, node, future, actions, metrics, deadline)
        metrics["scale_v4_choices_time_s"] = metrics.get("scale_v4_choices_time_s", 0.) + time.monotonic() - started
        return result

    def config(*args, **kwargs):
        original = previous_config(*args, **kwargs)
        return replace(original, backtrack_limit=backtrack_limit, max_restarts=max_restarts)

    construct_v4._choices, twostep_construct.ScalableConfig = choices, config
    try:
        yield
    finally:
        if construct_v4._choices is choices:
            construct_v4._choices = previous_choices
        if twostep_construct.ScalableConfig is config:
            twostep_construct.ScalableConfig = previous_config
