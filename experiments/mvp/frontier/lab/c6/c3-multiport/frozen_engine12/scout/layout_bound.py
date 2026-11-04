# Provenance: copied from experiments/mvp/frontier/lab/c2/c2-layout/bound.py.
"""Optimistic aggregate port-cover design lower bound; not a page LP bound.

Only necessary cell, summed stack-mass/height and unary eligibility constraints
survive. No pure-page or lashing approximation is used. A group may split among
regions. Cross-cover footprint signatures retain shared box restow counting.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
import math
from pathlib import Path
from time import monotonic

from ortools.sat.python import cp_model
from stowage.bounds import lower_bound as counting_bound
from stowage.feasibility.tables import build_tables
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1_enabled


def _floor(value, scale=1):
    a, b = float(value).as_integer_ratio()
    return a * scale // b


def _ceil(value, scale=1):
    a, b = float(value).as_integer_ratio()
    return -(-a * scale // b)


def lower_bound(instance, *, budget_s=30., tables=None, seed=0):
    """Return a certified lower bound (BestObjectiveBound, never incumbent).

    Empty arrival, no forecasts or future loads, nonnegative objective weights.
    Mass groups round each box DOWN to a 1 tonne bin; summed capacities round
    UP. Every actual slot maps to a footprint signature of (cover, deck) regions.
    """
    started = monotonic()
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError('budget_s must be finite and positive')
    if (instance.onboard.placements or instance.discharge_list or instance.plan_calls
            or instance.forecasts or instance.onboard.covers_off):
        raise ValueError('Bound is restricted to empty, closed arrival without future loads')
    base = counting_bound(instance)
    T = tables if tables is not None else build_tables(instance)
    legacy_dg_widened = not rule_freeze_v1_enabled() and bool(T.is_dg.any())
    if legacy_dg_widened:
        from ..bounds.unary import optimistic_dg_tables
        T = optimistic_dg_tables(T)
    w = instance.objective
    if min(w.relocate, w.hatch_cover, w.future_hatch_cover, w.future_restow) < 0:
        raise ValueError('Nonnegative objective weights required')
    first_call = {}
    for k in range(instance.call_index + 1, len(instance.rotation.calls)):
        first_call.setdefault(instance.rotation.calls[k].locode, k)
    boxes = {b.id: b for b in instance.containers}
    if any(int(T.units[T.box_index[bid]]) not in (1, 2) for bid in instance.load_list):
        raise ValueError('Footprints must have one or two cells')
    for cover in instance.vessel.hatch_covers:
        for sid in cover.deck_stack_ids:
            if T.stack_cover[T.stack_index[sid]] != cover.id:
                raise ValueError('Deck cover membership is inconsistent')
    for j in range(len(T.stack_ids)):
        cover = T.stack_cover[j]
        if T.stack_deck[j] and cover is not None:
            explicit = next(c for c in instance.vessel.hatch_covers if c.id == cover)
            if T.stack_ids[j] not in explicit.deck_stack_ids:
                raise ValueError('Deck cover membership is inconsistent')
    regions = defaultdict(list)
    for j in range(len(T.stack_ids)):
        regions[(T.stack_cover[j], bool(T.stack_deck[j]))].append(j)
    # A signature retains per-region footprint counts, not stack identity.
    signature_of = {}
    for s in range(len(T.slot_ids)):
        footprint = Counter((T.stack_cover[int(j)], bool(T.stack_deck[int(j)]))
                            for j in T.slot_stacks[s] if j >= 0)
        signature_of[s] = tuple(sorted(footprint.items(), key=lambda item: str(item[0])))
    groups = defaultdict(list)
    for bid in instance.load_list:
        b = T.box_index[bid]
        choices = tuple(sorted({signature_of[s] for s in range(len(T.slot_ids))
                                if T.static[b, s]}, key=str))
        # The union of slots in a group only enlarges choices; in fact choices
        # are identical within the group because they are part of its key.
        key = (int(T.units[b]), first_call[boxes[bid].pod],
               _floor(T.mass[b]) // 1000, _floor(T.height[b], 1000), choices)
        groups[key].append(bid)
    model = cp_model.CpModel()
    allocations = []
    per_region = defaultdict(list)
    hold_edge = defaultdict(list)
    hold_use = defaultdict(list)
    for g, (key, members) in enumerate(groups.items()):
        units, call, mass_t, height_mm, choices = key
        n = len(members)
        row = []
        for k, sig in enumerate(choices):
            x = model.new_int_var(0, n, f'x_{g}_{k}')
            row.append(x)
            holds, decks = [], []
            for (cover, deck), cells in sig:
                per_region[cover, deck].append((x, units, mass_t, height_mm, cells))
                if not deck:
                    if cover is None:
                        raise ValueError('Unmapped hold stack')
                    holds.append(cover)
                    hold_edge[cover, call].append(x)
                    hold_use[cover].append(x)
                elif cover is not None:
                    decks.append(cover)
            allocations.append((x, n, call, tuple(set(decks))))
        model.add(sum(row) == n)
    for region, stacks in regions.items():
        terms = per_region[region]
        model.add(sum(cells*x for x, units, mass, height, cells in terms)
                  <= sum(len(T.stack_cells[j]) for j in stacks))
        # Multiply all mass shares by 2, avoiding fractions for 40/45ft boxes.
        cap = sum(max(T.stack_lim_20[j], T.stack_lim_40[j], T.stack_lim_mixed[j]) for j in stacks)
        if math.isfinite(float(cap)):
            model.add(sum((2//units)*cells*mass*1000*x
                          for x, units, mass, height, cells in terms) <= sum(_ceil(max(T.stack_lim_20[j], T.stack_lim_40[j],
                                                    T.stack_lim_mixed[j]), 2) for j in stacks))
        hcap = sum(T.stack_hmax[j] for j in stacks)
        if math.isfinite(float(hcap)):
            model.add(sum(cells*height*x for x, units, mass, height, cells in terms)
                      <= sum(_ceil(T.stack_hmax[j], 1000) for j in stacks))
    used = {}
    edge = {}
    terms = []
    scale = 1000
    for cover, xs in hold_use.items():
        z = model.new_bool_var(f'used_{cover}')
        model.add(sum(xs) <= len(instance.load_list)*z)
        used[cover] = z
        terms.append(_floor(w.hatch_cover, 2*scale)*z)
    for (cover, call), xs in hold_edge.items():
        z = model.new_bool_var(f'edge_{cover}_{call}')
        model.add(sum(xs) <= len(instance.load_list)*z)
        model.add(z <= used[cover])
        edge[cover, call] = z
        terms.append(_floor(w.future_hatch_cover, 2*scale)*z)
    # Only cover-mediated deck restows survive; in-column overstows are free.
    restow_vars = []
    for k, (x, n, call, decks) in enumerate(allocations):
        for earlier in sorted(set(c for _, c in edge if c < call)):
            triggers = [edge[cover, earlier] for cover in decks if (cover, earlier) in edge]
            if not triggers:
                continue
            active = model.new_bool_var(f'active_{k}_{earlier}')
            model.add_max_equality(active, triggers)
            r = model.new_int_var(0, n, f'restow_{k}_{earlier}')
            model.add(r >= x - n*(1-active))
            restow_vars.append(r)
            terms.append(_floor(w.future_restow, w.future_restow_lifts_per_box*scale)*r)
    model.minimize(sum(terms))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = max(.001, budget_s-(monotonic()-started))
    status = solver.solve(model)
    if status in (cp_model.MODEL_INVALID, cp_model.INFEASIBLE):
        raise RuntimeError(f'Unexpected aggregate relaxation {solver.status_name(status)}')
    shipping = max(0., math.floor(float(solver.best_objective_bound))/scale)
    mandatory = {upper for stack in instance.yard.stacks
                 for k, lower in enumerate(stack.boxes) if lower in set(instance.load_list)
                 for upper in stack.boxes[k+1:] if upper not in set(instance.load_list)}
    assert len(mandatory) == base.relocations
    result = dict(instance_id=instance.id, lower_bound=max(base.weighted, base.relocations*w.relocate+shipping),
                  hatch_restow_lower_bound=shipping, forced_relocations=base.relocations,
                  weighted_forced_relocations=base.relocations*w.relocate,
                  status=solver.status_name(status).lower(), groups=len(groups),
                  variables=len(model.proto.variables), constraints=len(model.proto.constraints),
                  wall_s=monotonic()-started,
                  legacy_dg_unary_widened=legacy_dg_widened,
                  relaxation='aggregate regions; down-rounded mass; no support, E1/E2/E6, DG pair, OOG pair, securing, visibility or whole-ship physics; in-column restows free')
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result.update(relaxation_incumbent=solver.objective_value/scale,
                      incumbent_used_covers=sum(solver.value(z) for z in used.values()),
                      incumbent_port_cover_edges=sum(solver.value(z) for z in edge.values()),
                      incumbent_restowed_boxes=sum(solver.value(z) for z in restow_vars))
    return result

