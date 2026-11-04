"""Global six-cell certificates and checked-plan gaps on the fixed tiny cohort."""
import json
import os
from pathlib import Path
import sys
from time import perf_counter

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(HERE))
from stowage.io import load_instance
from tiny_bound import tiny_global_bound


def mandatory_inventory(instance):
    loads, forced = set(instance.load_list), set()
    for stack in instance.yard.stacks:
        for i, box in enumerate(stack.boxes):
            if box in loads:
                forced.update(b for b in stack.boxes[i+1:] if b not in loads)
    return sorted(forced)


def main():
    started = perf_counter()
    output = HERE / 'results/main'
    rows = []
    for entry in json.loads((HERE / 'cohort-manifest.json').read_text())['maps']:
        if entry['size'] != 'tiny':
            continue
        instance = load_instance(ROOT / entry['path'])
        bound = tiny_global_bound(instance)
        mandatory = mandatory_inventory(instance)
        # Every stranger above a requested box must leave that yard stack;
        # it cannot be loaded productively because it is not on the load list.
        total = bound['minimum_joint_weighted'] + len(mandatory) * instance.objective.relocate
        row = dict(instance_id=instance.id, ports=entry['ports'], input_sha256=entry['sha256'],
                   bound=bound, mandatory_strangers=mandatory, total_lower_bound=total, arms={})
        for arm in ('frontier09', 'engine', 'graph'):
            path = output / arm / f'{instance.id}.json'
            if not path.exists():
                continue
            plan = json.loads(path.read_text())
            if plan['passed']:
                assert plan['lifts'] >= total
                row['arms'][arm] = dict(lifts=plan['lifts'], gap=plan['lifts']-total,
                                        proved_optimal=plan['lifts'] == total)
        rows.append(row)
    result = dict(scope='global tiny geometric D15 bound plus mandatory yard inventory',
                  rows=rows, wall_s=perf_counter()-started,
                  optimum_counts={a: sum(r['arms'].get(a, {}).get('proved_optimal', False) for r in rows)
                                  for a in ('frontier09', 'engine', 'graph')})
    (output / 'global-tiny-bounds.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(maps=len(rows), optimum_counts=result['optimum_counts'], wall_s=result['wall_s'])))


if __name__ == '__main__':
    main()
