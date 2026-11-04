"""Compare a frontier run with stored two-step and StowLM rows, map by map. Usage: report.py <run_dir> <set: val|test>"""
import glob, json, sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def sign(w, l):
    n = w+l
    return min(1., 2*sum(comb(n, i) for i in range(min(w, l)+1))/2**n) if n else 1.


def rows(run):
    out = {}
    for f in glob.glob(f'{run}/*.json'):
        if f.endswith('AUDIT.json'):
            continue
        d = json.load(open(f))
        out[d['instance_id']] = d
    return out


def stored_val():
    fv = ROOT/'experiments/mvp/sft/final-validation'
    two, s82 = {}, {}
    def add(target, path, key='weighted_moves'):
        data = json.load(open(path))
        items = data if isinstance(data, list) else list(data.get('rows', data).values()) if isinstance(data.get('rows', data), dict) else data['rows']
        for r in items:
            target[r['instance_id']] = r.get(key) if r.get('passed') else None
    for name in ('reserve25', 'tiny48'):
        add(s82, fv/'8.2'/name/'rows.json')
        for f in glob.glob(str(fv/'twostep'/name/'*.json')):
            r = json.load(open(f))
            if 'instance_id' in r:
                two[r['instance_id']] = r.get('weighted_nonproductive') if r.get('passed') else None
    add(s82, ROOT/'experiments/mvp/exit/deployment-d137/audit-baseline/epoch-a8606f863639bb6b/summary.json')
    tw = json.load(open(ROOT/'experiments/mvp/sft/val-audit30-twostep/paired-120.json')) if (ROOT/'experiments/mvp/sft/val-audit30-twostep/paired-120.json').exists() else None
    for f in glob.glob(str(ROOT/'experiments/mvp/sft/val-audit30-twostep/120/*.json')):
        r = json.load(open(f))
        if 'instance_id' in r:
            two[r['instance_id']] = r.get('weighted_nonproductive') if r.get('passed') else None
    return two, s82


def stored_test():
    run = ROOT/'experiments/mvp/runs/test-91'
    two, stow = {}, {}
    for f in glob.glob(str(run/'twostep/*.json')):
        r = json.load(open(f))
        two[r['instance_id']] = r.get('weighted_nonproductive') if r.get('passed') else None
    for f in glob.glob(str(run/'stowlm/instances/*.json')):
        r = json.load(open(f))
        stow[r['instance_id']] = r.get('objective') if r.get('report_passed') and not r.get('repaired') else None
    return two, stow


def table(name, ids, frontier, other):
    mine = {i: frontier[i].get('weighted') if frontier[i].get('passed') else None for i in ids}
    mutual = [i for i in ids if mine[i] is not None and other.get(i) is not None]
    if not mutual:
        print(name, 'no mutual maps'); return
    a = sum(mine[i] for i in mutual)/len(mutual); b = sum(other[i] for i in mutual)/len(mutual)
    w = sum(mine[i] < other[i] for i in mutual); l = sum(mine[i] > other[i] for i in mutual)
    fp = sum(mine[i] is not None for i in ids); op = sum(other.get(i) is not None for i in ids)
    print(f'{name:28s} passes {fp}/{len(ids)} vs {op}/{len(ids)} | {len(mutual)} mutual: {a:.2f} vs {b:.2f} ({a/b-1:+.1%}) | fewer {w} more {l} equal {len(mutual)-w-l} p={sign(w, l):.2g}')


def main():
    run, which = sys.argv[1], sys.argv[2]
    f = rows(run)
    two, model = stored_val() if which == 'val' else stored_test()
    for rung in ('small', 'tiny'):
        ids = sorted(i for i in f if f'.{rung}.' in i)
        if not ids:
            continue
        print(f'--- {rung}: {len(ids)} maps done')
        table('frontier vs two-step', ids, f, two)
        table('frontier vs StowLM 8.2', ids, f, model)
        starts = [max(f[i].get('runs') or [{'best': 0, 'start': 'none'}], key=lambda r: -r['best'])['start'] for i in ids]
        from collections import Counter
        print('   best start', Counter(starts).most_common(), 'mean wall', round(sum(f[i].get('wall_s', 0) for i in ids)/len(ids)), 's', 'errors', sum('error' in f[i] for i in ids))


if __name__ == '__main__':
    main()
