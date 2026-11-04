"""Ladder table from frozen rows, against c2-layout's fixed sweep. Runs no checker."""
import collections
import json
from pathlib import Path
import statistics as st

HERE = Path(__file__).resolve().parent
dev = set(l.strip() for l in open(HERE/'dev6.txt'))
L = HERE.parents[1]/'c4/c2-layout/results'
old = {}
for run in ('ladder60-70-dense', 'f80-dense', 'medium12-dense'):
    for p in (L/run).glob('*.json'):
        r = json.loads(p.read_text())
        if 'instance_id' in r:
            old[r['instance_id']] = r


def rung(i):
    return next((f for f in ('f60', 'f70', 'f80') if f'-{f}-' in i), 'd185')


rows = [json.loads(p.read_text()) for p in sorted((HERE/'results/frozen-48').glob('*.json'))]
rows = [r for r in rows if 'instance_id' in r]
table = collections.defaultdict(lambda: collections.Counter())
lifts = collections.defaultdict(list)
paired = []
for r in rows:
    g = rung(r['instance_id'])
    i = r['instance_id']
    for scope in ('all', 'dev' if r['input'] in dev else 'rest'):
        table[g, scope]['maps'] += 1
        table[g, scope]['passed'] += bool(r['passed'])
        table[g, scope]['old'] += bool(old[i].get('passed'))
    table[g, 'all']['both'] += bool(r['passed'] and old[i].get('passed'))
    table[g, 'all']['lost'] += bool(old[i].get('passed') and not r['passed'])
    table[g, 'all']['new'] += bool(r['passed'] and not old[i].get('passed'))
    if r['passed']:
        lifts[g].append(r['lifts'])
    if r['passed'] and old[i].get('passed'):
        paired.append((g, i, r['lifts'], old[i]['lifts']))
for k in sorted(table):
    print(k, dict(table[k]), round(st.mean(lifts[k[0]]), 1) if k[1] == 'all' and lifts[k[0]] else '')
print('paired', len(paired), st.mean(p[2] for p in paired), st.mean(p[3] for p in paired))
for g in ('f60', 'f70'):
    q = [p for p in paired if p[0] == g]
    print(g, len(q), st.mean(p[2] for p in q), st.mean(p[3] for p in q), sum(p[2] < p[3] for p in q), sum(p[2] > p[3] for p in q))
print('wall passed mean', st.mean(r['wall_s'] for r in rows if r['passed']), 'failed mean', st.mean(r['wall_s'] for r in rows if not r['passed']))
print('load', sorted(r['method_metadata'].get('loadavg_start', [0])[0] for r in rows)[::47])
for r in rows:
    if not r['passed']:
        t = r['method_metadata'].get('trials', [])
        done = [q for q in t if q.get('rules') is not None]
        best = min(done, key=lambda q: (len(q['rules']), q.get('exact_ratio', 9)), default=None)
        print(r['instance_id'][-32:], r['n_boxes'], r['status'], None if best is None else (best.get('lifts'), round(best.get('exact_ratio', 0), 2), [x.split('.')[-1][:10] for x in best['rules']]),
              'old', [x[:10] for x in old[r['instance_id']].get('method_metadata', {}).get('trials', [{}])[0].get('rules', [])][:0], 'fallback', sum(bool(q.get('fallback')) for q in t), len(t))
