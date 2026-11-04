"""D194 K2 on the fill ladder: 8.2 with K1b's recipe on the fill sweep's 40 % and 60 % low-mass train maps, all ships, read-only.

Decode: greedy + 15 samples, K1b's flags, 3,600 s per map, largest map last, candidates stored as they finish, so a map cut by the limit keeps
the ones it reached. Judge: K1b's ranking and gate, cheapest compacted first, stopping at the first pass, with two choices made for this
scale: the finisher budget is 1,200 s, the sweep's medium budget for default and rule, and at most MAX_TRIALS candidates are tried per map, to
keep CPU modest (a medium map could otherwise spend 5 h). Detached and resumable. Usage: k2ladder.py [workers=2] [shards=3]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OUT = HERE/'k2'
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
os.environ.update(COMPACT_FINISHER_S='1200', **{k: '1' for k in THREADS})  # before compact is imported; spawned workers inherit it
os.environ.pop('STOWAGE_BALLAST_V4', None)
sys.path.insert(0, str(HERE.parent))
import compact
import judge

ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
FILL = ROOT/'experiments/mvp/strategy/fillsweep'
ADAPTER = 'experiments/mvp/sft/stowlm-v2/adapter'
K1B = json.loads((HERE/'manifest.json').read_text())
MAX_TRIALS, TIME_LIMIT = 4, 3600
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '2', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'PYTHONPATH': f'{HERE}:{HERE.parent}'}
SCRIPTS = (HERE/'k2ladder.py', HERE/'k2_decode.py', ROOT/'experiments/eval-c1/run_c1.py', HERE/'judge.py', HERE.parent/'compact.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def maps():
    rows = json.loads((FILL/'cohort.json').read_text())['maps']
    chosen = [m for m in rows if m['fill_target'] in (40, 60) and m['mass_level'] == 'low' and m['split'] == 'train']
    if len(chosen) != 18:
        raise SystemExit(f'{len(chosen)} maps, 18 expected')
    return sorted(chosen, key=lambda m: (m['n_loads'], m['map_id']))


def flags():
    values = list(K1B['flags'])
    values[values.index('--time-limit')+1] = str(TIME_LIMIT)
    return values


def seal(chosen):
    manifest = dict(decision='D194', flags=flags(), finisher_s=compact.BUDGET_S, max_trials=MAX_TRIALS, adapter=K1B['adapter'],
                    inputs={m['input']: sha(ROOT/m['input']) for m in chosen}, scripts={str(p.relative_to(ROOT)): sha(p) for p in SCRIPTS})
    path = OUT/'manifest.json'
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise SystemExit('manifest differs; refusing to resume')
    else:
        path.write_text(json.dumps(manifest, indent=1, sort_keys=True)+'\n')


def decode(chosen, shards):
    groups = [[m['input'] for m in chosen[i::shards]] for i in range(shards)]

    def one(i):
        with open(OUT/f'decode/s{i}.log', 'a') as stream:
            return subprocess.run([PYTHON, str(HERE/'k2_decode.py'), '--run-dir', str(OUT/f'decode/s{i}'), '--adapter', ADAPTER, '--loose', *groups[i], *flags(),
                                   '--store-candidates', str(OUT/'candidates'), '--decode-only'], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT).returncode
    (OUT/'decode').mkdir(exist_ok=True)
    with ThreadPoolExecutor(shards) as pool:
        codes = list(pool.map(one, range(shards)))
    if any(codes):
        raise SystemExit(f'decode failed: {codes}')


def judge_map(job):
    """K1b's ranking and gate over the candidates this map reached; returns its row."""
    from stowage.io import load_instance
    from stowage.model import MovePlan
    iid = job['instance_id']
    samples = sorted((OUT/'candidates'/iid).glob('sample-*.json'))
    raw = {p.name: p.read_text() for p in samples}
    binding = dict(input=job['input'], input_sha256=sha(ROOT/job['input']), samples_sha256=judge.sha_text(json.dumps(raw, sort_keys=True)),
                   finisher_s=compact.BUDGET_S, max_trials=MAX_TRIALS, code=judge.code_binding(), **compact.run_identity())
    target = OUT/'judged'/f'{iid}.json'
    if target.exists():
        row = json.loads(target.read_text())
        if row.get('binding') == binding:
            return row
    instance = load_instance(ROOT/job['input'])
    started = time.perf_counter()
    entries, actions = [], []
    for name in sorted(raw):
        sample = json.loads(raw[name])
        if sample['plan'] is None:
            continue
        plan = MovePlan.model_validate(sample['plan'])
        actions.append(len(plan.actions or []))
        compacted, info = compact.compact_plan(instance, plan)
        better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
        chosen = compacted if better else plan
        entries.append(dict(sample=sample['sample'], cost=info['compact_raw']['total'] if better else info['before']['total'], plan=chosen, compacted=better,
                            signature=judge.sha_text(json.dumps([m.model_dump(mode='json') for m in chosen.moves], sort_keys=True))))
    order, trials, winner = judge.rank(entries), [], None
    for entry in order[:MAX_TRIALS]:
        check, finished, report = compact.finish_and_check(instance, entry['plan'])
        if check.get('passed'):
            compact.admit_mask(check, compact.mask_ok(instance, finished.plan))
        trials.append(dict({k: entry[k] for k in ('sample', 'cost', 'compacted')}, **check))
        if check.get('passed'):
            winner = dict(trials[-1], plan_sha256=judge.publish(OUT/'plans'/f'{iid}.json', finished.model_dump_json()),
                          report_sha256=judge.publish(OUT/'plans'/f'{iid}.report.json', report.model_dump_json()))
            break
    n_loads = len(instance.load_list)
    row = dict(binding=binding, instance_id=iid, map_id=job['map_id'], ship=job['ship'], fill=job['fill_target'], n_loads=n_loads, samples_reached=len(raw), n_complete=len(entries),
               n_distinct=len(order), untried=max(0, len(order)-MAX_TRIALS), trials=trials, moves=winner['moves'] if winner else None, winner=winner,
               actions_per_box=sum(actions)/len(actions)/n_loads if actions else None, judge_s=round(time.perf_counter()-started, 1))
    judge.publish(target, json.dumps(row, indent=1, sort_keys=True))
    print(json.dumps({k: row[k] for k in ('map_id', 'n_complete', 'moves', 'judge_s')}), flush=True)
    return row


