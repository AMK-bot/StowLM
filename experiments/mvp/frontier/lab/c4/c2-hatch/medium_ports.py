"""Certified two-port Hall-capacity refinement of the frozen Cycle 3 bound."""
from __future__ import annotations

from itertools import product
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from time import monotonic

from stowage.bounds import lower_bound as counting_bound
from stowage.feasibility.tables import build_tables
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('c4_medium_layout_bound', HERE/'medium_layout.py')
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


def enumerate_cover_masks(capacities, demands, *, now_cost=2000, edge_cost=2000):
    """Minimum relaxed fixed cost, in caller's integer objective scale.

    A mask is 0=shut, 1=first-port, 2=second-port, 3=both ports. Every legal
    allocation obeys the three Hall inequalities for the two-port subsets.
    Tier/support, individual boxes, eligibility, masses and physics are free.
    """
    if len(demands) != 2 or min(demands) < 0:
        raise ValueError('Exactly two nonnegative port demands required')
    if min(now_cost, edge_cost, *capacities) < 0:
        raise ValueError('Nonnegative capacities and costs required')
    best = None
    witness = None
    feasible_masks = 0
    for masks in product(range(4), repeat=len(capacities)):
        opened = sum(cap for cap, mask in zip(capacities, masks) if mask)
        if opened < sum(demands):
            continue
        first = sum(cap for cap, mask in zip(capacities, masks) if mask & 1)
        if first < demands[0]:
            continue
        second = sum(cap for cap, mask in zip(capacities, masks) if mask & 2)
        if second < demands[1]:
            continue
        feasible_masks += 1
        cost = sum(now_cost*(mask != 0) + edge_cost*mask.bit_count() for mask in masks)
        if best is None or cost < best:
            best, witness = cost, masks
    return dict(cost_scaled=best, masks=witness, feasible_masks=feasible_masks,
                enumerated_masks=4**len(capacities))


def lower_bound(instance, *, budget_s=5., tables=None, base_result=None):
    """Certified port-capacity refinement; optional base_result is trusted.

    The caller supplying base_result is responsible for its certificate/source
    provenance. The CLI accepts only a matching input SHA256 and instance id.
    Without one, run the unchanged frozen bound using the requested budget.
    """
    started = monotonic()
    T = build_tables(instance) if tables is None else tables
    from dataclasses import replace
    from strengthening import safe_unary_mask
    static, unary_info = safe_unary_mask(instance, T)
    T = replace(T, static=static)
    if base_result is None:
        base_result = base.lower_bound(instance, budget_s=budget_s, tables=T)
    if base_result.get('instance_id') != instance.id:
        raise ValueError('Base certificate instance id mismatch')
    if (instance.onboard.placements or instance.discharge_list or instance.plan_calls
            or instance.forecasts or instance.onboard.covers_off):
        raise ValueError('Empty, closed arrival without forecast/future loads required')
    w = instance.objective
    if min(w.relocate, w.hatch_cover, w.future_hatch_cover, w.future_restow) < 0:
        raise ValueError('Nonnegative objective weights required')
    first_call = {}
    for k in range(instance.call_index + 1, len(instance.rotation.calls)):
        first_call.setdefault(instance.rotation.calls[k].locode, k)
    boxes = {b.id: b for b in instance.containers}
    calls = sorted({first_call[boxes[bid].pod] for bid in instance.load_list})
    if len(calls) != 2:
        raise ValueError('Port-capacity refinement requires exactly two future discharge calls')
    if any(int(T.units[T.box_index[bid]]) not in (1, 2) for bid in instance.load_list):
        raise ValueError('One/two-cell footprints required')
    capacities = {c.id: sum(len(T.stack_cells[j]) for j in range(len(T.stack_ids))
                          if not T.stack_deck[j] and T.stack_cover[j] == c.id)
                  for c in instance.vessel.hatch_covers}
    if any(not T.stack_deck[j] and T.stack_cover[j] not in capacities
           for j in range(len(T.stack_ids))):
        raise ValueError('Every hold stack must have a represented cover')
    witnesses = base._e6_witness_covers(instance, T)
    B = {bid for bid, covers in witnesses.items() if covers}
    common = set.intersection(*(set(witnesses[bid]) for bid in B)) if B else set()
    hold_only = {bid for bid in instance.load_list if not any(
        T.static[T.box_index[bid], s] and T.slot_deck[s] for s in range(len(T.slot_ids)))}
    mandatory = B | hold_only
    demands = [sum(int(T.units[T.box_index[bid]]) for bid in mandatory
                   if first_call[boxes[bid].pod] == call) for call in calls]
    scale = 1000
    now_cost = base._floor(w.hatch_cover, 2*scale)
    edge_cost = base._floor(w.future_hatch_cover, 2*scale)
    cover_ids = sorted(capacities)
    case_a = enumerate_cover_masks([capacities[c] for c in cover_ids], demands,
                                  now_cost=now_cost, edge_cost=edge_cost)
    # B nonempty: if one of its boxes is on deck, every common witness cover
    # is used; each has at least one later port edge. If B empty only A exists.
    case_b = len(common)*(now_cost+edge_cost) if B else None
    costs = [value for value in (case_a['cost_scaled'], case_b) if value is not None]
    if not costs:
        raise ValueError('Necessary capacity branches both exclude any complete supported stow')
    shipping = min(costs)/scale
    counting = counting_bound(instance)
    candidate = counting.relocations*w.relocate+shipping
    return dict(instance_id=instance.id,
        lower_bound=max(float(base_result['lower_bound']), candidate, counting.weighted),
        previous_lower_bound=base_result['lower_bound'],
        port_capacity_lower_bound=candidate, port_shipping_lower_bound=shipping,
        forced_relocations=counting.relocations, weighted_forced_relocations=counting.relocations*w.relocate,
        ordinary_heavy_boxes=len(B), common_witness_covers=sorted(common),
        mandatory_hold_cells_by_call=dict(zip(map(str, calls), demands)),
        hold_cell_capacities=capacities, case_a_all_heavy_hold=case_a,
        case_a_mask_covers=cover_ids, case_b_some_heavy_deck_cost_scaled=case_b,
        objective_scale=scale, status='enumerated_capacity_relaxation',
        wall_s=monotonic()-started,
        relaxation='two-port Hall capacity and safe E6 branch disjunction; drop all packing interactions, securing and whole-ship physics')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--base-results')
    parser.add_argument('--budget-s', type=float, default=5.)
    args = parser.parse_args()
    from catalog import load_set
    previous = {}
    if args.base_results:
        previous = {r['input']: r for r in json.loads(Path(args.base_results).read_text())}
    rows = []
    for path in load_set(args.list):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        old = previous.get(path)
        if old is not None and old.get('input_sha256') != digest:
            raise ValueError(f'Base certificate input hash mismatch: {path}')
        row = lower_bound(load_instance(path), budget_s=args.budget_s, base_result=old)
        row.update(input=path, input_sha256=digest)
        rows.append(row)
        print(json.dumps(row), flush=True)
        Path(args.out).write_text(json.dumps(rows, indent=2)+'\n')


if __name__ == '__main__':
    main()
