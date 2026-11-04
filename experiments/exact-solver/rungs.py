"""Cap every rung probe; a time limit is evidence of difficulty, never a proof.

Compare free relocation actions with default-only on identical inputs. Only two
OPTIMAL results establish whether the default rule loses objective value. An
unproved incumbent's overrides cannot answer whether *every* optimum needs one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from stowage.bounds import lower_bound
from stowage.io import load_instance
from stowage.solver.exact import solve_exact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rungs', nargs='+', default=['tiny', 'small', 'medium'])
    parser.add_argument('--time-limit', type=float, default=1.)
    parser.add_argument('--physics', action='store_true')
    parser.add_argument('--out', type=Path, default=Path('experiments/exact-solver/rungs.jsonl'))
    args = parser.parse_args()
    source_sha256 = hashlib.sha256(Path('src/stowage/solver/exact.py').read_bytes()).hexdigest()
    done = {json.loads(line)['key'] for line in args.out.read_text().splitlines()} if args.out.exists() else set()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for rung in args.rungs:
        for path in sorted(Path('experiments/instances', rung).glob('seed-*.json')):
            if path.name.endswith('.audit.json'):
                continue
            instance = load_instance(path)
            for default in (False, True):
                key = f'{rung}/{path.name}/physics={args.physics}/default={default}'
                if key in done:
                    continue
                started = perf_counter()
                result = solve_exact(instance, time_limit_s=args.time_limit, max_nodes=10000,
                                     num_workers=1, check_physics=args.physics,
                                     default_relocations_only=default)
                row = dict(source_sha256=source_sha256, key=key, rung=rung, instance_id=instance.id,
                           n_load=len(instance.load_list), check_physics=args.physics,
                           default_relocations_only=default, num_workers=1,
                           time_limit_s=args.time_limit, lower_bound=lower_bound(instance).weighted,
                           status=result.status, objective=result.objective, gap=result.gap,
                           expanded_nodes=result.expanded_nodes,
                           wall_time_s=result.wall_time_s, total_wall_time_s=perf_counter()-started,
                           metrics=result.metrics)
                row['counting_bound_gap'] = (0. if result.objective == 0 else
                                           (result.objective-row['lower_bound'])/result.objective) if result.objective is not None else None
                with args.out.open('a') as stream:
                    stream.write(json.dumps(row, sort_keys=True)+'\n')
                print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
