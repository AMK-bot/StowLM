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
        addresses = getattr(future, "address", None)
        deck = (bool(addresses[si].on_deck) if addresses is not None else
                bool(t.slot_deck[si]) if hasattr(t, "slot_deck") else False)
        # Native hold-first ordering protects hatch access. Pool pressure may
        # reorder choices only within that phase; loading deck first can force
        # later hold queries through expensive ship-restow/E9 replay.
        key = (int(deck), sum(deficits), max(deficits, default=0), max(risks, default=0.), sum(risks), order)
        ranked.append((key, action))
    ranked.sort(key=lambda item: item[0])
    metrics["scale_v4_booking_pool_calls"] = metrics.get("scale_v4_booking_pool_calls", 0) + 1
    metrics["scale_v4_booking_pool_max"] = max(metrics.get("scale_v4_booking_pool_max", 0), len(pools))
    return [action for _, action in ranked]


def record_empty_choices(instance, node, future, actions, metrics, deadline):
    """Bounded deep-prefix observations; never issue a new native query."""
    if actions:
        return actions
    ex, t = node.ex, future.t
    remaining = [cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] != "ship"]
    remaining += [cid for cid in ex.buffer if cid not in remaining]
    if not remaining or len(remaining) > 8:
        return actions
    signature = sorted(remaining)
    records = metrics.setdefault("scale_v4_empty_choices", [])
    if len(records) >= 8 or any(row.get("remaining_signature") == signature for row in records):
        return actions
    row = {"remaining_signature": signature, "remaining_count": len(remaining),
           "scope": "optimistic_domains_and_already_cached_native_mask_only",
           "mode_loss_is_diagnostic_not_proven_cause": True, "boxes": []}
    records.append(row)
    if time.monotonic() >= deadline:
        row["diagnostic_status"] = "skipped_deadline"
        return actions
    started = time.monotonic()
    try:
        indices, domains = future.evaluate(ex, remaining)
        backend = getattr(node.engine, "backend", node.engine)
        state = getattr(backend, "_cur", None)
        flat = getattr(state, "mask", None)
        cached = bool(getattr(state, "evaluated", False)) and flat is not None
        row["native_legal_counts_available"] = cached
        counts = (np.bincount(np.asarray(flat, dtype=np.int64) // len(t.slot_ids),
                              minlength=len(t.box_ids)) if cached and len(t.slot_ids) else None)
        dead = getattr(state, "dead", None) if cached else None
        if dead is not None:
            row["native_cached_dead_reason"] = getattr(dead, "reason", None)
            row["native_cached_dead_rule_ids"] = list(getattr(dead, "rule_ids", ()))
        long_columns = []
        slot_index = t.slot_index if hasattr(t, "slot_index") else {s: i for i, s in enumerate(t.slot_ids)}
        for cid, location in ex.where.items():
            if location[0] != "ship" or cid not in t.box_index:
                continue
            bi, si = t.box_index[cid], slot_index.get(location[1])
            if t.length[bi] == 20 or si is None:
                continue
            js = {int(j) for j in t.slot_stacks[si] if j >= 0}
            long_columns.append((cid, location[1], js))
        for ri, cid in enumerate(remaining):
            bi, box = t.box_index[cid], future.boxes[cid]
            static_slots, future_slots = np.flatnonzero(t.static[bi]), np.flatnonzero(domains[ri])
            info = {"container_id": cid, "length_ft": int(t.length[bi]),
                    "height_m": float(t.height[bi]), "mass_kg": float(t.mass[bi]),
                    "rank": int(t.rank[bi]), "reefer": bool(box.reefer),
                    "special_stowage": sorted(str(value) for value in box.special_stowage),
                    "static_domain_count": len(static_slots), "future_domain_count": len(future_slots),
                    "static_slots_sample": [t.slot_ids[int(si)] for si in static_slots[:8]],
                    "future_slots_sample": [t.slot_ids[int(si)] for si in future_slots[:8]],
                    "slot_samples_truncated": bool(len(static_slots) > 8 or len(future_slots) > 8),
                    "native_cached_legal_count": int(counts[bi]) if counts is not None else None}
            if t.length[bi] == 20:
                possible_stacks = set(int(j) for j in t.slot_stacks[static_slots].ravel() if j >= 0)
                blockers = [(placed, slot, sorted(js & possible_stacks))
                            for placed, slot, js in long_columns if js & possible_stacks]
                info["long_occupied_columns_sample"] = [
                    {"container_id": placed, "slot": slot, "stack_indices": stacks}
                    for placed, slot, stacks in blockers[:4]]
                info["long_occupied_columns_truncated"] = len(blockers) > 4
            row["boxes"].append(info)
        row["diagnostic_status"] = "recorded"
    except Exception as exc:
        row.update(diagnostic_status="error", error=f"{type(exc).__name__}: {exc}")
    elapsed = time.monotonic() - started
    row["diagnostic_time_s"] = elapsed
    metrics["scale_v4_empty_choices_diagnostic_time_s"] = metrics.get("scale_v4_empty_choices_diagnostic_time_s", 0.) + elapsed
    return actions


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
        native_finished = time.monotonic()
        record_empty_choices(instance, node, future, actions, metrics, deadline)
        diagnostics_finished = time.monotonic()
        metrics["scale_v4_search_version"] = VERSION
        metrics["scale_v4_box_profiles"] = profiles
        metrics["scale_v4_backtrack_limit"] = backtrack_limit
        metrics["v4_search_scope"] = (f"bounded monotone placements; no forced ship restows; "
                                      f"at most {profiles} full-fact box profiles per state")
        actions = reservations._rank(instance, node, future, actions, metrics, deadline)
        mode_finished = time.monotonic()
        result = rank_bookings(instance, node, future, actions, metrics, deadline)
        booking_finished = time.monotonic()
        for name, elapsed in (("native_choices", native_finished-started),
                              ("diagnostics", diagnostics_finished-native_finished),
                              ("mode_order", mode_finished-diagnostics_finished),
                              ("booking_order", booking_finished-mode_finished),
                              ("choices", booking_finished-started)):
            key = f"scale_v4_{name}_time_s"
            metrics[key] = metrics.get(key, 0.) + elapsed
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
