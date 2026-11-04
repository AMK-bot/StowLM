"""Necessary cell/tier packing, support-height physics and port-cover bound.

Necessary unary, physical-stack capacity, hold length-class and safe E6 cuts
survive. No pure-page or fitted securing approximation is used. Cross-cover
footprint signatures retain shared box restow counting.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
import math
from pathlib import Path
from time import monotonic
import importlib.util

from ortools.sat.python import cp_model
from stowage.bounds import lower_bound as counting_bound
from stowage.feasibility.tables import build_tables
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1_enabled

_physics_spec = importlib.util.spec_from_file_location('c4_layout_necessary_physics', Path(__file__).with_name('physics_bound.py'))
physics_bound = importlib.util.module_from_spec(_physics_spec)
_physics_spec.loader.exec_module(physics_bound)


def minimum_covers(required, capacities):
    """Cardinality consequence of a nonnegative additive resource inequality."""
    if required <= 0:
        return 0
    supplied = 0
    for k, capacity in enumerate(sorted(capacities, reverse=True), 1):
        supplied += capacity
        if supplied >= required:
            return k
    return len(capacities) + 1


def _e6_witness_covers(instance, T):
    """Safe empty-bottom E6 witnesses, never assuming heavy implies hold.

    The cuts are disabled with any lateral OOG or activated soft E6. For an
    ordinary non-DG box, outside-stack cargo cannot otherwise invalidate an
    empty-bottom witness. Validate the witness using the production checker.
    """
    if (rule_freeze_v1_enabled() or T.e6_fallback is not True
            or T.e6_threshold is None):
        return {}
    boxes = {b.id: b for b in instance.containers}
    if any(b.oog and any((b.oog.over_width_port_m, b.oog.over_width_starboard_m,
                         b.oog.over_length_fore_m, b.oog.over_length_aft_m))
           for b in boxes.values()):
        return {}
    from stowage.checker.rules import placement_violations
    empty = instance.onboard.model_copy(update={'placements': [], 'covers_off': {}})
    bottom = []
    for s, slot in enumerate(T.slot_ids):
        if T.slot_deck[s]:
            continue
        physical = [int(c) for c in T.slot_cells[s] if c >= 0]
        if any(T.cell_k[c] != 0 for c in physical):
            continue
        covers = {T.stack_cover[int(j)] for j in T.slot_stacks[s] if j >= 0}
        if len(covers) == 1 and None not in covers:
            bottom.append((s, slot, next(iter(covers))))
    result = {}
    for bid in instance.load_list:
        box = boxes[bid]
        if (box.gross_mass_kg < T.e6_threshold or box.dg is not None
                or box.oog is not None or box.special_stowage):
            continue
        bi, covers = T.box_index[bid], set()
        for s, slot, cover in bottom:
            if cover in covers or not T.static[bi, s]:
                continue
            if not any(v.severity == 'hard' for v in placement_violations(
                    instance, empty, box, slot, call_index=instance.call_index)):
                covers.add(cover)
        result[bid] = tuple(sorted(covers))
    return result


def _floor(value, scale=1):
    a, b = float(value).as_integer_ratio()
    return a * scale // b


def _ceil(value, scale=1):
    a, b = float(value).as_integer_ratio()
    return -(-a * scale // b)


def lower_bound(instance, *, budget_s=30., tables=None, seed=0, base_result=None):
    """Return max(analytic capacity certificate, BestObjectiveBound).

    Empty arrival, no forecasts or future loads, nonnegative objective weights.
    Mass groups round each box DOWN to kg; capacities round UP. Every actual
    slot maps to its exact physical-cell footprint; actual support heights are
    relaxed to global minimum ordinary-height prefixes.
    """
    started = monotonic()
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError('budget_s must be finite and positive')
    if (instance.onboard.placements or instance.discharge_list or instance.plan_calls
            or instance.forecasts or instance.onboard.covers_off):
        raise ValueError('Bound is restricted to empty, closed arrival without future loads')
    base = counting_bound(instance)
    T = tables if tables is not None else build_tables(instance)
    w = instance.objective
    if min(w.relocate, w.hatch_cover, w.future_hatch_cover, w.future_restow) < 0:
        raise ValueError('Nonnegative objective weights required')
    first_call = {}
    for k in range(instance.call_index + 1, len(instance.rotation.calls)):
        first_call.setdefault(instance.rotation.calls[k].locode, k)
    boxes = {b.id: b for b in instance.containers}
    from stowage.checker.visibility import _fixed_loads
    fixed_stack_ids = {stack.id for stack in instance.vessel.fixed_load_stacks}
    fixed_occupied = {cid for load in _fixed_loads(instance.vessel)
                      for box in (load.cargo_boxes or ()) for cid in box.cell_ids}
    if fixed_stack_ids.intersection(T.stack_ids) or fixed_occupied.intersection(T.cell_ids):
        raise ValueError('Tier floors require background cargo stacks disjoint from decision stacks')
    minimum_height = min(boxes[bid].height_m for bid in instance.load_list)
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
        regions[j].append(j)
    witnesses = _e6_witness_covers(instance, T)
    # Keep physical tier/cell identity; actual heights still have lower floors.
    signature_of = {}
    for s in range(len(T.slot_ids)):
        footprint = Counter(int(j) for j in T.slot_stacks[s] if j >= 0)
        physical_signature = tuple(sorted(footprint.items(), key=lambda item: str(item[0])))
        cells = tuple(sorted(int(c) for c in T.slot_cells[s] if c >= 0))
        signature_of[s] = (physical_signature, cells)
    groups = defaultdict(list)
    for bid in instance.load_list:
        b = T.box_index[bid]
        choices = tuple(sorted({signature_of[s] for s in range(len(T.slot_ids))
                                if T.static[b, s]}, key=str))
        # The union of slots in a group only enlarges choices; in fact choices
        # are identical within the group because they are part of its key.
        key = (int(T.units[b]), first_call[boxes[bid].pod],
               _floor(T.mass[b]), _floor(T.height[b], 1000),
               int(T.length[b]), witnesses.get(bid, ()), choices)
        groups[key].append(bid)
    model = cp_model.CpModel()
    allocations = []
    physics_allocations = []
    per_cell = defaultdict(list)
    per_region = defaultdict(list)
    hold_edge = defaultdict(list)
    hold_use = defaultdict(list)
    per_stack_lengths = defaultdict(lambda: defaultdict(list))
    deck_e6 = []
    for g, (key, members) in enumerate(groups.items()):
        units, call, mass_kg, height_mm, length, e6_covers, choices = key
        n = len(members)
        row = []
        for k, (sig, slot_cells) in enumerate(choices):
            x = model.new_int_var(0, n, f'x_{g}_{k}')
            row.append(x)
            holds, decks = [], []
            for j, cells in sig:
                cover, deck = T.stack_cover[j], bool(T.stack_deck[j])
                per_region[j].append((x, units, mass_kg, height_mm, cells))
                per_stack_lengths[j][length == 20].append(x)
                if not deck:
                    if cover is None:
                        raise ValueError('Unmapped hold stack')
                    holds.append(cover)
                    hold_edge[cover, call].append(x)
                    hold_use[cover].append(x)
                elif cover is not None:
                    decks.append(cover)
            allocations.append((x, n, call, tuple(set(decks))))
            for cell in slot_cells:
                per_cell[cell].append(x)
            support_z = physics_bound.tier_support_floor(T, slot_cells, minimum_height)
            physics_allocations.append((x, members, sig, support_z))
            if decks and e6_covers:
                deck_e6.append((x, n, e6_covers))
        model.add(sum(row) == n)
    for cell in range(len(T.cell_ids)):
        xs = per_cell[cell]
        model.add(sum(xs) <= 1)
        stack, tier = int(T.cell_stack[cell]), int(T.cell_k[cell])
        if tier:
            lower = T.stack_cells[stack][tier-1]
            model.add(sum(xs) <= sum(per_cell[lower]))
    for region, stacks in regions.items():
        terms = per_region[region]
        model.add(sum(cells*x for x, units, mass, height, cells in terms)
                  <= sum(len(T.stack_cells[j]) for j in stacks))
        # Multiply all mass shares by 2, avoiding fractions for 40/45ft boxes.
        cap = sum(max(T.stack_lim_20[j], T.stack_lim_40[j], T.stack_lim_mixed[j]) for j in stacks)
        if math.isfinite(float(cap)):
            model.add(sum((2//units)*cells*mass*x
                          for x, units, mass, height, cells in terms) <= sum(_ceil(max(T.stack_lim_20[j], T.stack_lim_40[j],
                                                    T.stack_lim_mixed[j]), 2) for j in stacks))
        hcap = sum(T.stack_hmax[j] for j in stacks)
        if math.isfinite(float(hcap)):
            model.add(sum(cells*height*x for x, units, mass, height, cells in terms)
                      <= sum(_ceil(T.stack_hmax[j], 1000) for j in stacks))
        # The unchanged full securing checker rejects mixed 20/long holds.
        # This condition is exact and independent of motion/GM assumptions.
        j = stacks[0]
        if not T.stack_deck[j]:
            long_hold = model.new_bool_var(f'long_hold_{j}')
            model.add(sum(per_stack_lengths[j][True]) <= len(instance.load_list)*(1-long_hold))
            model.add(sum(per_stack_lengths[j][False]) <= len(instance.load_list)*long_hold)
    used = {}
    edge = {}
    terms = []
    scale = 1000
    for cover, xs in hold_use.items():
        z = model.new_bool_var(f'used_{cover}')
        # A long box occurs once at each physical support, up to twice here.
        model.add(sum(xs) <= 2*len(instance.load_list)*z)
        model.add(sum(xs) >= z)
        used[cover] = z
        terms.append(_floor(w.hatch_cover, 2*scale)*z)
    for (cover, call), xs in hold_edge.items():
        z = model.new_bool_var(f'edge_{cover}_{call}')
        model.add(sum(xs) <= 2*len(instance.load_list)*z)
        model.add(sum(xs) >= z)
        model.add(z <= used[cover])
        edge[cover, call] = z
        terms.append(_floor(w.future_hatch_cover, 2*scale)*z)
    e6_cuts = 0
    for x, n, covers in deck_e6:
        for cover in covers:
            if cover in used:
                model.add(x <= n*used[cover])
            else:
                model.add(x == 0)
            e6_cuts += 1

    # A shut cover has zero usable hold capacity. Explicit cardinality cuts
    # are necessary consequences of capacity, not empirical page restrictions.
    hold_caps = {cover.id: sum(len(T.stack_cells[j]) for j in range(len(T.stack_ids))
                  if not T.stack_deck[j] and T.stack_cover[j] == cover.id)
                 for cover in instance.vessel.hatch_covers}
    deck_capacity = sum(len(T.stack_cells[j]) for j in range(len(T.stack_ids)) if T.stack_deck[j])
    forced_hold_cells = sum(int(T.units[T.box_index[bid]]) for bid in instance.load_list
        if not any(T.static[T.box_index[bid], s] and T.slot_deck[s] for s in range(len(T.slot_ids))))
    required_hold_cells = max(forced_hold_cells,
        sum(int(T.units[T.box_index[bid]]) for bid in instance.load_list)-deck_capacity)
    cardinality = minimum_covers(required_hold_cells, hold_caps.values())
    witness_ids = [bid for bid, covers in witnesses.items() if covers]
    common_witnesses = set.intersection(*(set(witnesses[bid]) for bid in witness_ids)) if witness_ids else set()
    # Either some witness-family box is on deck, forcing every common witness
    # cover open, or the entire family (plus hold-only cargo) must fit below.
    must_hold_ids = {bid for bid in instance.load_list
        if not any(T.static[T.box_index[bid], s] and T.slot_deck[s] for s in range(len(T.slot_ids)))}
    conditional_hold_cells = sum(int(T.units[T.box_index[bid]])
                                 for bid in must_hold_ids | set(witness_ids))
    conditional_count = minimum_covers(conditional_hold_cells, hold_caps.values())
    e6_cardinality = min(len(common_witnesses), conditional_count) if common_witnesses else 0
    cardinality = max(cardinality, e6_cardinality)
    model.add(sum(used.values()) >= cardinality)
    model.add(sum(edge.values()) >= sum(used.values()))
    model.add(sum(terms) >= cardinality*(_floor(w.hatch_cover, 2*scale)
                                        + _floor(w.future_hatch_cover, 2*scale)))
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
    physics = physics_bound.attach(model, instance, T, physics_allocations)
    model.minimize(sum(terms))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = max(.001, budget_s-(monotonic()-started))
    status = solver.solve(model)
    if status in (cp_model.MODEL_INVALID, cp_model.INFEASIBLE):
        raise RuntimeError(f'Unexpected aggregate relaxation {solver.status_name(status)}')
    capacity_shipping = cardinality*(_floor(w.hatch_cover, 2*scale)
                                    + _floor(w.future_hatch_cover, 2*scale))/scale
    shipping = max(capacity_shipping, 0., math.floor(float(solver.best_objective_bound))/scale)
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
                  necessary_physics=physics,
                  minimum_support_height_m=minimum_height,
                  retained_physical_cells=len(per_cell),
                  required_hold_cells=required_hold_cells, necessary_cover_count=cardinality,
                  capacity_shipping_lower_bound=capacity_shipping,
                  e6_common_witness_covers=sorted(common_witnesses),
                  e6_conditional_hold_cells=conditional_hold_cells,
                  e6_conditional_cover_count=conditional_count,
                  hold_cell_capacities=hold_caps, e6_witness_boxes=len(witnesses), e6_cuts=e6_cuts,
                  relaxation='exact physical cell footprints, no-gap prefix and minimum-height support floors plus necessary displacement, weather vertical moment and global minimum-motion securing rows; no exact support height coupling, E1/E2, DG/OOG pairs, exact securing, visibility or finite-angle righting area; safe bottom-witness E6 cuts only; no mixed20/long holds; in-column restows free')
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result.update(relaxation_incumbent=solver.objective_value/scale,
                      incumbent_used_covers=sum(solver.value(z) for z in used.values()),
                      incumbent_port_cover_edges=sum(solver.value(z) for z in edge.values()),
                      incumbent_restowed_boxes=sum(solver.value(z) for z in restow_vars))
    if base_result is not None:
        if base_result.get('instance_id') != instance.id:
            raise ValueError('Base certificate instance id mismatch')
        result['physics_cp_lower_bound'] = result['lower_bound']
        result['previous_port_hall_lower_bound'] = base_result['lower_bound']
        result['lower_bound'] = max(result['lower_bound'], base_result['lower_bound'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--budget-s', type=float, default=30.)
    parser.add_argument('--base-results')
    args = parser.parse_args()
    # The score runner's approved-list gate establishes TRAIN provenance.
    from catalog import load_set
    paths = load_set(args.list)
    rows = []
    previous = {r['input']: r for r in json.loads(Path(args.base_results).read_text())} if args.base_results else {}
    for path in paths:
        instance = load_instance(path)
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        old = previous.get(path)
        if old is not None and old.get('input_sha256') != digest:
            raise ValueError(f'Base certificate input hash mismatch: {path}')
        row = lower_bound(instance, budget_s=args.budget_s, base_result=old)
        row.update(input=path, input_sha256=digest)
        rows.append(row)
        print(json.dumps(row), flush=True)
        Path(args.out).write_text(json.dumps(rows, indent=2)+'\n')


if __name__ == '__main__':
    main()
