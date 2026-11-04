"""Cold D15 lower bound: physical-cell packing, cover graph and necessary securing.

Reuse the Cycle 4 necessary-physics proof and Cycle 3 Hall cover proof. This
module never reads a plan or old result. See BOUND.md for the precise scope.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tiers = _module('c6_lifts_necessary_tiers', LAB/'c4/c2-layout/bound_tiers.py')
ports = _module('c6_lifts_hall_ports', LAB/'c3/c2-layout/bound_ports.py')
_original_attach = tiers.physics_bound.attach


def components(footprints):
    """Connected physical columns: a long box can join its two support columns."""
    parent = {}

    def find(j):
        parent.setdefault(j, j)
        if parent[j] != j:
            parent[j] = find(parent[j])
        return parent[j]

    for footprint in footprints:
        js = tuple(footprint)
        for j in js:
            parent[find(j)] = find(js[0])
    return {j: find(j) for j in parent}


def add_active_root_rows(model, records, *, capacity_n=714000.):
    """records=(count, maximum count, physical columns, force lower bound).

    In any passing image, each occupied column has at most two root end
    frames. Each root carries transverse force <= 2*.85*210000 N. Chain
    paths cannot leave a component of the possible-support graph.
    """
    component = components([js for _, _, js, _ in records])
    per_column, forces = defaultdict(list), defaultdict(list)
    for x, n, js, force in records:
        for j in js:
            per_column[j].append((x, n))
        forces[component[js[0]]].append(math.floor(force)*x)
    active = {}
    for j, xs in per_column.items():
        z = model.new_bool_var(f'c6_active_deck_column_{j}')
        model.add(sum(x for x, _ in xs) >= z)
        model.add(sum(x for x, _ in xs) <= sum(n for _, n in xs)*z)
        active[j] = z
    cap = math.ceil(capacity_n*(1.+1.e-7))
    for root, terms in forces.items():
        model.add(sum(terms) <= cap*sum(z for j, z in active.items() if component[j] == root))
    return dict(active_deck_columns=len(active), support_components=len(forces),
                transverse_capacity_per_active_column_n=cap,
                reference_gm_used=False, securing_gm_floor_m=0.)


def _attach(model, instance, T, allocations):
    metadata = _original_attach(model, instance, T, allocations)
    envelopes = tiers.physics_bound.envelopes
    boxes = {b.id: b for b in instance.containers}
    records = []
    cache = {}
    for allocation in allocations:
        x, members, signature = allocation[:3]
        js = tuple(j for j, _ in signature)
        if not any(T.stack_deck[j] for j in js):
            continue
        base = min(float(T.stack_base_z[j]) for j in js)
        if len(allocation) == 4:
            base = max(base, float(allocation[3]))
        forces = []
        for bid in members:
            key = (bid, base)
            if key not in cache:
                b = boxes[bid]
                height = b.height_m+(b.oog.over_height_m if b.oog else 0.)
                r = envelopes.securing_coefficients(instance, b, on_deck=True,
                            z_lower=base+.45*height, gm_lower=0.)
                # min-end share only weakens the total-force inequality; it
                # also matches the previously audited Cycle 4 coefficient.
                cache[key] = r['transverse_force_n']*r['per_column_share_factor']
            forces.append(cache[key])
        records.append((x, len(members), js, min(forces)))
    metadata['c6_active_roots'] = add_active_root_rows(model, records)
    return metadata


tiers.physics_bound.attach = _attach


def lower_bound(instance, *, budget_s=15., seed=0, tables=None):
    """Necessary relaxation bound among full-check passing complete plans.

    Empty closed arrival; immutable disjoint fixed support; no future loads.
    Every coefficient is necessary at all positive lashing GM, never a fitted
    securing row. CP BestObjectiveBound is used, never the CP incumbent.
    """
    from stowage.feasibility.tables import build_tables
    started = monotonic()
    T = build_tables(instance) if tables is None else tables
    row = tiers.lower_bound(instance, budget_s=budget_s, seed=seed, tables=T)
    try:
        hall = ports.lower_bound(instance, budget_s=budget_s, tables=T, base_result=row)
    except ValueError as exc:
        if 'exactly two future discharge calls' not in str(exc):
            raise
        hall = None
    row['physical_securing_lower_bound'] = row['lower_bound']
    if hall is not None:
        row['lower_bound'] = max(row['lower_bound'], hall['lower_bound'])
        row['port_hall_certificate'] = hall
    row.update(cold=True, proof_scope='fewest weighted lifts among complete full-checker passing plans; D15 fixed stow, no later loads',
               source='c4 physical-cell necessary physics + c3 two-port Hall + c6 active deck-root capacity',
               wall_s=monotonic()-started)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--budget-s', type=float, default=15.)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    output = Path(args.out).resolve()
    if not output.is_relative_to(HERE):
        raise ValueError('Bound output must remain inside c6/c6-lifts')
    from catalog import load_set
    from stowage.io import load_instance
    rows = []
    output.parent.mkdir(parents=True, exist_ok=True)
    for path in load_set(args.list):
        row = lower_bound(load_instance(path), budget_s=args.budget_s, seed=args.seed)
        row.update(input=str(path), input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
        rows.append(row)
        output.write_text(json.dumps(rows, indent=2)+'\n')
        print(json.dumps({k: row[k] for k in ('instance_id', 'lower_bound', 'status', 'wall_s')}), flush=True)


if __name__ == '__main__':
    main()
