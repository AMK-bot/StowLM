"""D193 k-curve: for each arm and map, take the first k stored candidates in sample order, rank them as the judge does, and walk the ranking
to the first plan that passes finisher, checker and mask replay, for k = 1, 2, 4, 8, 16. A verdict the judge already reached is reused."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import compact
import judge

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
KS = (1, 2, 4, 8, 16)
ARMS = {'8.2': HERE, 'null': HERE/'null', 'K1r': HERE/'k1r'}


def jobs(maps):
    return [dict(job, arm=arm) for arm in ARMS for job in maps]


def walk(entries, k, verdict):
    """(best passing cost or None, cheapest raw cost or None, candidates judged) over samples below k. verdict(entry) gives passed, moves."""
    subset = [e for e in entries if e['sample'] < k]
    order = judge.rank(subset)
    for tried, entry in enumerate(order, 1):
        result = verdict(entry)
        if result.get('passed'):
            return result['moves'], order[0]['cost'], tried
    return None, order[0]['cost'] if order else None, len(order)


def curve(job):
    from stowage.io import load_instance
    from stowage.model import MovePlan
    arm, iid, folder = job['arm'], job['instance_id'], ARMS[job['arm']]
    raw = {p.name: p.read_text() for p in sorted((folder/'candidates'/iid).glob('sample-*.json'))}
    if len(raw) != 16:
        raise SystemExit(f'{arm} {iid}: {len(raw)} samples')
    binding = dict(samples_sha256=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(), finisher_s=compact.BUDGET_S, **compact.run_identity())
    target = HERE/'kcurve'/arm/f'{iid}.json'
    if target.exists():
        row = json.loads(target.read_text())
        if row.get('binding') == binding:
            return row
    instance = load_instance(ROOT/job['input'])
    entries = []
    for name in sorted(raw):
        sample = json.loads(raw[name])
        if sample['plan'] is None:
            continue
        plan = MovePlan.model_validate(sample['plan'])
        compacted, info = compact.compact_plan(instance, plan)
        better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
        chosen = compacted if better else plan
        entries.append(dict(sample=sample['sample'], cost=info['compact_raw']['total'] if better else info['before']['total'], plan=chosen,
                            signature=judge.sha_text(json.dumps([m.model_dump(mode='json') for m in chosen.moves], sort_keys=True))))
    judged = json.loads((folder/'judged/model'/f'{iid}.json').read_text())
    known = {t['sample']: dict(passed=t.get('passed') is True, moves=t.get('moves')) for t in judged['trials']}
    cache, fresh = {e['signature']: known[e['sample']] for e in entries if e['sample'] in known}, [0]

    def verdict(entry):
        if entry['signature'] not in cache:
            check, finished, _ = compact.finish_and_check(instance, entry['plan'])
            if check.get('passed'):
                compact.admit_mask(check, compact.mask_ok(instance, finished.plan))
            cache[entry['signature']] = dict(passed=check.get('passed') is True, moves=check.get('moves'), timed_out=check.get('timed_out'))
            fresh[0] += 1
        return cache[entry['signature']]
    points = {}
    for k in KS:
        moves, raw_best, tried = walk(entries, k, verdict)
        points[str(k)] = dict(moves=moves, raw_best=raw_best, judged=tried, eligible=any(e['sample'] < k for e in entries))
    row = dict(binding=binding, arm=arm, instance_id=iid, points=points, fresh_verdicts=fresh[0])
    judge.publish(target, json.dumps(row, indent=1, sort_keys=True))
    print(json.dumps(dict(arm=arm, instance_id=iid, fresh=fresh[0])), flush=True)
    return row


def first_sample(arm, folder, job, out):
    """One map's first sample alone, compacted and judged; a verdict from that arm's own judged trials is reused."""
    from stowage.io import load_instance
    from stowage.model import MovePlan
    iid = job['instance_id']
    target = Path(out)/arm/f'{iid}.json'
    raw = (Path(folder)/'candidates'/iid/'sample-00.json').read_text()
    binding = dict(sample_sha256=judge.sha_text(raw), finisher_s=compact.BUDGET_S, **compact.run_identity())
    if target.exists():
        row = json.loads(target.read_text())
        if row.get('binding') == binding:
            return row
    sample, row = json.loads(raw), dict(binding=binding, instance_id=iid, complete=False, moves=None, reused=False)
    if sample['plan'] is not None:
        instance = load_instance(ROOT/job['input'])
        plan = MovePlan.model_validate(sample['plan'])
        compacted, info = compact.compact_plan(instance, plan)
        better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
        trials = json.loads((Path(folder)/'judged/model'/f'{iid}.json').read_text())['trials']
        known = next((t for t in trials if t['sample'] == 0), None)
        if known is None:
            check, finished, _ = compact.finish_and_check(instance, compacted if better else plan)
            if check.get('passed'):
                compact.admit_mask(check, compact.mask_ok(instance, finished.plan))
        else:
            check = known
        row.update(complete=True, reused=known is not None, moves=check.get('moves') if check.get('passed') else None, timed_out=check.get('timed_out'))
    judge.publish(target, json.dumps(row, indent=1, sort_keys=True))
    return row
