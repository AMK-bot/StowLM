"""D209 8.5a, detached and resumable: run reader v2 first (D220); only if it passes all gates, build the data, train from 8.2's adapter, then decode the 55 validation maps with K1b's recipe, judge, and audit sample 0.
Prompt: everything is stowlm-prompt 2.2. 8.2 only knows 2.2, the trainer refuses mixed prompts, and the decode stack is 2.2, so the rule labels are rebuilt from their
records at 2.2; prompt 9 isn't used. 8.2's val.jsonl is copied byte for byte. 8.2's adapter is only read. Usage: k85a.py [--now] [--after-leader]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics as st
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OUT = HERE/'k85a'
os.environ.update(K1B_OUT=str(OUT), COMPACT_FINISHER_S='600')  # before compact and judge are imported; spawned workers inherit it
sys.path.insert(0, str(HERE.parent))
import judge
import k1b_report as r

ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
RUN = ROOT/'experiments/mvp/sft/stowlm-85a'
V2 = ROOT/'experiments/mvp/sft/stowlm-v2'
SNAPSHOT = ROOT/'experiments/mvp/stowlm9/data-85'
WAIT_FOR = ROOT/'experiments/mvp/reader/GPU.DONE'
K1B = json.loads((HERE/'manifest.json').read_text())
RULE_SHARE, LR = .30, 2e-5
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '8', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'PYTHONPATH': f'{HERE}:{HERE.parent}', **{k: '1' for k in THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cores():
    return subprocess.run(['sysctl', '-n', 'kern.sched_recommended_cores'], capture_output=True, text=True).stdout.strip()


def log(text):
    print(f'{datetime.now():%H:%M:%S} cores={cores()} {text}', flush=True)


def repeats(n_base, n_rule, share=RULE_SHARE):
    """Whole repeats of the rule rows that come closest to ``share`` of the epoch."""
    return max(1, min(range(1, 200), key=lambda k: abs(k*n_rule/(n_base+k*n_rule)-share)))


def iters_for(rows, accumulation=4):
    return rows//accumulation*accumulation


def read_rows(path):
    with open(path) as stream:
        return [line for line in stream if line.strip()]


def merge(base_lines, rule_lines, val_ids, k):
    rule_ids = {json.loads(line)['instance_id'] for line in rule_lines}
    if rule_ids & val_ids:
        raise SystemExit(f'rule rows on 8.2 val maps: {sorted(rule_ids & val_ids)[:3]}')
    return base_lines+rule_lines*k


def build_data(target, workers='2'):
    snapshot = SNAPSHOT.resolve()
    records = target.with_name('rule-rows-2.2')
    if not (records/'train.jsonl').exists():
        subprocess.run([PYTHON, str(ROOT/'experiments/mvp/sft/dataset/build.py'), '--out', str(records), '--train', str(snapshot/'records'), '--workers', workers],
                       cwd=ROOT, env=ENV, stdout=open(target.with_name('build.log'), 'a'), stderr=subprocess.STDOUT, check=True)
    base, val = read_rows(V2/'data/train.jsonl'), read_rows(V2/'data/val.jsonl')
    rule = read_rows(records/'train.jsonl')
    k = repeats(len(base), len(rule))
    lines = merge(base, rule, {json.loads(line)['instance_id'] for line in val}, k)
    temporary = target.with_name(target.name+'.tmp')
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    (temporary/'train.jsonl').write_text(''.join(lines))
    shutil.copyfile(V2/'data/val.jsonl', temporary/'val.jsonl')
    stats = json.loads((V2/'data/stats.json').read_text())
    stats['splits']['train']['examples'] = len(lines)
    (temporary/'stats.json').write_text(json.dumps(stats, indent=1)+'\n')
    tokens = lambda rows: sum(len(json.loads(x)['input_ids']) for x in rows)
    base_ids = {json.loads(x)['instance_id'] for x in base}
    manifest = dict(decision='D209', snapshot=str(snapshot), snapshot_records=len(rule), repeats=k, base_rows=len(base), train_rows=len(lines), rule_share_rows=len(rule)*k/len(lines),
                    rule_share_tokens=tokens(rule)*k/tokens(lines), rule_maps_also_in_82=len({json.loads(x)['instance_id'] for x in rule} & base_ids), iters=iters_for(len(lines)),
                    optimizer_updates=iters_for(len(lines))//4, learning_rate=LR, prompt='stowlm-prompt@2.2', val_sha256=sha(temporary/'val.jsonl'), parent_adapter_sha256=sha(V2/'adapter/adapters.safetensors'))
    (temporary/'manifest.json').write_text(json.dumps(manifest, indent=1)+'\n')
    temporary.rename(target)
    return manifest


def train(manifest):
    parent_sha = sha(V2/'adapter/adapters.safetensors')
    args = (V2/'train.cmd').read_text().split()
    args = args[args.index('llm.stowlm.train')+1:]
    start = args.index('--data-dir')
    args[start+1], args[args.index('--adapter-dir')+1] = str(RUN/'data'), str(RUN/'adapter')
    args[args.index('--learning-rate')+1] = str(LR)
    args[args.index('--iters')+1] = str(manifest['iters'])
    log(f"train: {manifest['iters']} steps, {manifest['optimizer_updates']} updates, LR {LR}")
    with open(RUN/'train.log', 'a') as stream:
        subprocess.run([PYTHON, str(RUN/'train_warm.py'), str(V2/'adapter'), *args], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT, check=True)
    if sha(V2/'adapter/adapters.safetensors') != parent_sha:
        raise SystemExit('8.2 adapter changed')
    (RUN/'DONE.json').write_text(json.dumps(dict(marker='8.5a complete', adapter=str(RUN/'adapter'), at=datetime.now().astimezone().isoformat()), indent=1)+'\n')


def reader_v2():
    """D220: reader v2 runs first, ahead of 8.5a. gpu_run_v2 owns the 3 h hard stop and the sealed-v2 scoring; a
    crash isn't resumable, so it's recorded and the decode goes on."""
    marker = RUN/'reader-v2.DONE'
    if marker.exists():
        return
    env = {k: v for k, v in ENV.items() if k not in ('K1B_OUT', 'COMPACT_FINISHER_S')}
    env['TOKENIZERS_PARALLELISM'] = 'false'
    module = 'experiments.mvp.reader.'
    results = []
    for command in ([module+'freeze_v2', '--stage', 'recipe', '--check'], [module+'gpu_run_v2', '--train-only'],
                    [module+'gpu_run_v2', '--score-final', '--spark-summary', 'experiments/mvp/reader/sealed-v2/spark-summary.json'], [module+'freeze_v2', '--stage', 'final', '--check']):
        log(f'reader v2: {command}')
        with open(ROOT/'experiments/mvp/reader/gpu-run-v2.out', 'a') as stream:
            code = subprocess.run([PYTHON, '-m', *command], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT).returncode
        results.append(dict(command=command, code=code))
        if code:
            break
    marker.write_text(json.dumps(dict(results=results, at=datetime.now().astimezone().isoformat()), indent=1)+'\n')
    log(f'reader v2 finished: {[r["code"] for r in results]}')


def reader_passed(path=ROOT/'experiments/mvp/reader/results-v2/gates.json'):
    """D220: 8.5a runs only if reader v2 passes all three gates; a missing file is a miss."""
    if not Path(path).exists():
        return False
    gates = json.loads(Path(path).read_text())
    return all(gates.get(name) is True for name in ('R1', 'R2', 'R3'))


def decode(files, shards):
    env = {**ENV, 'STOWLM_MLX_CACHE_LIMIT_GB': '2'}
    groups = [sorted(files)[i::shards] for i in range(shards)]

    def one(i):
        with open(OUT/f'decode/s{i}.log', 'a') as stream:
            return subprocess.run([PYTHON, 'experiments/eval-c1/run_c1.py', '--run-dir', str(OUT/f'decode/s{i}'), '--adapter', str((RUN/'adapter').relative_to(ROOT)), '--loose', *groups[i], *K1B['flags'],
                                   '--store-candidates', str(OUT/'candidates'), '--decode-only'], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT).returncode
    (OUT/'decode').mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(shards) as pool:
        codes = list(pool.map(one, range(shards)))
    if any(codes):
        raise SystemExit(f'decode failed: {codes}')


def audit_arm(jobs):
    """Sample 0 through skill-check's deterministic audit: same compaction, sight and lashing replay, no finisher."""
    import importlib.util
    import compact
    from stowage.io import load_instance
    from stowage.model import MovePlan
    spec = importlib.util.spec_from_file_location('skill_part1', HERE/'skill-check/part1.py')
    part1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(part1)
    rows = {}
    for job in jobs:
        iid = job['instance_id']
        target = OUT/'audit'/f'{iid}.json'
        if target.exists():
            rows[iid] = json.loads(target.read_text())
            continue
        sample = json.loads((OUT/'candidates'/iid/'sample-00.json').read_text())
        row = dict(instance_id=iid, complete=sample['plan'] is not None, clear=False)
        if row['complete']:
            instance = load_instance(ROOT/job['input'])
            plan = MovePlan.model_validate(sample['plan'])
            compacted, info = compact.compact_plan(instance, plan)
            better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
            result = part1.deterministic(instance, part1.Problem(instance), compacted if better else plan)
            row.update(clear=bool(result['sight_clear']), all_clear=result['all_clear'])
        judge.publish(target, json.dumps(row, indent=1))
        rows[iid] = row
    return rows


