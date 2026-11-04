"""D217: serial frozen greedy + 16 independent T=1 small-validation trials.

The greedy run is separate from the 16-sample stochastic pool. No fit is called.
Curves use K1b's compacted-cost ranking, signature deduplication, first passing
candidate selection and cached per-signature 600-second full-checker verdicts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE/'small'
KS = (1, 2, 4, 8, 16)
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
             'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS',
             'RAYON_NUM_THREADS', 'POLARS_MAX_THREADS'):
    os.environ[name] = '1'
os.environ.update(CUDA_VISIBLE_DEVICES='', TOKENIZERS_PARALLELISM='false',
                  UV_CACHE_DIR=str(ROOT/'.uv-cache'))
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(row, indent=2, sort_keys=True)+'\n')
    tmp.replace(path)


def status(stage, **details):
    write(OUT/'status.json', dict(stage=stage, pid=os.getpid(), utc=datetime.now(timezone.utc).isoformat(), **details))


def read_rows(folder):
    return [json.loads(p.read_text()) for p in sorted(Path(folder).glob('*.json'))]


def mean(values):
    return sum(values)/len(values) if values else None


def pair(mine, rival):
    shared = sorted(set(mine) & set(rival))
    mutual = [i for i in shared if mine[i] is not None and rival[i] is not None]
    a, b = [mine[i] for i in mutual], [rival[i] for i in mutual]
    ma, mb = mean(a), mean(b)
    return dict(n_compared=len(shared), mine_pass=sum(mine[i] is not None for i in shared),
                rival_pass=sum(rival[i] is not None for i in shared), mutual=len(mutual),
                mine_mean=ma, rival_mean=mb, delta=ma/mb-1 if mb else None,
                fewer=sum(x<y for x,y in zip(a,b)), equal=sum(x==y for x,y in zip(a,b)),
                more=sum(x>y for x,y in zip(a,b)))


def publish_report():
    greedy, curves = read_rows(OUT/'greedy'), read_rows(OUT/'curves')
    samples = [json.loads(p.read_text()) for p in sorted((OUT/'samples').glob('*/*.json'))]
    k1b = HERE.parent/'k1b'
    historical = {}
    for arm, root in [('K1b', k1b), ('K1r', k1b/'k1r'), ('K1c masked alone', k1b/'k1c')]:
        historical[arm] = {r['instance_id']: r['moves'] for r in read_rows(root/'judged/model')}
    if historical['K1c masked alone']:
        historical['K1c with K1b fallback'] = {
            i: min([x for x in (historical['K1b'][i], v) if x is not None], default=None)
            for i,v in historical['K1c masked alone'].items()}
    points = {str(k): dict(maps=len(curves), eligible=sum(r['points'][str(k)]['eligible'] for r in curves),
                          passed=sum(r['points'][str(k)]['moves'] is not None for r in curves),
                          mean_moves=mean([r['points'][str(k)]['moves'] for r in curves if r['points'][str(k)]['moves'] is not None]),
                          mean_raw_best=mean([r['points'][str(k)]['raw_best'] for r in curves if r['points'][str(k)]['raw_best'] is not None])) for k in KS}
    completed = [r for r in samples if r.get('plan') is not None]
    full_pools = {}
    for s in samples:
        full_pools.setdefault(s['instance_id'], []).append(s)
    full_pools = {i:ss for i,ss in full_pools.items() if len(ss)==16 and all(s['plan'] is None or 'prepared' in s for s in ss)}
    raw_pool_curve = {}
    for k in KS:
        costs = [min((s['prepared']['cost'] for s in ss if s['sample']<k and s['plan'] is not None), default=None) for ss in full_pools.values()]
        raw_pool_curve[str(k)] = dict(maps=len(full_pools), eligible=sum(c is not None for c in costs), mean_raw_best=mean([c for c in costs if c is not None]))
    gpass = [r['evaluation']['moves'] for r in greedy if r['evaluation'].get('passed')]
    results = dict(expected_maps=55, expected_stochastic_samples=880,
                   greedy=dict(maps=len(greedy), complete=sum(r['complete'] for r in greedy),
                               passed=len(gpass), mean_moves=mean(gpass),
                               gate_clear=sum(r['evaluation'].get('gate_clear') is True for r in greedy)),
                   stochastic=dict(samples=len(samples), complete=len(completed),
                                   gate_audited=sum('prepared' in r for r in completed),
                                   gate_clear=sum(r.get('prepared', {}).get('gate_clear') is True for r in completed),
                                   mean_rewinds=mean([r['metadata']['rewinds'] for r in samples]),
                                   completion_rate=len(completed)/len(samples) if samples else None),
                   curves=points, raw_pool_curve=raw_pool_curve, comparisons={}, pending=len(greedy)!=55 or len(samples)!=880 or len(curves)!=55)
    for k in KS:
        mine = {r['instance_id']: r['points'][str(k)]['moves'] for r in curves}
        results['comparisons'][str(k)] = {}
        for arm, rival in historical.items():
            # Only k=16 baselines are final judged pools. Other k use stored curves when available.
            source = k1b/'kcurve'/('8.2' if arm=='K1b' else arm)
            rival_curve = read_rows(source) if arm in ('K1b','K1r') else []
            if k != 16 and not rival_curve:
                continue
            if rival_curve:
                rival = {r['instance_id']: r['points'][str(k)]['moves'] for r in rival_curve}
            results['comparisons'][str(k)][arm] = pair(mine, rival)
    write(OUT/'summary.json', results)
    fmt = lambda x: '—' if x is None else f'{x:.2f}'
    lines = ['# D217 small validation: frozen fitted rule', '',
             'Frozen first-fit weights SHA-256 `da15edecf1061c2e2014317ccaf53190f6c2997b5f225f8ca39bd5174a6559d6`. '
             '55 K1b validation inputs only; no test input, fitting, tuning, GPU, or source edits. One serial worker, native threads one. '
             'Each finished trial uses unchanged K1b compaction, V4 600 s, full checker and production mask replay, with fresh D206 admission.', '',
             '**'+('PENDING: observed rows only; missing work is not counted as failure.' if results['pending'] else 'COMPLETE.')+'**', '',
             '| Greedy arm | Maps observed / 55 | Complete | Passed | Mean moves on own passes |',
             '|---|---:|---:|---:|---:|',
             '| Historical 8.2 k=1 | 55/55 | 49 | 46 | 24.20 |',
             f'| Frozen fitted rule | {len(greedy)}/55 | {results["greedy"]["complete"]} | {len(gpass)} | {fmt(mean(gpass))} |', '',
             'Historical 8.2 is the stored KCURVE.md row; it was judged in its original execution window. '
             'This is not a fresh matched timing experiment.', '',
             f'Stochastic T=1 pool: {len(samples)}/880 stored samples, {len(completed)} complete '
             f'({fmt(100*len(completed)/len(samples)) if samples else "—"}%). '
             f'{results["stochastic"]["gate_clear"]}/{results["stochastic"]["gate_audited"]} gate-audited complete samples clear. '
             f'Mean rewinds per stored sample: {fmt(results["stochastic"]["mean_rewinds"])}. '
             'All 16 are stochastic, seeds 0–15; the greedy trial is separate. K1b/K1c use greedy + 15 at T=.7; K1r is all uniform random. '
             'Pooled candidate rates are descriptive because candidates repeat maps.', '',
             '| Arm / regime | Stored samples | Complete | Mean rewinds |',
             '|---|---:|---:|---:|',
             '| Historical K1b: greedy + 15 T=.7 | 880 | 792 (90.0%) | — |',
             '| Historical K1r: uniform random | 880 | 729 (82.8%) | — |',
             '| Historical null: greedy + 15 T=.7 | 880 | 668 (75.9%) | — |',
             '| Historical K1c masked alone: greedy + 15 T=.7 | 880 | 560 (63.6%) | — |',
             f'| Fitted rule: 16 T=1, observed | {len(samples)}/880 | {len(completed)} ({100*len(completed)/len(samples):.1f}%) | {fmt(results["stochastic"]["mean_rewinds"])} |' if samples else '| Fitted rule: 16 T=1 | 0/880 | pending | — |', '',
             '| k | Maps observed / 55 | Eligible | Passed | Mean final moves | Mean cheapest compacted raw moves |',
             '|---:|---:|---:|---:|---:|---:|']
    for k,p in points.items():
        lines.append(f'| {k} | {p["maps"]}/55 | {p["eligible"]} | {p["passed"]} | {fmt(p["mean_moves"])} | {fmt(p["mean_raw_best"])} |')
    lines += ['', 'Raw pool readout before finishing (complete 16-sample pools only):', '',
              '| k | Pools observed / 55 | Eligible | Mean cheapest compacted raw moves |',
              '|---:|---:|---:|---:|']
    for k,p in raw_pool_curve.items():
        lines.append(f'| {k} | {p["maps"]}/55 | {p["eligible"]} | {fmt(p["mean_raw_best"])} |')
    lines += ['', 'D207 companion rates for the frozen rule (default tanks, compacted chosen plan):', '',
              '| Regime / index | Stored / expected | Complete | Gate audited | Sight/lashing clear | Clear / complete |',
              '|---|---:|---:|---:|---:|---:|',
              f'| Separate greedy | {len(greedy)}/55 | {results["greedy"]["complete"]} | {sum(r["complete"] for r in greedy)} | {results["greedy"]["gate_clear"]} | {fmt(100*results["greedy"]["gate_clear"]/results["greedy"]["complete"]) if results["greedy"]["complete"] else "—"}% |']
    for i in range(16):
        group = [s for s in samples if s['sample']==i]
        done = [s for s in group if s['plan'] is not None]
        clear = sum(s.get('prepared',{}).get('gate_clear') is True for s in done)
        audited = sum('prepared' in s for s in done)
        rate = f'{100*clear/len(done):.1f}%' if done and audited==len(done) else 'pending' if done else '—'
        lines.append(f'| T=1 index {i} | {len(group)}/55 | {len(done)} | {audited} | {clear} | {rate} |')
    lines += ['', 'The companion historical rates are in `../k1b/skill-check/extensions/sample-curves.md` and '
              '`../k1b/skill-check/part1-sample0.md`; the T=1 regime differs from the historical T=.7 regime.']
    lines += ['', 'Best-of-k uses the first k samples, compacted-cost ranking, deduplicated move signatures, and the first passing trial, exactly as K1b. '
              'Raw costs are pre-finisher and do not establish full-checker success.', '',
              '| k | Rule vs historical arm | Maps observed | Rule / rival passes | Mutual | Mean final moves: rule / rival | Δ | Fewer / equal / more |',
              '|---:|---|---:|---:|---:|---:|---:|---:|']
    for k, comparators in results['comparisons'].items():
        for arm,p in comparators.items():
            delta = '—' if p['delta'] is None else f'{p["delta"]:+.1%}'
            lines.append(f'| {k} | {arm} | {p["n_compared"]} | {p["mine_pass"]} / {p["rival_pass"]} | {p["mutual"]} | {fmt(p["mine_mean"])} / {fmt(p["rival_mean"])} | {delta} | {p["fewer"]} / {p["equal"]} / {p["more"]} |')
    lines += ['', 'K1c with fallback uses its own checked masked pool and K1b; masked-alone is shown separately. '
              'K1c has no stored full k-curve here, so only k=16 is compared. '
              'Incremental results, provenance and runtime/admission evidence are under `small/`; an incomplete queue is not a negative result.', '']
    (HERE/'SMALL.md').write_text('\n'.join(lines))


def seal():
    from rule import WEIGHTS_SHA256
    import compact
    import judge
    baseline = json.loads((HERE.parent/'k1b/manifest.json').read_text())
    files = baseline['inputs']
    assert len(files) == 55
    for rel, digest in files.items():
        assert rel.startswith('experiments/mvp/corpus/val/') and 'test' not in rel
        assert sha(ROOT/rel) == digest, f'input changed: {rel}'
    sources = [Path(__file__), HERE/'rule.py', HERE/'evaluate.py', HERE/'frozen-source/rf.py',
               Path(compact.__file__), Path(judge.__file__)]
    manifest = dict(decision='D217', weights_sha256=WEIGHTS_SHA256, inputs=files,
                    code={str(p.relative_to(ROOT)):sha(p) for p in sources},
                    greedy_separate=True, stochastic_seeds=list(range(16)), stochastic_temperature=1,
                    finisher_s=600, workers=1, native_threads=1, **compact.run_identity())
    path = OUT/'manifest.json'
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise RuntimeError('small manifest differs; refusing mixed-epoch resume')
    write(path, manifest)
    return files


def greedy(files):
    from rule import decode_instance
    from evaluate import evaluate
    from stowage.io import load_instance
    from stowage.model import MovePlan
    for rel in sorted(files):
        inst = load_instance(ROOT/rel)
        target = OUT/'greedy'/f'{inst.id}.json'
        if target.exists():
            continue
        status('greedy', instance_id=inst.id)
        candidate = OUT/'greedy-candidates'/f'{inst.id}.json'
        if candidate.exists():
            raw = json.loads(candidate.read_text())
            plan = MovePlan.model_validate(raw['plan']) if raw['plan'] is not None else None
            meta = raw['metadata']
        else:
            plan, meta = decode_instance(inst, seed=0, greedy=True)
            write(candidate, dict(instance_id=inst.id, metadata=meta, plan=plan.model_dump(mode='json') if plan else None))
        result = evaluate(ROOT/rel, plan, OUT/'greedy-evaluations'/inst.id)
        write(target, dict(instance_id=inst.id, input=rel, metadata=meta, complete=plan is not None, evaluation=result))
        publish_report()


def stochastic(files):
    from rule import decode_instance
    from evaluate import prepare
    from stowage.io import load_instance
    from stowage.model import MovePlan
    for rel in sorted(files):
        inst = load_instance(ROOT/rel)
        for seed in range(16):
            path = OUT/'samples'/inst.id/f'sample-{seed:02d}.json'
            row = json.loads(path.read_text()) if path.exists() else None
            if row is not None and (row['plan'] is None or 'prepared' in row):
                continue
            status('stochastic', instance_id=inst.id, sample=seed)
            if row is None:
                plan, meta = decode_instance(inst, seed=seed, greedy=False)
                row = dict(instance_id=inst.id, input=rel, sample=seed, metadata=meta,
                           plan=plan.model_dump(mode='json') if plan else None)
                write(path, row)
            else:
                plan = MovePlan.model_validate(row['plan'])
            if plan is not None:
                chosen, info = prepare(inst, plan)
                row['prepared'] = info
                row['chosen_plan'] = chosen.model_dump(mode='json')
                row['signature'] = hashlib.sha256(json.dumps([m.model_dump(mode='json') for m in chosen.moves], sort_keys=True).encode()).hexdigest()
                write(path, row)
            publish_report()


def curves(files):
    from evaluate import evaluate
    import judge
    from stowage.io import load_instance
    from stowage.model import MovePlan
    for rel in sorted(files):
        inst = load_instance(ROOT/rel)
        target = OUT/'curves'/f'{inst.id}.json'
        if target.exists():
            continue
        samples = read_rows(OUT/'samples'/inst.id)
        if len(samples) != 16 or any(s['plan'] is not None and 'prepared' not in s for s in samples):
            raise RuntimeError(f'{inst.id}: incomplete stochastic pool')
        # Ranking refers to the once-compacted plan; evaluate receives the raw
        # original and applies that same single compaction before finishing.
        entries = [dict(sample=s['sample'], cost=s['prepared']['cost'], signature=s['signature'], plan=s['plan']) for s in samples if s['plan'] is not None]
        points = {}
        for k in KS:
            status('curve', instance_id=inst.id, k=k)
            ranked = judge.rank([e for e in entries if e['sample']<k])
            moves, tried, winner = None, 0, None
            for entry in ranked:
                result = evaluate(ROOT/rel, MovePlan.model_validate(entry['plan']), OUT/'sample-evaluations'/inst.id/entry['signature'])
                tried += 1
                if result.get('passed'):
                    moves, winner = result['moves'], entry['sample']
                    break
            points[str(k)] = dict(moves=moves, winner_sample=winner, raw_best=ranked[0]['cost'] if ranked else None,
                                  judged=tried, eligible=bool(ranked))
        write(target, dict(instance_id=inst.id, input=rel, points=points))
        publish_report()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('all','greedy','stochastic','curves','report'), default='all')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.phase == 'report':
        publish_report()
        return
    with (OUT/'queue.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        files = seal()
        write(OUT/'process.json', dict(pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat(),
                                      argv=sys.argv, workers=1, native_threads=1))
        for stage, fn in [('greedy',greedy), ('stochastic',stochastic), ('curves',curves)]:
            if args.phase in ('all', stage):
                fn(files)
        status('complete' if args.phase=='all' else args.phase+'_complete')
        publish_report()


if __name__ == '__main__':
    main()
