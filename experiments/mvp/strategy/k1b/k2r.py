"""D195: K1r on the fill ladder, so K2-ladder can be read against chance. CPU only, no LLM. Starts when the ladder's decode is done: uniform random
legal actions under K1b's mask stack, 16 samples, 3,600 s per map, then the ladder's judge (1,200 s finisher, at most 4 tries), then a section in
K2-LADDER.md once K2.DONE exists. Also rewrites K1R.md and KCURVE.md after K1R.DONE, with the wording of the band fixed. Usage: k2r.py [workers=3]
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
import time

import k2ladder  # first: it sets the 1,200 s finisher before compact is imported
import k1r
import k1r_report

HERE = Path(__file__).resolve().parent
OUT = HERE/'k2r'
ROOT = HERE.parents[3]


def bind_worker():
    if k2ladder.compact.BUDGET_S != 1200:
        raise SystemExit('wrong finisher budget')
    k1r.OUT = k2ladder.OUT = OUT


def decode_one(job):
    bind_worker()
    import k2r_decode
    return k2r_decode.decode_map(job, OUT, k2ladder.TIME_LIMIT)


def judge_one(job):
    bind_worker()
    return k2ladder.judge_map(job)


def section(random_rows, model_rows):
    model = {r['map_id']: r for r in model_rows}
    lines = ['', '## K1r on the same maps (D195)', '',
             "Uniform random legal actions under the same mask stack, with no LLM and no GPU: 16 samples, 3,600 s per map, then the same judge (1,200 s finisher, at most 4 tries). Read K2-ladder against this.", '',
             '| Map | Boxes | Samples reached | Complete | Actions per box | K1r | Moves | 8.2 | 8.2 moves | Default | Rule |', '|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|']
    mark = lambda s, arm: 'pending' if s is None else ('pass' if s[arm] else 'fail')
    for r in sorted(random_rows, key=lambda x: (x['fill'], x['n_loads'])):
        s, m = k2ladder.sweep(r['map_id']), model[r['map_id']]
        lines.append(f"| {r['map_id']} | {r['n_loads']} | {r['samples_reached']}/16 | {r['n_complete']} | {'—' if r['actions_per_box'] is None else format(r['actions_per_box'], '.2f')} | "
                     f"{'pass' if r['moves'] is not None else 'no pass'} | {'—' if r['moves'] is None else format(r['moves'], 'g')} | {'pass' if m['moves'] is not None else 'no pass'} | "
                     f"{'—' if m['moves'] is None else format(m['moves'], 'g')} | {mark(s, 'default')} | {mark(s, 'rule')} |")
    both = [r for r in random_rows if r['moves'] is not None and model[r['map_id']]['moves'] is not None]
    only_model = [r['map_id'] for r in random_rows if r['moves'] is None and model[r['map_id']]['moves'] is not None]
    only_random = [r['map_id'] for r in random_rows if r['moves'] is not None and model[r['map_id']]['moves'] is None]
    lines += ['', f"**Passes.** K1r {sum(r['moves'] is not None for r in random_rows)}/18, 8.2 {sum(m['moves'] is not None for m in model.values())}/18. Only 8.2: {', '.join(only_model) or 'none'}. Only K1r: {', '.join(only_random) or 'none'}. "
              f"Mutual passes: {len(both)}" + (f", mean moves K1r {sum(r['moves'] for r in both)/len(both):.2f} vs 8.2 {sum(model[r['map_id']]['moves'] for r in both)/len(both):.2f}." if both else '.')]
    wins = [r['map_id'] for r in random_rows if r['moves'] is not None and (s := k2ladder.sweep(r['map_id'])) and not s['default'] and not s['rule']]
    lines += [f"**Does chance also pass a map both default and rule fail?** {', '.join(wins) or 'no'}. A K2-ladder win counts against chance only if K1r fails that map.", '']
    return '\n'.join(lines)


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    while not (HERE/'k2/decode.DONE').exists():
        time.sleep(60)
    OUT.mkdir(exist_ok=True)
    from stowage.io import load_instance
    jobs = [dict(m, instance_id=load_instance(ROOT/m['input']).id) for m in k2ladder.maps()]
    if not (OUT/'decode.DONE').exists():
        with ProcessPoolExecutor(workers) as pool:
            list(pool.map(decode_one, jobs))
        (OUT/'decode.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')
    with ProcessPoolExecutor(workers) as pool:
        random_rows = list(pool.map(judge_one, jobs))
    while not (HERE/'k2/K2.DONE').exists():
        time.sleep(60)
    model_rows = [json.loads(p.read_text()) for p in (HERE/'k2/judged').glob('*.json')]
    with open(HERE/'K2-LADDER.md', 'a') as stream:
        stream.write(section(random_rows, model_rows))
    while not (HERE/'k1r/K1R.DONE').exists():
        time.sleep(60)
    k1r_report.write_all()
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| k2r/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- k2r/run (fix-t8-sft, 31 Oct): D195 K1r on the fill ladder, CPU only; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (OUT/'K2R.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