def regression(mine, theirs=45, n=55):
    """Mine: a plain statement, not a verdict D209 sets. 5 or more fewer maps counts as well below."""
    return 'REGRESSION' if mine <= theirs-5 else 'kept' if mine >= theirs-1 else 'slipped'


def report(jobs, audits, manifest):
    rows = lambda folder: {x['instance_id']: x for x in map(json.loads, (p.read_text() for p in Path(folder).glob('*.json')))}
    new, old = rows(OUT/'judged/model'), rows(HERE/'judged/model')
    samples = lambda root, iid: [json.loads(p.read_text()) for p in sorted((root/'candidates'/iid).glob('sample-*.json'))]
    stats = {}
    for name, root in (('8.5a', OUT), ('8.2', HERE)):
        pool = [s for job in jobs for s in samples(root, job['instance_id'])]
        stats[name] = dict(complete=sum(s['status'] == 'complete' for s in pool), n=len(pool), rewinds=sum(s['backtracks'] for s in pool), dead=sum(s['status'] == 'dead_end' for s in pool))
    base = {p.parent.name: json.loads(p.read_text()) for p in (HERE/'skill-check/rows/8.2').glob('*/sample-00.json')}
    clear = {i: bool(x['clear']) for i, x in audits.items()}
    old_clear = {i: bool((x.get('deterministic') or x).get('sight_clear')) for i, x in base.items()}
    only_new, only_old = sum(clear[i] and not old_clear.get(i) for i in clear), sum(old_clear.get(i) and not clear[i] for i in clear)
    count = sum(clear.values())
    lines = ['# 8.5a: 8.2 resumed on rule labels (D209)', '',
             f"Validation only, 55 small maps, K1b's recipe. Trained from 8.2's adapter on {manifest['base_rows']} 8.2 rows plus {manifest['snapshot_records']} rule labels repeated {manifest['repeats']} times ({manifest['rule_share_rows']*100:.0f} % of rows, {manifest['rule_share_tokens']*100:.0f} % of tokens), {manifest['iters']} steps, {manifest['optimizer_updates']} updates, LR {manifest['learning_rate']}, prompt 2.2. The rule rows' maps are ones the rule passes, so they lean easier (D209). Judged in a different window from 8.2.", '',
             '| Measure | 8.5a | 8.2 |', '|---|---:|---:|',
             *[f"| {label} | {f(stats['8.5a'])} | {f(stats['8.2'])} |" for label, f in (('Complete samples', lambda s: f"{s['complete']}/{s['n']} ({s['complete']/s['n']*100:.0f} %)"), ('Dead-end samples', lambda s: str(s['dead'])), ('Rewinds', lambda s: str(s['rewinds'])))],
             f"| Sample 0 clears sight and lashing | {count}/55 | 45/55 |", '',
             f"**Sample-0 sight clear, paired with 8.2:** only 8.5a {only_new}, only 8.2 {only_old} (sign p = {r.sign_p(only_new, only_old):.3f}). **{regression(count).upper()}**: my threshold, 5 or more maps fewer counts as well below.", '',
             *r.HEAD, r.row_of('8.5a K1b vs 8.2 K1b', r.pair({i: x['moves'] for i, x in new.items()}, {i: x['moves'] for i, x in old.items()})), '']
    (HERE/'K85A.md').write_text('\n'.join(lines))


