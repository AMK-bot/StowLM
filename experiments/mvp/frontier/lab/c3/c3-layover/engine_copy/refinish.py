"""Finish plans that ran out of finisher time, with a longer budget, without repeating the search. Same finisher, same checker.

Usage: refinish.py <run_dir> <out_dir> [--finalize-s 600]. Takes each map whose run has unfinished cheaper tries with stored
actions. A row written here replaces the map's result only if it passes and is cheaper; the budget is recorded.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run')
    parser.add_argument('out')
    parser.add_argument('--finalize-s', type=float, default=600.)
    args = parser.parse_args()
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.io import load_instance
    from planner import _finish
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    space = get_action_space('boxslot')
    for path in sorted(Path(args.run).glob('*.json')):
        if path.name == 'AUDIT.json':
            continue
        row = json.loads(path.read_text())
        target = out/path.name
        pending = [t for t in row.get('tries', []) if 'actions' in t and not t.get('passed')
                   and t['search_objective'] < (row.get('weighted') or float('inf'))]
        if target.exists() or not pending:
            continue
        instance = load_instance(ROOT/row['input'])
        record = dict(instance_id=row['instance_id'], input=row['input'], before=row.get('weighted'), finalize_s=args.finalize_s, tries=[])
        for t in sorted(pending, key=lambda x: x['search_objective']):
            actions = [Place(a['box'], a['slot']) for a in t['actions']]
            plan = space.expand(instance, actions)
            result, finished = _finish(instance, plan, seed=0, finalize_s=args.finalize_s, label='refinish')
            finished['search_objective'] = t['search_objective']
            record['tries'].append(finished)
            if finished['passed']:
                record.update(weighted=finished['weighted'], passed=True, plan=json.loads(result.plan.model_dump_json()))
                break
        record.setdefault('passed', False)
        target.write_text(json.dumps(record, indent=1))
        print(row['instance_id'], record['before'], '->', record.get('weighted'), [(x['search_objective'], x['finalize_status'], x['finalize_s']) for x in record['tries']], flush=True)


if __name__ == '__main__':
    main()
