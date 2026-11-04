"""D183 null arm: the K1b recipe with untrained base Glimmer + mask, no adapter. Same 55 maps, seeds, flags, compaction and judge
as K1b; the baseline rows are K1b's own. Detached and resumable. Usage: null_driver.py [workers=4] [shards=2]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
OUT = HERE/'null'
os.environ.update(K1B_OUT=str(OUT), COMPACT_FINISHER_S='600')  # before compact and judge are imported; spawned workers inherit it
sys.path.insert(0, str(HERE.parent))
import judge
import k1b_report as r

ROOT = HERE.parents[3]
PYTHON = str(ROOT/'.venv/bin/python')
K1B = json.loads((HERE/'manifest.json').read_text())
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')
ENV = {**os.environ, 'STOWLM_MLX_CACHE_LIMIT_GB': '2', 'UV_CACHE_DIR': str(ROOT/'.uv-cache'), 'PYTHONPATH': f'{HERE}:{HERE.parent}', **{k: '1' for k in THREADS}}
ENV.pop('STOWAGE_BALLAST_V4', None)


def decode(files, shards):
    groups = [sorted(files)[i::shards] for i in range(shards)]

    def one(i):
        with open(OUT/f'decode/s{i}.log', 'a') as stream:
            return subprocess.run([PYTHON, 'experiments/eval-c1/run_c1.py', '--run-dir', str(OUT/f'decode/s{i}'), '--loose', *groups[i], *K1B['flags'],
                                   '--store-candidates', str(OUT/'candidates'), '--decode-only'], cwd=ROOT, env=ENV, stdout=stream, stderr=subprocess.STDOUT).returncode
    (OUT/'decode').mkdir(exist_ok=True)
    with ThreadPoolExecutor(shards) as pool:
        codes = list(pool.map(one, range(shards)))
    if any(codes):
        raise SystemExit(f'decode failed: {codes}')


def rows(folder):
    return {x['instance_id']: x for x in map(json.loads, (p.read_text() for p in Path(folder).glob('*.json')))}


def report():
    null, trained, baseline = rows(OUT/'judged/model'), rows(HERE/'judged/model'), rows(HERE/'judged/baseline')
    moves = lambda arm: {i: x['moves'] for i, x in arm.items()}
    tally = lambda arm: {k: sum(x['outcome'] == k for x in arm.values()) for k in sorted({x['outcome'] for x in arm.values()})}
    passed = [x for x in null.values() if x['moves'] is not None]
    lines = ['# K1b null arm (D183)', '',
             'Untrained base Glimmer + mask, no adapter: the exact K1b recipe on the same 55 small validation maps, with the same seeds, D167 flags, compaction and judge. '
             "The baseline is K1b's compacted two-step. Exact two-sided sign tests, ties dropped, no adjustment.", '',
             *r.HEAD, r.row_of('Null vs 8.2 K1b', r.pair(moves(null), moves(trained))), r.row_of('Null vs compacted two-step', r.pair(moves(null), moves(baseline))),
             r.row_of('8.2 K1b vs compacted two-step', r.pair(moves(trained), moves(baseline))), '',
             f'**Outcomes.** Null: {tally(null)}. 8.2 K1b: {tally(trained)}.',
             f"**Complete candidates per map, of 16.** Null {sum(x['n_complete'] for x in null.values())/55:.1f}, 8.2 {sum(x['n_complete'] for x in trained.values())/55:.1f}.",
             f"**Selection.** Null winner greedy on {sum(x['winner_sample'] == 0 for x in passed)} of {len(passed)} passing maps; compacted on {sum(x['winner_compacted'] is True for x in passed)}; "
             f"first ranked candidate passed on {sum(len(x['trials']) == 1 for x in passed)}.", '']
    (HERE/'NULL.md').write_text('\n'.join(lines))


def main():
    workers, shards = int(sys.argv[1]) if len(sys.argv) > 1 else 4, int(sys.argv[2]) if len(sys.argv) > 2 else 2
    files = sorted(K1B['inputs'])
    OUT.mkdir(exist_ok=True)
    if not (OUT/'decode.DONE').exists():
        decode(files, shards)
        (OUT/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
    os.environ.update({k: '1' for k in THREADS})
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    with ProcessPoolExecutor(workers) as pool:
        list(pool.map(judge.judge_model, judge.jobs(files)))
    report()
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| k1b-null/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k1b-null/run (fix-t8-sft, 30-31 Oct): D183 null arm; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (OUT/'NULL.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
