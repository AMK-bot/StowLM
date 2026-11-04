"""D151: experiments/mvp/sft/stowlm-v3/data from best-d151/: 8.2's maps with the best verified teacher, 8.2's val.jsonl byte
for byte. Gates: rung counts match the admitted list (713 + 540 when nothing was excluded), val is 40 + 40, train and val
share no map, and every row where gold stayed has 8.2's exact input_ids. Publishes by one rename, with manifest.json.
Usage: build_83.py [workers=4]
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import d151

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT/'experiments/mvp/sft/stowlm-v3/data'
PYTHON = str(ROOT/'.venv/bin/python')


def rows_of(path):
    with open(path) as stream:
        return [json.loads(line) for line in stream]


def check_rows(train, v2_train, v2_val, admitted, excluded):
    """Every gate that needs the built rows; returns the comparison with 8.2. Raises on any failure."""
    by_id = {a['instance_id']: a for a in admitted}
    rungs = Counter(by_id[row['instance_id']]['rung'] for row in train)
    expected = dict(d151.TRAIN_RUNG)
    for item in excluded:
        expected[item['rung']] -= 1
    if dict(rungs) != expected or len(train) != len(by_id):
        raise RuntimeError(f'train rows {dict(rungs)}, expected {expected}')
    val_rungs = Counter(row['meta']['rung'] for row in v2_val)
    if dict(val_rungs) != d151.VAL_RUNG:
        raise RuntimeError(f'val rows {dict(val_rungs)}, expected {d151.VAL_RUNG}')
    overlap = {row['instance_id'] for row in train} & {row['instance_id'] for row in v2_val}
    if overlap:
        raise RuntimeError(f'train and val share maps: {sorted(overlap)[:3]}')
    old = {row['instance_id']: row for row in v2_train}
    changed_gold, two_step_equal, two_step_differ = [], 0, 0
    for row in train:
        same = row['input_ids'] == old[row['instance_id']]['input_ids']
        if by_id[row['instance_id']]['source'] == 'gold':
            if not same:
                changed_gold.append(row['instance_id'])
        else:
            two_step_equal += same
            two_step_differ += not same
    if changed_gold:
        raise RuntimeError(f'{len(changed_gold)} kept-gold rows differ from 8.2: {changed_gold[:3]}')
    return dict(kept_gold_rows_identical=sum(a['source'] == 'gold' for a in admitted), two_step_rows_differ=two_step_differ,
                two_step_rows_identical=two_step_equal)


def main():
    workers = sys.argv[1] if len(sys.argv) > 1 else '4'
    manifest, admission = d151.read(HERE/'d151-manifest.json'), d151.read(HERE/'admitted.json')
    if admission['uuid'] != manifest['uuid'] or not admission['change_proof']['passed']:
        raise SystemExit('admission does not belong to this manifest')
    entries = {e['case']: e for e in manifest['entries']}
    admitted = [dict(a, instance_id=entries[a['case']]['instance_id']) for a in admission['rows']]
    temporary = OUT.with_name(f'data.tmp-{manifest["uuid"]}')
    if temporary.exists():
        shutil.rmtree(temporary)
    with open(HERE/'build-83.log', 'w') as log:
        subprocess.run([PYTHON, str(ROOT/'experiments/mvp/sft/dataset/build.py'), '--out', str(temporary),
                        '--train', str(HERE/'best-d151'), '--workers', workers], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    train, v2_train, v2_val = rows_of(temporary/'train.jsonl'), d151.v2_rows('train'), d151.v2_rows('val')
    comparison = check_rows(train, v2_train, v2_val, admitted, admission['excluded'])
    shutil.copyfile(d151.V2/'data/val.jsonl', temporary/'val.jsonl')
    if d151.sha(temporary/'val.jsonl') != d151.sha(d151.V2/'data/val.jsonl'):
        raise RuntimeError('val.jsonl is not byte-identical')
    stats, old_stats = d151.read(temporary/'stats.json'), d151.read(d151.V2/'data/stats.json')
    stats['splits']['val'] = old_stats['splits']['val']
    d151.write(temporary/'stats.json', stats)
    rungs = ('tiny', 'small')
    mix = {r: dict(Counter(a['source'] for a in admission['rows'] if a['rung'] == r)) for r in rungs}
    new, old = stats['splits']['train'], old_stats['splits']['train']
    d151.write(temporary/'manifest.json', dict(
        schema='stowlm-v3-data-v1', decision='D151/D152', teacher_uuid=manifest['uuid'], launch_id=admission['launch_id'],
        outcome_epoch=manifest['outcome_epoch'], checker_epoch=manifest['checker_epoch'], budget_s=manifest['budget_s'],
        train_rows=len(train), val_rows=len(v2_val), val_sha256=d151.sha(temporary/'val.jsonl'), excluded=admission['excluded'],
        excluded_fraction=admission['excluded_fraction'], provenance_mix=mix, comparison_with_82=comparison,
        tokens=dict(v3=dict(total=new['total_tokens']['sum'], completion=new['completion_tokens']['sum'], actions=new['n_actions']['sum']),
                    v2=dict(total=old['total_tokens']['sum'], completion=old['completion_tokens']['sum'], actions=old['n_actions']['sum'])),
        change_proof=admission['change_proof'], audit=d151.read(HERE/'audit-sample.json'),
        gold_only_label='small gold_warmstart rows: stored gold re-verified with verify_candidate under this epoch (D152 P1-2)',
        val_note='8.2 val.jsonl unchanged, so its tiny rows still hold stored gold (logic-d151 P3-1)'))
    if OUT.exists():
        OUT.rename(OUT.with_name(f'data.superseded-{int(os.path.getmtime(OUT))}'))
    temporary.rename(OUT)
    print(json.dumps(dict(train=len(train), val=len(v2_val), mix=mix, comparison=comparison)))


if __name__ == '__main__':
    main()