def main():
    while '--now' not in sys.argv and not WAIT_FOR.exists():
        time.sleep(60)
    log('start')
    RUN.mkdir(exist_ok=True)
    reader_v2()
    if '--after-leader' not in sys.argv and not reader_passed():
        (RUN/'WAITING-FOR-LEADER').write_text('Reader v2 missed a gate or crashed. 8.5a waits for the leader (D220); a reader v3 may take the GPU first. Sealed v2 is not touched again.\n')
        log('reader v2 did not pass all gates: stopping before 8.5a')
        return
    if not (RUN/'data/manifest.json').exists():
        build_data(RUN/'data')
    manifest = json.loads((RUN/'data/manifest.json').read_text())
    if not (RUN/'DONE.json').exists():
        train(manifest)
    log('trained')
    files = sorted(K1B['inputs'])
    OUT.mkdir(exist_ok=True)
    if not (OUT/'decode.DONE').exists():
        decode(files, 3)
        (OUT/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
    log('decoded')
    jobs = judge.jobs(files)
    with ProcessPoolExecutor(3) as pool:
        list(pool.map(judge.judge_model, jobs))
    audits = audit_arm(jobs)
    report(jobs, audits, manifest)
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| k85a/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k85a/run (fix-t8-sft, 31 Oct-1 Nov): D209 8.5a; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (OUT/'DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
