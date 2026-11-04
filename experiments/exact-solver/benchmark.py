"""Sequential exact-solver measurements, with checkpointed JSONL output.

The tiny fixture family isolates discrete moves, so agreement with the existing
brute-force oracle is a *relaxed* comparison, never a certificate of a safe ship.
Both searches use all physical moves; the oracle optionally checks static rules.
A proof for the complete joint problem requires the operational solver and full
checker. Seeded cases vary yard order, blockers, PODs, masses and lift weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from time import perf_counter

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.model.fixtures import build_instance


def tiny_instance(seed: int) -> Instance:
    """Three-box synthetic family with two load targets and an optional blocker.

    Built using the contract fixture, restricted to one or two deck stacks, a
    three-position yard and no covers. APPROXIMATION: hull placeholders remain;
    this family is deliberately unsuitable for physics/operational certification.
    """
    rng = random.Random(seed)
    data = build_instance().model_dump()
    boxes = data['containers'][1:4]
    ids = [box['id'] for box in boxes]
    for box in boxes:
        box.update(pod=rng.choice(('THBKK', 'THSGZ')), dg=None,
                   special_stowage=[], oog=None, gross_mass_kg=rng.randrange(5000, 14000))
    data.update(id=f'exact_tiny_{seed}', seed=seed, containers=boxes,
                load_list=ids[:2], discharge_list=[])
    data['onboard']['placements'] = []
    layout = seed % 4
    if layout == 0:
        yard = [[ids[0], ids[2]], [ids[1]]]
    elif layout == 1:
        yard = [[ids[0], ids[1], ids[2]]]
    elif layout == 2:
        yard = [[ids[2], ids[0], ids[1]]]
    else:
        yard = [[ids[0]], [ids[1]], [ids[2]]]
    data['yard']['stacks'] = [dict(key=f'A-{i:03}-01', length_ft=20, boxes=stack)
                              for i, stack in enumerate(yard, 1)]
    block = data['port']['yard']['blocks'][0]
    block.update(n_bays=3, n_rows=1)
    data['port']['yard']['blocks'] = [block]
    stacks = [s for s in data['vessel']['stacks']
              if s['id'] in (('00101D',) if seed % 3 == 0 else ('00101D', '00301D'))]
    for stack in stacks:
        stack['hatch_cover_id'] = None
    cells = {cid for stack in stacks for cid in stack['cell_ids']}
    data['vessel'].update(stacks=stacks, hatch_covers=[])
    data['vessel']['cells'] = [c for c in data['vessel']['cells'] if c['id'] in cells]
    data['objective']['relocate'] = float(1 + seed % 3)
    # Synthetic expert policy parameters; no threshold is silently omitted.
    data['rule_overrides'] = {
        'expert.e6.heavy_under_deck': {'heavy_threshold': 30000.},
        'expert.e1.heavy_bottom': {'heavy_threshold': 30000.},
        'expert.e2.weight_order': {'tolerance': 10000.},
    }
    return Instance.model_validate(data)


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps(row, sort_keys=True) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=50)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--time-limit', type=float, default=2.)
    parser.add_argument('--out', type=Path, default=Path('experiments/exact-solver/tiny.jsonl'))
    args = parser.parse_args()
    source_sha256 = hashlib.sha256(Path('src/stowage/solver/exact.py').read_bytes()).hexdigest()
    from stowage.solver.exact import solve_exact
    done = {json.loads(line)['seed'] for line in args.out.read_text().splitlines()} if args.out.exists() else set()
    for seed in range(args.start, args.start + args.count):
        if seed in done:
            continue
        instance = tiny_instance(seed)
        started = perf_counter()
        exact = solve_exact(instance, time_limit_s=args.time_limit, num_workers=1,
                            check_physics=False, check_static_rules=True)
        oracle = brute_force_optimum(instance, time_limit_s=args.time_limit, max_nodes=100_000,
                                     check_static_rules=True)
        row = dict(source_sha256=source_sha256, check_static_rules=True, seed=seed, family='relaxed_tiny', time_limit_s=args.time_limit,
                   num_workers=1, lower_bound=lower_bound(instance).weighted,
                   exact=dict(status=exact.status, objective=exact.objective,
                              lower_bound=exact.lower_bound, gap=exact.gap,
                              expanded_nodes=exact.expanded_nodes, wall_time_s=exact.wall_time_s,
                              metrics=exact.metrics),
                   brute_force=dict(status=oracle.status, objective=oracle.objective,
                                    nodes=oracle.expanded_nodes, wall_time_s=oracle.wall_time_s),
                   total_wall_time_s=perf_counter() - started)
        row['counting_bound_gap'] = (0. if exact.objective == 0 else
                                      (exact.objective-row['lower_bound'])/exact.objective) if exact.objective is not None else None
        append_row(args.out, row)
        print(json.dumps(row), flush=True)
        assert exact.status == 'OPTIMAL', row
        assert oracle.status == 'optimal', row
        assert exact.objective == oracle.objective, row
        assert row['lower_bound'] <= exact.objective, row


if __name__ == '__main__':
    main()
