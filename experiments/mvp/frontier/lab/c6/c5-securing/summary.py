"""Cycle 6 ladder against the frozen cycle 5 run, both cold. Names every map that lost a pass. Runs no checker."""
import collections
import json
from pathlib import Path
import statistics as st
import sys

HERE = Path(__file__).resolve().parent
dev = set(l.strip() for l in open(HERE/'dev12.txt'))


def load(path):
    rows = {}
    for p in sorted(path.glob('*.json')):
        r = json.loads(p.read_text())
        if 'instance_id' in r:
            rows[r['instance_id']] = r
    return rows


old = load(HERE.parents[1]/'c5/c5-securing/results/frozen-48')
new = load(HERE/'results/frozen-48')


def rung(i):
    return next((f for f in ('f60', 'f70', 'f80') if f'-{f}-' in i), 'd185')


t = collections.defaultdict(collections.Counter)
for i, r in new.items():
    g = rung(i)
    for scope in (g, 'dev' if r['input'] in dev else 'not dev', 'all'):
        t[scope]['maps'] += 1
        t[scope]['now'] += bool(r['passed'])
        t[scope]['before'] += bool(old[i]['passed'])
        t[scope]['gained'] += bool(r['passed'] and not old[i]['passed'])
        t[scope]['lost'] += bool(old[i]['passed'] and not r['passed'])
for k, v in t.items():
    print(k, dict(v))
print('lost', [i for i, r in new.items() if old[i]['passed'] and not r['passed']])
print('gained', [(i[-32:], r['lifts']) for i, r in new.items() if r['passed'] and not old[i]['passed']])
both = [(new[i]['lifts'], old[i]['lifts']) for i in new if new[i]['passed'] and old[i]['passed']]
print('both', len(both), st.mean(a for a, _ in both), st.mean(b for _, b in both), sum(a < b for a, b in both), sum(a > b for a, b in both))
print('wall passed', st.mean(r['wall_s'] for r in new.values() if r['passed']), 'failed', st.mean([r['wall_s'] for r in new.values() if not r['passed']] or [0]))
print('load', sorted(r['method_metadata'].get('loadavg_start', [0])[0] for r in new.values())[::47])
