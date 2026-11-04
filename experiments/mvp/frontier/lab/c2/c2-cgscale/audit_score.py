"""Fresh-process replay of saved constructor certificates; full check saved passes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = HERE.parents[1]
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(LAB)]

from catalog import ensure_safe
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check
from stowage.io import load_instance
from stowage.model.plan import MovePlan
from stowage.moves import apply_plan


def audit(folder):
    rows = []
    for path in sorted(folder.glob('*.json')):
        stored = json.loads(path.read_text())
        if 'instance_id' not in stored:
            continue
        instance = load_instance(ROOT/ensure_safe(stored['input']))
        meta = stored.get('method_metadata', {})
        certificate = meta.get('replay_actions')
        row = dict(instance_id=instance.id, n_boxes=len(instance.load_list),
                   claimed_complete=bool(meta.get('replay_complete')), replay_legal=False,
                   full_checker_pass=False, scorecard_pass=bool(stored.get('passed')))
        started = monotonic()
        if certificate:
            actions = [Place(item['box'], item['slot']) for item in certificate]
            plan = get_action_space('boxslot').expand(instance, actions)
            replay = apply_plan(instance, plan)
            loaded = {item.container_id for item in replay.final_state.ship.placements}
            row.update(replay_legal=bool(replay.legal and set(instance.load_list) <= loaded),
                       loaded=len(loaded & set(instance.load_list)),
                       replay_lifts=float(replay.count.weighted_nonproductive))
        if stored.get('passed'):
            report = check(instance, MovePlan.model_validate(stored['plan']))
            row['full_checker_pass'] = bool(report.passed)
            row['full_checker_violations'] = len(report.violations)
        row['audit_s'] = monotonic()-started
        rows.append(row)
        print(json.dumps(row), flush=True)
    return dict(rows=rows, maps=len(rows),
                complete_replay_legal=sum(row['replay_legal'] for row in rows),
                complete_428_replay_legal=sum(row['replay_legal'] and row['n_boxes'] == 428 for row in rows),
                full_checker_passes=sum(row['full_checker_pass'] for row in rows),
                claims_match=all(row['claimed_complete'] == row['replay_legal'] and
                                 row['scorecard_pass'] == row['full_checker_pass'] for row in rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, default=HERE/'results/score-medium12')
    parser.add_argument('--out', type=Path, default=HERE/'results/score-audit.json')
    args = parser.parse_args()
    result = audit(args.folder)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    if not result['claims_match']:
        raise SystemExit('saved replay/pass certificate mismatch')
