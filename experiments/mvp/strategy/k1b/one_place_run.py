"""D199 step 3: one-placement decode, after the ladder decode ends, within 2 GPU hours: the 15 small ablation maps at K1b's recipe, and the 6 f40 ladder maps on feeder_500
and feeder_800 at 1,200 s per map. Reports complete samples, dead ends, actions per box and time per sample against K1b and the ladder on the same maps. Also refreshes
K2-TELEMETRY.md. Detached and resumable. Usage: one_place_run.py [--now]
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import statistics as st
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
OUT = HERE/'one-place'
ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
K1B = json.loads((HERE/'manifest.json').read_text())
ADAPTER = 'experiments/mvp/sft/stowlm-v2/adapter'
GPU_HOURS_S = 7200
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '2', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'PYTHONPATH': str(HERE), **{k: '1' for k in THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)


def flags(limit):
    values = list(K1B['flags'])
    values[values.index('--time-limit')+1] = str(limit)
    return values


def maps():
    small = (ROOT/'experiments/mvp/valsplit/ablation_small15.txt').read_text().split()
    rows = json.loads((HERE.parent/'fillsweep/cohort.json').read_text())['maps']
    ladder = [m['input'] for m in sorted(rows, key=lambda x: (x['n_loads'], x['map_id']))
              if m['fill_target'] == 40 and m['mass_level'] == 'low' and m['ship'] in ('feeder_500', 'feeder_800')]
    if len(small) != 15 or len(ladder) != 6:
        raise SystemExit('wrong map lists')
    return small, ladder


def decode(small, ladder):
    jobs = [('small', small, 7200), ('ladder0', ladder[0::2], 1200), ('ladder1', ladder[1::2], 1200)]

    def one(job):
        name, files, limit = job
        with open(OUT/f'{name}.log', 'a') as stream:
            try:
                return subprocess.run([PYTHON, str(HERE/'one_place_decode.py'), '--run-dir', str(OUT/'decode'/name), '--adapter', ADAPTER, '--loose', *files, *flags(limit),
                                       '--store-candidates', str(OUT/'candidates'), '--decode-only'], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT, timeout=GPU_HOURS_S).returncode
            except subprocess.TimeoutExpired:
                return 'cut at the 2 h budget'
    (OUT/'decode').mkdir(exist_ok=True)
    with ThreadPoolExecutor(3) as pool:
        return list(pool.map(one, jobs))


def stats(root, iid, records):
    samples = [json.loads(p.read_text()) for p in sorted((root/'candidates'/iid).glob('sample-*.json'))]
    complete = [s for s in samples if s['plan'] is not None]
    rec = records.get(iid) or {}
    wall = (rec.get('metrics') or {}).get('pool_plan_wall_s') or rec.get('pipeline_s')
    return dict(reached=len(samples), complete=len(complete), dead_ends=sum(s['status'] == 'dead_end' for s in samples), rewinds=sum(s['backtracks'] for s in samples),
                actions=[len(s['plan']['actions']) for s in complete], wall=wall)


def records(root):
    found = {}
    for path in root.glob('decode/*/instances/*.json'):
        rec = json.loads(path.read_text())
        found[rec['instance_id']] = rec
    return found


def line(name, boxes, a):
    per = f"{st.mean(a['actions'])/boxes:.2f}" if a['actions'] else '—'
    each = f"{a['wall']/a['reached']:.0f}" if a['wall'] and a['reached'] else '—'
    return f"{a['reached']}/16 | {a['complete']} | {a['dead_ends']} | {a['rewinds']} | {per} | {each} |"


def report(codes):
    from stowage.io import load_instance
    small, ladder = maps()
    mine = records(OUT)
    k1b, k2 = records(HERE), records(HERE/'k2')
    lines = ['# One-placement decode (D199)', '',
             "An outer mask layer forbids a Place on a box the plan already placed. 8.2 unchanged, K1b's flags, no training; the rule isn't in the mask. Validation and train ladder maps; decode telemetry only, with no judge, so nothing here is a pass or a move count. "
             f'Baselines are the same maps in K1b and the ladder decode, which ran at different times and load. Shard results: {codes}.', '']
    for title, files, base, baseroot, note in (('15 small validation maps, 16 samples', small, k1b, HERE, 'K1b'), ('6 f40 ladder maps, 1,200 s per map', ladder, k2, HERE/'k2', 'ladder at 3,600 s')):
        lines += [f'## {title}', '', f'| Map | Boxes | One-placement: reached | Complete | Dead ends | Rewinds | Actions per box | s/sample | {note}: reached | Complete | Dead ends | Rewinds | Actions per box | s/sample |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        totals, baseline = [], []
        for f in files:
            instance = load_instance(ROOT/f)
            a, b = stats(OUT, instance.id, mine), stats(baseroot, instance.id, base)
            n = len(instance.load_list)
            totals.append(a)
            baseline.append(b)
            lines.append(f"| {instance.id.replace('D192.medium-', '')} | {n} | {line('', n, a)} {line('', n, b)}")
        count = lambda rows, key: sum(r[key] for r in rows)
        lines += ['', f"**Totals.** One-placement: {count(totals, 'reached')} reached, {count(totals, 'complete')} complete, {count(totals, 'dead_ends')} dead ends. {note}: {count(baseline, 'reached')} reached, {count(baseline, 'complete')} complete, {count(baseline, 'dead_ends')} dead ends.", '']
    (HERE/'ONE-PLACEMENT.md').write_text('\n'.join(lines))


def main():
    while '--now' not in sys.argv and not (HERE/'k2/decode.DONE').exists():
        time.sleep(30)
    subprocess.run([PYTHON, str(HERE/'k2_telemetry.py'), '2'], cwd=ROOT, env=ENV, stdout=open(HERE/'k2_telemetry.log', 'a'), stderr=subprocess.STDOUT)
    OUT.mkdir(exist_ok=True)
    small, ladder = maps()
    codes = decode(small, ladder)
    report(codes)
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| one-place/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- one-place/run (fix-t8-sft, 31 Oct): D199 one-placement decode test; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (OUT/'DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
