"""Small read-only graph audit and a necessary-capacity hatch lower bound.

This is not a constructor or full-checker certificate. See THEORY.md.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'experiments/mvp/frontier'),
                str(ROOT / 'experiments/mvp/frontier/lab')]


def matroid_counterexample():
    """The weighted-capacity independence system fails matroid exchange."""
    sizes = {'long': 2, 'short_a': 1, 'short_b': 1}
    a, b = {'long'}, {'short_a', 'short_b'}
    feasible = lambda members: sum(sizes[x] for x in members) <= 2
    assert feasible(a) and feasible(b) and len(a) < len(b)
    assert not any(feasible(a | {x}) for x in b - a)
    return dict(capacity_teu=2, sizes=sizes, A=sorted(a), B=sorted(b),
                exchange_candidates={x: feasible(a | {x}) for x in sorted(b - a)},
                verdict='not a matroid; greedy exactness does not follow')


def elementary_reefer_certificate(instance):
    """Independent four-lift proof, using geometry instead of the static mask.

    Absence of every deck plug forces each loaded reefer into a covered hold.
    A closed cover needs off/on now and at the reefer's future discharge call.
    This proves a bound, not existence of any legal or checker-passing plan.
    """
    stacks = {s.id: s for s in instance.vessel.stacks}
    if instance.onboard.placements or instance.onboard.covers_off:
        return None
    if any(c.reefer_plug and stacks[c.stack_id].on_deck for c in instance.vessel.cells):
        return None
    weights = instance.objective
    if min(weights.relocate, weights.restow, weights.future_restow,
           weights.hatch_cover, weights.future_hatch_cover) < 0:
        return None
    for box in instance.containers:
        if box.id not in instance.load_list or not box.reefer:
            continue
        future = next((c.index for c in instance.rotation.calls
                       if c.index > instance.call_index and c.locode == box.pod), None)
        if future is not None:
            return dict(box=box.id, future_call=future, deck_plug_cells=0,
                        hatch_lifts_lower_bound=4,
                        weighted_lifts_lower_bound=2 * weights.hatch_cover + 2 * weights.future_hatch_cover,
                        reason='loaded reefer must use covered hold; off/on now and at its POD')
    return None


def hatch_relaxation(problem, *, time_limit_s=0.2):
    """Bound raw hatch lifts by grouping boxes with equal static area choices.

    Every legal assignment induces this cell-capacity assignment, but the
    reverse need not be legal. The objective is only hatch lifts. On timeout,
    report the solver's dual bound, never its feasible incumbent as a bound.
    Onboard cargo is deliberately omitted (a relaxation), as are all dynamic
    packing/physics rules. Cross-area spanning slots cause a safe fallback.
    """
    from ortools.sat.python import cp_model
    from experiments.mvp.strategy.headroom.layout import BlockModel

    p, t = problem, problem.engine.T
    blocks = BlockModel.from_instance(p.instance)
    by_id = {b.id: b for b in blocks.blocks}
    areas = tuple(by_id)
    groups = Counter()
    for box in p.instance.load_list:
        row = t.box_index[box]
        choices = set()
        for s in range(len(t.slot_ids)):
            if not t.static[row, s]:
                continue
            touched = blocks.blocks_of_slot(t.slot_ids[s])
            if len(touched) != 1:
                return dict(lower_bound=0, status='cross_area_slot_safe_fallback')
            choices.update(touched)
        if not choices:
            return dict(lower_bound=None, status='no_static_area')
        later = next((c.index for c in p.instance.rotation.calls
                      if c.index > p.instance.call_index and c.locode == p.boxes[box].pod), None)
        groups[int(t.units[row]), later, tuple(sorted(choices))] += 1
    model = cp_model.CpModel()
    n, at_area = {}, defaultdict(list)
    cover_use, port_use = {}, {}
    for g, ((units, port, choices), count) in enumerate(groups.items()):
        terms = []
        for area in choices:
            var = n[g, area] = model.NewIntVar(0, count, f'n{g}_{area}')
            terms.append(var)
            at_area[area].append(units * var)
            block = by_id[area]
            if block.zone != 'hold':
                continue
            cover = block.hatch_group
            if cover not in cover_use:
                cover_use[cover] = model.NewBoolVar(f'use_{cover}')
            model.Add(var <= count * cover_use[cover])
            if port is not None:
                key = cover, port
                if key not in port_use:
                    port_use[key] = model.NewBoolVar(f'port_{cover}_{port}')
                model.Add(var <= count * port_use[key])
        model.Add(sum(terms) == count)
    for area in areas:
        model.Add(sum(at_area[area]) <= len(by_id[area].cell_ids))
    initial_off = set(p.instance.onboard.covers_off)
    objective = len(initial_off) + sum(2 * flag for cover, flag in cover_use.items()
                                     if cover not in initial_off) + 2 * sum(port_use.values())
    model.Minimize(objective)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.Solve(model)
    if status == cp_model.INFEASIBLE:
        return dict(lower_bound=None, status='capacity_relaxation_infeasible')
    bound = max(0, math.ceil(solver.BestObjectiveBound() - 1e-7))
    return dict(lower_bound=bound, status=solver.StatusName(status), groups=len(groups),
                wall_s=solver.WallTime(), incumbent=(solver.ObjectiveValue()
                if status in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None))


def audit(instance, *, baseline=None, time_limit_s=0.2):
    from alns import Problem
    from experiments.mvp.strategy.headroom.layout import BlockModel
    p = Problem(instance)
    blocks = BlockModel.from_instance(instance)
    loads = [p.boxes[b] for b in instance.load_list]
    ports = Counter(str(b.pod) for b in loads)
    teu = Counter()
    for b in loads:
        teu[str(b.pod)] += 1 if b.length_ft == 20 else 2
    pairs = sum(a * b for i, a in enumerate(ports.values()) for b in list(ports.values())[i + 1:])
    forced_hold = [b.id for b in loads
                   if p.engine.T.static[p.engine.T.box_index[b.id]].any()
                   and not (p.engine.T.static[p.engine.T.box_index[b.id]] & p.engine.T.slot_deck).any()]
    stored_static = None
    if (baseline or {}).get('stored'):
        stored_static = all(p.engine.T.static[p.engine.T.box_index[b], p.engine.T.slot_index[s]]
                            for b, s in baseline['stored'].items())
    return dict(instance_id=instance.id, boxes=len(loads), ports=dict(ports), port_teu=dict(teu),
                lengths=dict(Counter(b.length_ft for b in loads)), covers=len(instance.vessel.hatch_covers),
                initial_onboard=len(instance.onboard.placements),
                initial_covers_off=dict(instance.onboard.covers_off), forced_hold_boxes=forced_hold,
                stored_all_slots_static_eligible=bool(stored_static) if stored_static is not None else None,
                elementary_reefer_certificate=elementary_reefer_certificate(instance),
                blocks=[dict(id=b.id, cells=len(b.cell_ids), stacks=len(b.stack_ids)) for b in blocks.blocks],
                possible_cross_port_box_pairs=pairs,
                hatch_port_activation_vertices=len(instance.vessel.hatch_covers) * len(ports),
                hatch_bound=hatch_relaxation(p, time_limit_s=time_limit_s),
                stored_parts=(baseline or {}).get('parts'), stored_lifts=(baseline or {}).get('lifts'),
                stored_passed=(baseline or {}).get('passed'))


def main():
    from catalog import load_baseline, load_set, ensure_safe
    from stowage.io import load_instance
    parser = argparse.ArgumentParser()
    parser.add_argument('--set', choices=['quick_small', 'quick_medium', 'medium12'])
    parser.add_argument('--instance')
    parser.add_argument('--bound-s', type=float, default=0.2)
    args = parser.parse_args()
    paths = load_set(args.set) if args.set else [ensure_safe(args.instance)] if args.instance else []
    baseline = load_baseline()
    rows = []
    for path in paths:
        instance = load_instance(ROOT / path)
        rows.append(audit(instance, baseline=baseline.get(instance.id), time_limit_s=args.bound_s))
    print(json.dumps(dict(matroid=matroid_counterexample(), maps=rows), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
