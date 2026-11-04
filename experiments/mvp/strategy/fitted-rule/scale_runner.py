"""D217: frozen fitted-rule greedy decode and judge600 on the 18 TRAIN K2 maps.

One sequential map worker; native library threads are one. Run from repository root:
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/strategy/fitted-rule/scale_runner.py
Historical K2 comparison is read-only and had a longer 1200s finisher / sampled pool.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE / 'scale'
FILL = ROOT / 'experiments/mvp/strategy/fillsweep'
K2 = HERE.parent / 'k1b/k2'
FROZEN_HASH = 'da15edecf1061c2e2014317ccaf53190f6c2997b5f225f8ca39bd5174a6559d6'
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['UV_CACHE_DIR'] = str(ROOT / '.uv-cache')
os.environ['COMPACT_FINISHER_S'] = '600'
os.environ.pop('STOWAGE_BALLAST_V4', None)
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(HERE))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def cohort():
    maps = json.loads((FILL / 'cohort.json').read_text())['maps']
    chosen = [m for m in maps if m['split'] == 'train' and m['fill_target'] in (40, 60) and m['mass_level'] == 'low']
    assert len(chosen) == 18
    assert min(m['n_loads'] for m in chosen) == 58 and max(m['n_loads'] for m in chosen) == 306
    for m in chosen:
        assert '/maps/train/medium/' in m['input'] and '/test' not in m['input']
    return sorted(chosen, key=lambda m: (m['n_loads'], m['map_id']))


def comparators(job):
    """Do not imply that absent sweep rows failed or budgets were matched."""
    prior = K2 / 'judged' / f"D192.{job['map_id']}.json"
    k2 = json.loads(prior.read_text())
    ret = dict(model82=dict(source=str(prior.relative_to(ROOT)), source_sha256=sha(prior),
                           complete=k2['n_complete'], samples=k2['samples_reached'],
                           passed=k2['moves'] is not None, moves=k2['moves'], judge_seconds=k2['judge_s'],
                           finisher_seconds=k2['binding']['finisher_s']))
    sweep = FILL / 'runs/main/maps' / job['map_id'] / 'summary.json'
    ret['sweep'] = None
    if sweep.exists():
        record = json.loads(sweep.read_text())
        if record.get('complete') is True:
            ret['sweep'] = dict(source=str(sweep.relative_to(ROOT)), source_sha256=sha(sweep), arms={})
            for name in ('default', 'rule'):
                arm = record['arms'][name]
                ret['sweep']['arms'][name] = dict(
                    passed=bool(arm['raw'].get('passed') or arm['compact'].get('passed')),
                    raw_passed=bool(arm['raw'].get('passed')),
                    compact_passed=bool(arm['compact'].get('passed')))
    return ret


def manifest(jobs):
    assert sha(HERE / 'frozen-source/fitted.json') == FROZEN_HASH
    value = dict(decision='D217', arm='fitted-rule-greedy', frozen_weights_sha256=FROZEN_HASH,
                 seed=0, greedy=True, workers=1, finisher_seconds=600,
                 historical_comparator='K2: 16 target samples T=.7 including greedy, 3600s decode, <=4 distinct candidates, 1200s finisher',
                 inputs=[dict(map_id=j['map_id'], input=j['input'], input_sha256=sha(ROOT / j['input']),
                              n_loads=j['n_loads'], ship=j['ship'], fill=j['fill_target']) for j in jobs])
    target = OUT / 'manifest.json'
    if target.exists():
        assert json.loads(target.read_text()) == value, 'scale manifest differs; refusing resume'
    else:
        atomic_json(target, value)
    return value


def write_report(jobs):
    rows = []
    for job in jobs:
        path = OUT / 'maps' / job['map_id'] / 'row.json'
        if path.exists():
            rows.append(json.loads(path.read_text()))
    done = [r for r in rows if r.get('evaluated') is True]
    complete = sum(r['complete'] for r in rows)
    clear = sum(r.get('gate_clear') is True for r in rows)
    passed = sum(r.get('passed') is True for r in rows)
    heading = 'FINAL' if len(done) == 18 else 'IN PROGRESS'
    lines = ['# Frozen fitted rule on the K2 ladder (D217)', '',
             f'**{heading}: {len(done)}/18 maps resolved; {complete} complete, {clear} gate clear, {passed} full-checker passes.**', '',
             'One greedy plan per map, frozen first-fit weights, same compaction, judge.py finisher at 600 seconds, full checker and mask replay. '
             'The cohort is the exact 18 K2 train maps (low mass, 40%/60% requested fill); there is no fitting or tuning here. '
             'Decode seconds include mask setup. Plan seconds are decode plus evaluation and exclude admission waits.', '',
             f'Weights SHA-256: `{FROZEN_HASH}`.', '',
             'Historical 8.2 passed 0/18 using up to 16 samples, a 3600-second decode allowance, and up to four distinct plans with a 1200-second finisher each. '
             'Two-step and block-rule results are the existing fill-sweep raw-or-compacted passes; pending rows stay pending. '
             'These are historical comparisons with different budgets and contention, not a matched speed benchmark.', '',
             '| Map | Ship | Fill | Boxes | Complete | Gate clear | Pass | Moves | Decode s | Evaluate s | Plan s | 8.2 complete / reached | 8.2 pass | Two-step | Block rule |',
             '|---|---|---:|---:|---|---|---|---:|---:|---:|---:|---:|---|---|---|']
    byid = {r['map_id']: r for r in rows}
    fmt = lambda x: '—' if x is None else f'{x:.2f}'
    yn = lambda x: '—' if x is None else ('yes' if x else 'no')
    for job in jobs:
        r = byid.get(job['map_id'], {})
        c = r.get('comparators') or comparators(job)
        m = c['model82']
        arms = c['sweep']['arms'] if c['sweep'] else {}
        lines.append(f"| {job['map_id']} | {job['ship']} | {job['fill_target']} | {job['n_loads']} | {yn(r.get('complete'))} | {yn(r.get('gate_clear'))} | {yn(r.get('passed'))} | {fmt(r.get('moves'))} | {fmt(r.get('decode_seconds'))} | {fmt(r.get('evaluation_seconds'))} | {fmt(r.get('plan_seconds'))} | {m['complete']} / {m['samples']} | {yn(m['passed'])} | {yn(arms.get('default', {}).get('passed'))} | {yn(arms.get('rule', {}).get('passed'))} |")
    lines.extend(['', 'An incomplete plan is recorded as no gate clear and no pass; no finisher is invoked for it. '
                  'Gate failures still receive the finisher, because ballast can change lashing forces. '
                  'Per-map raw plans, metadata, evaluations and checker evidence are in `scale/maps/`.', ''])
    (HERE / 'SCALE.md').write_text('\n'.join(lines))
    atomic_json(OUT / 'summary.json', dict(status=heading, evaluated=len(done), total=18, complete=complete,
                                          gate_clear=clear, passed=passed, rows=rows))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = cohort()
    manifest(jobs)
    if args.report_only:
        write_report(jobs)
        return
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from rule import decode_instance
    from evaluate import evaluate
    atomic_json(OUT / 'process.json', dict(pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat(), workers=1))
    code = {name: sha(HERE / name) for name in ('rule.py', 'evaluate.py', 'scale_runner.py')}
    for job in jobs:
        folder = OUT / 'maps' / job['map_id']
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / 'row.json'
        binding = dict(input_sha256=sha(ROOT / job['input']), code_sha256=code, weights_sha256=FROZEN_HASH)
        if target.exists():
            prev = json.loads(target.read_text())
            assert prev['binding'] == binding, f"changed binding: {job['map_id']}"
            if prev.get('evaluated') is True:
                continue
        inst = load_instance(ROOT / job['input'])
        decode_path = folder / 'decode.json'
        if decode_path.exists():
            saved = json.loads(decode_path.read_text())
            assert saved['binding'] == binding
            plan = MovePlan.model_validate(saved['plan']) if saved['plan'] is not None else None
            metadata = saved['metadata']
        else:
            plan, metadata = decode_instance(inst, seed=0, greedy=True)
            atomic_json(decode_path, dict(binding=binding, metadata=metadata,
                                         plan=plan.model_dump(mode='json') if plan is not None else None))
        row = dict(binding=binding, map_id=job['map_id'], instance_id=inst.id, input=job['input'],
                   n_loads=len(inst.load_list), ship=job['ship'], fill=job['fill_target'],
                   complete=plan is not None, decode=metadata, decode_seconds=metadata['decode_seconds'],
                   gate_clear=False, passed=False, moves=None, comparators=comparators(job), evaluated=False)
        atomic_json(target, row)
        write_report(jobs)
        if plan is not None:
            start = time.perf_counter()
            result = evaluate(ROOT / job['input'], plan, folder / 'evaluation')
            row.update(evaluation=result, evaluation_elapsed_seconds=time.perf_counter() - start,
                       evaluation_seconds=result['compute_seconds'],
                       gate_clear=result.get('gate_clear'), passed=result.get('passed') is True,
                       moves=result.get('moves'))
        else:
            row['evaluation_seconds'] = 0.
        row.update(evaluated=True, plan_seconds=row['decode_seconds'] + row['evaluation_seconds'])
        atomic_json(target, row)
        write_report(jobs)
        print(json.dumps({k: row[k] for k in ('map_id', 'n_loads', 'complete', 'gate_clear', 'passed', 'decode_seconds', 'evaluation_seconds')}), flush=True)
    write_report(jobs)
    atomic_json(OUT / 'DONE.json', dict(completed_utc=datetime.now(timezone.utc).isoformat(), maps=18))


if __name__ == '__main__':
    main()
