"""D174 detached rest: 600 s retry of the train plans that hit the finisher limit, the restow probe, then, when the validation
decode ends, compaction of its plans and the validation section of COMPACT.md. Usage: compact_followup.py <decode_pid>"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from math import comb
import statistics as st
import subprocess
import sys
import time

import compact_report as cr

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PYTHON = str(ROOT/'.venv/bin/python')
ENV = {**os.environ, 'PYTHONPATH': str(HERE), 'COMPACT_FINISHER_S': '600'}
ENV.pop('STOWAGE_BALLAST_V4', None)
AUDIT82 = ROOT/'experiments/mvp/exit/deployment-d137/parent-audit-8.2/audit-baseline/summary.json'
AUDIT_TWO = ROOT/'experiments/mvp/sft/val-audit30-twostep/120'
RESERVE_TWO = ROOT/'experiments/mvp/sft/final-validation/twostep/reserve25'


def sign_p(a, b):
    n = a+b
    return min(1., 2*sum(comb(n, k) for k in range(min(a, b)+1))/2**n) if n else 1.


def two_step():
    rows = [json.loads(p.read_text()) for folder in (AUDIT_TWO, RESERVE_TWO) for p in folder.glob('*.json')]
    return {r['instance_id']: r.get('weighted_nonproductive') if r['passed'] is True else None for r in rows}


def pair_line(name, mine, rival):
    shared = [m for m in rival if mine.get(m) is not None and rival[m] is not None]
    only_l, only_r = sum(mine.get(m) is not None and rival[m] is None for m in rival), sum(mine.get(m) is None and rival[m] is not None for m in rival)
    fewer, more = sum(mine[m] < rival[m] for m in shared), sum(mine[m] > rival[m] for m in shared)
    left, right = st.mean(mine[m] for m in shared), st.mean(rival[m] for m in shared)
    return (f"| {name} | {sum(v is not None for v in mine.values())}/55 | {sum(v is not None for v in rival.values())}/55 | {only_l} / {only_r} ({sign_p(only_l, only_r):.3f}) | {len(shared)} | "
            f"{left:.2f} vs {right:.2f} | {(left/right-1)*100:+.1f} % | {fewer} / {len(shared)-fewer-more} / {more} ({sign_p(fewer, more):.4f}) |")


def main():
    subprocess.run([PYTHON, str(HERE/'compact.py'), 'retry', '4'], cwd=ROOT, env=ENV, stdout=open(HERE/'compact-train-retry.log', 'a'), stderr=subprocess.STDOUT, check=True)
    subprocess.run([PYTHON, str(HERE/'restow_probe.py'), '2'], cwd=ROOT, env=ENV, stdout=open(HERE/'restow-probe.log', 'a'), stderr=subprocess.STDOUT)
    pid = int(sys.argv[1])
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(30)
    subprocess.run([PYTHON, str(HERE/'compact.py'), 'folder', str(HERE/'val-greedy-82/run'), str(HERE/'compact-val'), '4'], cwd=ROOT, env=ENV,
                   stdout=open(HERE/'compact-val.log', 'a'), stderr=subprocess.STDOUT, check=True)
    items = cr.rows(HERE/'compact-val')
    rival = two_step()
    before = {r['instance_id']: r['before_moves'] if r['before_passed'] else None for r in items}
    after = {r['instance_id']: r['final_moves'] if r['final_passed'] else None for r in items}
    retry = cr.rows(HERE/'compact-train-retry')
    lines = ['', '## Train: the plans that hit the finisher limit, retried at 600 s', '',
             f"{len(retry)} plans: {sum(r['kept'] == 'compact' for r in retry)} kept, {sum(r['attempted'] and r['kept'] == 'original' for r in retry)} still not.", '', *cr.table(retry), '',
             '## Validation: 8.2 greedy on audit30 + reserve25, compacted', '',
             'Re-decoded with the 9.1 recipe (greedy, mask + securing + look-ahead, 32 rewinds, 120 s finisher), storing plans. Compaction used a 600 s finisher. '
             'Two-step is D156 (120 s finisher). Compaction here changed no recipe and uses no test map. Exact two-sided sign tests, ties dropped, no adjustment.', '',
             '| Arm vs two-step | Passed | Two-step passed | Only arm / only two-step (p) | Mutual | Mean moves | Δ | Fewer / equal / more (p) |', '|---|---:|---:|---:|---:|---:|---:|---:|',
             pair_line('8.2 greedy', before, rival), pair_line('8.2 greedy, compacted', after, rival), '', *cr.table(items), '', *cr.breakdown(items), '']
    probe = HERE/'restow-probe.log'
    if probe.exists():
        lines += ['## Same-call restow ban: replay probe (report only)', '', probe.read_text().strip()[-500:], '']
    with open(HERE/'COMPACT.md', 'a') as stream:
        stream.write('\n'.join(lines))
    ledger = ROOT/'experiments/fleet/ledger.md'
    ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True) if not line.startswith('| compact/')))
    with open(ROOT/'experiments/fleet/history.md', 'a') as history:
        history.write(f'\n- compact/d174 (fix-t8-sft, 30 Oct): train + validation compaction, ≤ 5 CPU + one GPU decode; finished {datetime.now().astimezone().isoformat(timespec="minutes")}.\n')
    (HERE/'COMPACT.DONE').write_text(datetime.now().astimezone().isoformat()+'\n')


if __name__ == '__main__':
    main()