def sweep(map_id):
    """The sweep's arms for this map, passing on either the raw or compacted plan; None while the map is pending."""
    path = FILL/'runs/main/maps'/map_id/'summary.json'
    if not path.exists() or json.loads(path.read_text()).get('complete') is not True:
        return None
    arms = json.loads(path.read_text())['arms']
    return {arm: bool(arms[arm]['raw'].get('passed') or arms[arm]['compact'].get('passed')) for arm in ('default', 'rule')}


def verdict(rows):
    """D194: met if 8.2 passes at least one map that both default and rule fail in the sweep."""
    decided = [r for r in rows if r['sweep'] is not None]
    wins = [r['map_id'] for r in decided if r['moves'] is not None and not r['sweep']['default'] and not r['sweep']['rule']]
    pending = [r['map_id'] for r in rows if r['sweep'] is None and r['moves'] is not None]
    return dict(met=bool(wins), wins=wins, pending=pending)


def decode_seconds(map_input):
    for path in OUT.glob('decode/s*/instances/*.json'):
        rec = json.loads(path.read_text())
        if rec.get('input') == map_input:
            return rec.get('pipeline_s')
    return None


def write_report(rows, jobs):
    for row in rows:
        row['sweep'] = sweep(row['map_id'])
    v = verdict(rows)
    mark = lambda s, arm: 'pending' if s is None else ('pass' if s[arm] else 'fail')
    lines = ['# K2 on the fill ladder (D194)', '',
             "18 train maps of the fill sweep: 40 % and 60 % requested fill, low mass, all three ships; read-only. 8.2 with K1b's recipe: greedy + 15 samples at T = 0.7, 32 rewinds, D167 mask stack, 3,600 s per map, candidates stored and compacted, cheapest first through the V4 finisher, full checker and mask replay, stopping at the first pass. "
             "Two changes for this scale, set before any result: the finisher has 1,200 s, the sweep's own medium budget for default and rule, and at most 4 candidates are tried per map. "
             'Default and rule pass here if the raw or compacted plan passed in `fillsweep/runs/main`. Train maps; nothing here touches test.', '',
             '| Map | Ship | Fill | Boxes | Samples reached | Complete | Actions per box | 8.2 | Moves | Tried / distinct | Default | Rule | Decode s | Judge s |', '|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|---|---:|---:|']
    inputs = {j['map_id']: j['input'] for j in jobs}
    for r in sorted(rows, key=lambda x: (x['fill'], x['n_loads'])):
        outcome = 'pass' if r['moves'] is not None else ('dead end' if not r['n_complete'] else 'finisher limit' if any(t.get('timed_out') for t in r['trials']) else 'fail')
        lines.append(f"| {r['map_id']} | {r['ship']} | {r['fill']} | {r['n_loads']} | {r['samples_reached']}/16 | {r['n_complete']} | {'—' if r['actions_per_box'] is None else format(r['actions_per_box'], '.2f')} | {outcome} | "
                     f"{'—' if r['moves'] is None else format(r['moves'], 'g')} | {len(r['trials'])} / {r['n_distinct']} | {mark(r['sweep'], 'default')} | {mark(r['sweep'], 'rule')} | {decode_seconds(inputs[r['map_id']])} | {r['judge_s']} |")
    lines += ['', '| Ship | Fill | Maps | 8.2 passed | Default passed | Rule passed | Sweep pending |', '|---|---:|---:|---:|---:|---:|---:|']
    for ship in ('feeder_500', 'feeder_800', 'feeder_1700'):
        for fill in (40, 60):
            sel = [r for r in rows if r['ship'] == ship and r['fill'] == fill]
            done = [r for r in sel if r['sweep']]
            lines.append(f"| {ship} | {fill} | {len(sel)} | {sum(r['moves'] is not None for r in sel)} | {sum(r['sweep']['default'] for r in done)} | {sum(r['sweep']['rule'] for r in done)} | {len(sel)-len(done)} |")
    lines += ['', '## Verdict against D194', '', f"**K2-ladder: {'MET' if v['met'] else 'not met'}.** 8.2 passes a map both default and rule fail: {', '.join(v['wins']) or 'none'}."]
    if v['pending']:
        lines.append(f"Sweep still pending on maps 8.2 passes, so this can change: {', '.join(v['pending'])}.")
    lines.append('')
    (HERE/'K2-LADDER.md').write_text('\n'.join(lines))


def main():
    workers, shards = int(sys.argv[1]) if len(sys.argv) > 1 else 2, int(sys.argv[2]) if len(sys.argv) > 2 else 3
    OUT.mkdir(exist_ok=True)
    chosen = maps()
    seal(chosen)
    if not (OUT/'decode.DONE').exists():
        decode(chosen, shards)
        (OUT/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
    jobs = []
    from stowage.io import load_instance
    for m in chosen:
        jobs.append(dict(m, instance_id=load_instance(ROOT/m['input']).id))
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(judge_map, jobs))
    write_report(rows, jobs)
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| k2-ladder/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k2-ladder/run (fix-t8-sft, 31 Oct): D194; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (OUT/'K2.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
