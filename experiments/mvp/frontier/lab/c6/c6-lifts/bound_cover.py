"""Compact cold cover/tier relaxation with necessary minimum-motion tension.

Freshly recompute the frozen bound, then spend the remaining deadline on a
coarser model with a stronger necessary securing row. No previous plan or row
is read. See BOUND_COVER.md for the projection proof.
"""
from collections import Counter, defaultdict
from fractions import Fraction
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from time import monotonic

from ortools.sat.python import cp_model
from stowage.feasibility.tables import build_tables
from stowage.io import load_instance
from stowage.checker import lashing

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('c6_frozen_bound_for_cover', HERE/'bound.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
envelopes = old.tiers.physics_bound.envelopes


def down(value):
    return math.floor(Fraction(value))


def tension_coefficient(mass, at, wind, support_lever, height, tier):
    """Outward integer coefficient, N*m, in an aggregate root-tension row.

    Force floor and support lever must already be lower bounds. Negative
    gravity, allowable fitting tension and rod relief are rounded outward.
    """
    F = Fraction
    h, lever = F(height), max(F(0), F(support_lever))
    coefficient = F(mass)*F(at)*(lever+F(.45)*h)+F(wind)*(lever+h/2)
    coefficient -= F(mass)*F(lashing.G)*F(lashing.WIDTH)/2
    guard = F(1.)+F(1.e-7)
    if tier < 2:
        coefficient -= F(300000)*guard*h
    if tier == 0:
        coefficient -= F(2)*F(.85*lashing.LOCK_TENSION_SWL)*F(lashing.WIDTH)*guard
    return down(coefficient)


def fresh_old_with_witnesses(instance, T, *, budget_s, seed):
    """Reuse one current-call E6 witness computation in both old proof arms.

    These imported modules are private to this adapter. Restore helpers in
    finally, and keep no cache between calls or instances.
    """
    original = old.tiers._e6_witness_covers
    original_ports = old.ports.base._e6_witness_covers
    witnesses = None
    def cached(current, tables):
        nonlocal witnesses
        if current is not instance or tables is not T:
            return original(current, tables)
        if witnesses is None:
            witnesses = original(current, tables)
        return witnesses
    old.tiers._e6_witness_covers = cached
    old.ports.base._e6_witness_covers = cached
    try:
        result = old.lower_bound(instance, budget_s=budget_s, seed=seed, tables=T)
    finally:
        old.tiers._e6_witness_covers = original
        old.ports.base._e6_witness_covers = original_ports
    return result, witnesses or {}


def lower_bound(instance, *, budget_s=60., seed=0):
    started = monotonic()
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError('positive finite budget required')
    if not math.isfinite(float(instance.objective.restow)) or instance.objective.restow < 0:
        raise ValueError('finite nonnegative current-restow weight required')
    deadline = started+budget_s
    T = build_tables(instance)
    previous, witnesses = fresh_old_with_witnesses(instance, T,
                        budget_s=max(.001, min(15., .25*budget_s)), seed=seed)
    timing = dict(old_s=monotonic()-started)
    w = instance.objective
    # The frozen adapter has already enforced empty closed arrival, no later
    # loads, disjoint fixed support and nonnegative objective coefficients.
    if deadline-monotonic() < 1.:
        return dict(previous, previous_lower_bound=previous['lower_bound'], cover_lower_bound=None,
                    new_status='setup_budget_exhausted', timing=timing, wall_s=monotonic()-started)
    boxes = {b.id: b for b in instance.containers}
    hmin = min(Fraction(boxes[bid].height_m) for bid in instance.load_list)
    first_call = {}
    for k in range(instance.call_index+1, len(instance.rotation.calls)):
        first_call.setdefault(instance.rotation.calls[k].locode, k)
    region_keys = sorted({(bool(T.stack_deck[j]), str(T.stack_cover[j])) for j in range(len(T.stack_ids))})
    region_index = {key: i for i, key in enumerate(region_keys)}
    region_stacks = {r: [j for j in range(len(T.stack_ids))
                       if (bool(T.stack_deck[j]), str(T.stack_cover[j])) == key]
                     for r, key in enumerate(region_keys)}
    region_of = {j: r for r, js in region_stacks.items() for j in js}
    slot_signature = {}
    signatures = defaultdict(list)
    deck_footprints = []
    for s in range(len(T.slot_ids)):
        cs = [int(c) for c in T.slot_cells[s] if c >= 0]
        sig = tuple(sorted(Counter((region_of[int(T.cell_stack[c])], int(T.cell_k[c])) for c in cs).items()))
        slot_signature[s] = sig
        signatures[sig].append(s)
        js = tuple(sorted({r for (r, _), _ in sig if region_keys[r][0]}))
        if js:
            deck_footprints.append(js)
    component = old.components(deck_footprints)
    root_base = {root: max(float(T.stack_base_z[j]) for r, js in region_stacks.items()
                          if component.get(r) == root for j in js)
                 for root in set(component.values())}
    groups = defaultdict(list)
    for bid in instance.load_list:
        b = T.box_index[bid]
        choices = tuple(sorted({slot_signature[s] for s in range(len(T.slot_ids)) if T.static[b, s]}))
        key = (first_call[boxes[bid].pod], int(T.units[b]),
               down(Fraction(float(T.mass[b]))/2000)*2000,
               down(Fraction(float(T.height[b]))*1000), tuple(witnesses.get(bid, ())), choices)
        groups[key].append(bid)
    model = cp_model.CpModel()
    occupancy, region_load = defaultdict(list), defaultdict(list)
    hold_use, hold_edge = defaultdict(list), defaultdict(list)
    tension, force = defaultdict(list), defaultdict(list)
    allocations = []
    deck_e6 = []
    coefficient_cache = {}
    for g, (key, members) in enumerate(groups.items()):
        call, units, mass, height_mm, e6_covers, choices = key
        xs = []
        for k, sig in enumerate(choices):
            x = model.new_int_var(0, len(members), f'x_{g}_{k}')
            xs.append(x)
            deck_regions = []
            hold_covers = set()
            for (r, tier), cells in sig:
                occupancy[r, tier].append((x, cells))
                region_load[r].append((x, cells, units, mass, height_mm))
                deck, cover = region_keys[r]
                if deck:
                    deck_regions.append(r)
                elif cover != str(None):
                    hold_covers.add(cover)
            for cover in hold_covers:
                hold_use[cover].append(x)
                hold_edge[cover, call].append(x)
            decks = tuple(sorted(set(deck_regions)))
            allocations.append((x, len(members), call, tuple(region_keys[r][1] for r in decks)))
            if decks:
                if e6_covers:
                    deck_e6.append((x, len(members), e6_covers))
                root = component[decks[0]]
                # All real choices behind this aggregate signature must be
                # admitted: minimise physical support height across them.
                support = min(Fraction(float(T.stack_base_z[int(T.cell_stack[c])]))
                              + int(T.cell_k[c])*hmin
                              for s in signatures[sig] for c in T.slot_cells[s] if c >= 0)
                tier = min(t for (r, t), _ in sig)
                lever = max(Fraction(0), support-Fraction(root_base[root]))
                coeffs, forces = [], []
                for bid in members:
                    cache_key = (bid, sig)
                    if cache_key not in coefficient_cache:
                        b = boxes[bid]
                        physical_height = b.height_m+(b.oog.over_height_m if b.oog else 0.)
                        motion = envelopes.motion_floor(instance, gm_lower=0.,
                                     z_lower=envelopes._down(support+Fraction(.45)*Fraction(physical_height)))
                        m = max(b.gross_mass_kg, 2500 if b.length_ft == 20 else 3500)
                        expanded_l = lashing.LENGTH[b.length_ft]+(b.oog.over_length_fore_m+b.oog.over_length_aft_m if b.oog else 0.)
                        wind = max(15700 if b.length_ft == 20 else 31600, expanded_l*physical_height*1000)
                        coefficient_cache[cache_key] = (tension_coefficient(m, motion['at_m_s2'],
                                         wind*(1.-1.e-10), lever, physical_height, tier),
                                         down((m*motion['at_m_s2']+wind)*(1.-1.e-10)))
                    c, f = coefficient_cache[cache_key]
                    coeffs.append(c); forces.append(f)
                tension[root].append(min(coeffs)*x)
                force[root].append(min(forces)*x)
        model.add(sum(xs) == len(members))
    # Cover/tier packing is deliberately weaker than individual physical cells.
    for (r, tier), terms in occupancy.items():
        cap = sum(1 for j in region_stacks[r] for c in T.stack_cells[j] if int(T.cell_k[c]) == tier)
        model.add(sum(c*x for x, c in terms) <= cap)
        if tier:
            model.add(sum(c*x for x, c in terms) <= sum(c*x for x, c in occupancy.get((r, tier-1), [])))
    for r, terms in region_load.items():
        js = region_stacks[r]
        capacities = [float(max(T.stack_lim_20[j], T.stack_lim_40[j], T.stack_lim_mixed[j])) for j in js]
        if all(math.isfinite(c) for c in capacities):
            capacity = sum(map(Fraction, capacities))
            model.add(sum((2//units)*cells*mass*x for x, cells, units, mass, h in terms) <= math.ceil(2*capacity))
        heights = [float(T.stack_hmax[j]) for j in js]
        if all(math.isfinite(h) for h in heights):
            hcap = sum(map(Fraction, heights))
            model.add(sum(cells*h*x for x, cells, units, mass, h in terms) <= math.ceil(1000*hcap))
    for root, terms in tension.items():
        model.add(sum(terms) <= 0)
        columns = sum(len(region_stacks[r]) for r in region_stacks if component.get(r) == root)
        model.add(sum(force[root]) <= math.ceil(714000.*(1.+1.e-7))*columns)
    scale = 1000
    used, edges, objective = {}, {}, []
    for cover, xs in hold_use.items():
        z = model.new_bool_var('used_'+cover)
        model.add(sum(xs) <= len(instance.load_list)*z)
        model.add(sum(xs) >= z)
        used[cover] = z
        objective.append(old.tiers._floor(w.hatch_cover, 2*scale)*z)
    for (cover, call), xs in hold_edge.items():
        z = model.new_bool_var(f'edge_{cover}_{call}')
        model.add(sum(xs) <= len(instance.load_list)*z)
        model.add(sum(xs) >= z)
        edges[cover, call] = z
        objective.append(old.tiers._floor(w.future_hatch_cover, 2*scale)*z)
    e6_cuts = 0
    for x, n, covers in deck_e6:
        for cover in covers:
            if str(cover) in used:
                model.add(x <= n*used[str(cover)])
            else:
                model.add(x == 0)
            e6_cuts += 1
    # A cardinality consequence of the same physical capacity/E6 proof maps
    # directly to actual used-cover indicators in this coarser relaxation.
    model.add(sum(used.values()) >= previous.get('necessary_cover_count', 0))
    restows = []
    for k, (x, n, call, decks) in enumerate(allocations):
        for earlier in sorted({p for c, p in edges if p < call}):
            triggers = [edges[c, earlier] for c in set(decks) if (c, earlier) in edges]
            if not triggers:
                continue
            active = model.new_bool_var(f'restow_trigger_{k}_{earlier}')
            model.add_max_equality(active, triggers)
            z = model.new_int_var(0, n, f'restow_{k}_{earlier}')
            model.add(z >= x-n*(1-active))
            restows.append(z)
            objective.append(old.tiers._floor(w.future_restow, w.future_restow_lifts_per_box*scale)*z)
    model.minimize(sum(objective))
    timing['model_s'] = monotonic()-started-timing['old_s']
    remaining = deadline-monotonic()
    if remaining <= .01:
        return dict(previous, previous_lower_bound=previous['lower_bound'], cover_lower_bound=None,
                    new_status='setup_budget_exhausted', timing=timing, wall_s=monotonic()-started)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = remaining
    status = solver.solve(model)
    if status in (cp_model.INFEASIBLE, cp_model.MODEL_INVALID):
        raise RuntimeError('Unexpected necessary cover relaxation '+solver.status_name(status))
    shipping = max(0., math.floor(solver.best_objective_bound)/scale)
    new_bound = previous['weighted_forced_relocations']+shipping
    timing['solve_s'] = solver.wall_time
    return dict(instance_id=instance.id, lower_bound=max(previous['lower_bound'], new_bound),
                previous_lower_bound=previous['lower_bound'], cover_lower_bound=new_bound,
                previous_status=previous['status'], new_status=solver.status_name(status).lower(),
                forced_relocations=previous['forced_relocations'], hatch_restow_lower_bound=shipping,
                groups=len(groups), variables=len(model.proto.variables), constraints=len(model.proto.constraints),
                e6_witness_boxes=len(witnesses), e6_cuts=e6_cuts,
                cold=True, reference_gm_used=False, securing_gm_floor_m=0., timing=timing,
                wall_s=monotonic()-started, proof_scope=previous['proof_scope'],
                relaxation='cover/tier counts, unary eligibility, outward mass/height capacity, no-gap count, global minimum-motion base-tension and shear; exact securing/E1/E2/physics relaxed; cover-mediated restows only')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--budget-s', type=float, default=60.)
    args = parser.parse_args()
    output = Path(args.out).resolve()
    if not output.is_relative_to(HERE):
        raise ValueError('output outside c6-lifts')
    from catalog import load_set
    rows = []
    output.parent.mkdir(parents=True, exist_ok=True)
    for path in load_set(args.list):
        row = lower_bound(load_instance(path), budget_s=args.budget_s)
        row.update(input=str(path), input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
        rows.append(row)
        output.write_text(json.dumps(rows, indent=2)+'\n')
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
