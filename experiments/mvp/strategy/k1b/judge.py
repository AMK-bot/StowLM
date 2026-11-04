"""D183 judging. Model: every stored candidate compacted, ranked by compacted cost (the original if compaction isn't cheaper), then
V4 finisher, full checker and mask replay, cheapest first, stopping at the first pass. Baseline: two-step at 600 s, compacted, the
better of the two checked plans. One row per arm and map; a row is reused only if its input and source hashes still match."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

import compact

HERE = Path(os.environ.get('K1B_OUT', Path(__file__).resolve().parent))  # K1B_OUT: another arm's candidates and judged rows
ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = (Path(__file__).resolve(), Path(compact.__file__).resolve())
TWOSTEP = ROOT/'experiments/mvp/strategy/twostep-600'


def sha_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_binding():
    return {path.name: sha_file(path) for path in SCRIPTS}


def publish(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(text)
    temporary.replace(path)
    return sha_text(text)


def keep_winner(name, folder, finished, report):
    """The finished plan and its report, published before the row that names their digests (rev-k1b P2)."""
    return dict(plan_sha256=publish(HERE/'judged'/folder/f'{name}.json', finished.model_dump_json()),
                report_sha256=publish(HERE/'judged'/folder/f'{name}.report.json', report.model_dump_json()))


def rank(entries):
    """Cheapest first; the sample index breaks ties, so greedy wins one. Identical move sequences are judged once."""
    seen, order = set(), []
    for entry in sorted(entries, key=lambda e: (e['cost'], e['sample'])):
        if entry['signature'] not in seen:
            seen.add(entry['signature'])
            order.append(entry)
    return order


def outcome(n_complete, trials):
    if not n_complete:
        return 'dead end'
    if any(t.get('passed') for t in trials):
        return 'pass'
    return 'finisher limit' if any(t.get('timed_out') for t in trials) else 'checker or finisher fail'


def jobs(files):
    from stowage.io import load_instance
    result = []
    for rel in files:
        if '/corpus/val/' not in rel:
            raise SystemExit(f'not a validation map: {rel}')
        result.append(dict(input=rel, instance_id=load_instance(ROOT/rel).id))
    return result


def judge(job):
    model = judge_model(job)
    baseline = judge_baseline(job)
    return dict(model=model, baseline=baseline)


def cached(path, binding, folder):
    """A stored row, only with its exact binding and a winner whose plan and report still have the digests it recorded."""
    if not path.exists():
        return None
    row = json.loads(path.read_text())
    if row.get('binding') != binding:
        return None
    winner = row.get('winner')
    if winner:
        plan, report = path.parent.parent/folder/path.name, path.parent.parent/folder/f'{path.stem}.report.json'
        if not plan.exists() or not report.exists() or sha_file(plan) != winner['plan_sha256'] or sha_file(report) != winner['report_sha256']:
            return None
    return row


def judge_model(job):
    from stowage.io import load_instance
    from stowage.model import MovePlan
    iid = job['instance_id']
    samples = sorted((HERE/'candidates'/iid).glob('sample-*.json'))
    if len(samples) != 16:
        raise SystemExit(f'{iid}: {len(samples)} stored samples, 16 expected')
    raw = {p.name: p.read_text() for p in samples}
    binding = dict(instance_id=iid, input=job['input'], input_sha256=hashlib.sha256((ROOT/job['input']).read_bytes()).hexdigest(),
                   samples_sha256=sha_text(json.dumps(raw, sort_keys=True)), finisher_s=compact.BUDGET_S, code=code_binding(), **compact.run_identity())
    target = HERE/'judged/model'/f'{iid}.json'
    row = cached(target, binding, 'plans')
    if row:
        return row
    instance = load_instance(ROOT/job['input'])
    started = time.perf_counter()
    entries = []
    for name in sorted(raw):
        sample = json.loads(raw[name])
        if sample['plan'] is None:
            continue
        plan = MovePlan.model_validate(sample['plan'])
        compacted, info = compact.compact_plan(instance, plan)
        original_cost = info['before']['total']
        use_compact = compacted is not None and info['compact_raw']['total'] < original_cost
        chosen = compacted if use_compact else plan
        entries.append(dict(sample=sample['sample'], cost=info['compact_raw']['total'] if use_compact else original_cost, original_cost=original_cost, plan=chosen,
                            compacted=use_compact, signature=sha_text(json.dumps([m.model_dump(mode='json') for m in chosen.moves], sort_keys=True)),
                            reason=info.get('reason')))
    trials, winner, artifacts = [], None, None
    for entry in rank(entries):
        check, finished, report = compact.finish_and_check(instance, entry['plan'])
        if check.get('passed'):
            compact.admit_mask(check, compact.mask_ok(instance, finished.plan))
        trials.append(dict({k: entry[k] for k in ('sample', 'cost', 'original_cost', 'compacted')}, **check))
        if check.get('passed'):
            winner = dict(trials[-1])
            artifacts = keep_winner(iid, 'plans', finished, report)
            break
    row = dict(binding=binding, instance_id=iid, input=job['input'], n_samples=16, n_complete=len(entries), n_distinct=len(rank(entries)), trials=trials,
               outcome=outcome(len(entries), trials), moves=winner.get('moves') if winner else None, winner_sample=winner['sample'] if winner else None,
               winner_compacted=winner['compacted'] if winner else None, parts=winner.get('after') if winner else None, winner=artifacts,
               wall_s=round(time.perf_counter()-started, 1))
    publish(target, json.dumps(row, indent=1, sort_keys=True))
    print(json.dumps({k: row[k] for k in ('instance_id', 'outcome', 'moves', 'wall_s')}), flush=True)
    return row


def judge_baseline(job):
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    iid = job['instance_id']
    source = TWOSTEP/f'{iid}.json'
    base = json.loads(source.read_text())
    finished_path, raw_path = TWOSTEP/'plans'/f'{iid}.json', TWOSTEP/'raw'/f'{iid}.json'
    plan_path = finished_path if finished_path.exists() else raw_path  # a failed run still has its raw draft (rev-k1b P1)
    binding = dict(instance_id=iid, input=job['input'], input_sha256=sha_file(ROOT/job['input']), row_sha256=sha_text(source.read_text()),
                   plan_sha256=sha_text(plan_path.read_text()) if plan_path.exists() else None, finisher_s=compact.BUDGET_S, code=code_binding(), **compact.run_identity())
    target = HERE/'judged/baseline'/f'{iid}.json'
    row = cached(target, binding, 'baseline-plans')
    if row:
        return row
    base_moves = base.get('weighted_nonproductive') if base.get('passed') else None
    row = dict(binding=binding, instance_id=iid, base_moves=base_moves, base_outcome=base['outcome'], moves=base_moves, compact_used=False, attempted=False, check=None)
    if plan_path.exists():
        instance = load_instance(ROOT/job['input'])
        plan = PlannerResult.model_validate(json.loads(plan_path.read_text())).plan
        compacted, info = compact.compact_plan(instance, plan)
        row.update(reason=info.get('reason'), compact_raw=info.get('compact_raw'))
        if compacted is not None and (base_moves is None or info['compact_raw']['total'] < base_moves):
            check, finished, report = compact.finish_and_check(instance, compacted)
            if check.get('passed'):
                compact.admit_mask(check, compact.mask_ok(instance, finished.plan))
            row.update(attempted=True, check=check)
            if check.get('passed') and (base_moves is None or check['moves'] < base_moves):
                row.update(moves=check['moves'], compact_used=True, winner=keep_winner(iid, 'baseline-plans', finished, report))
    row['outcome'] = 'pass' if row['moves'] is not None else base['outcome']
    publish(target, json.dumps(row, indent=1, sort_keys=True))
    return row
