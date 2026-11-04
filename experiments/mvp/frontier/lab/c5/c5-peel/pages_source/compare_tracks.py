"""The benchmark against the other tracks on the 55 small validation maps, matched by input file.

Reads the frontier run and the rows other tracks stored; runs nothing. Block-rule rows are read as they stand, so its count
grows until its run ends. Validation is development data for the frontier planner: use this to place the tracks, not to claim.
"""
import glob
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tables import interval

ROOT = Path(__file__).resolve().parents[3]
STRATEGY = ROOT/'experiments/mvp/strategy'


def frontier(run):
    out = {}
    for path in glob.glob(f'{run}/*.json'):
        if not path.endswith('AUDIT.json'):
            row = json.load(open(path))
            out[row['input']] = row.get('weighted') if row.get('passed') else None
    return out


def k1b():
    rows = [json.load(open(p)) for p in glob.glob(str(STRATEGY/'k1b/judged/model/*.json'))]
    return {r['input']: r['moves'] if r['outcome'] == 'pass' else None for r in rows}


def blockrule():
    out = {}
    for path in glob.glob(str(STRATEGY/'blockrule/runs/small-val/maps/*/summary.json')):
        row = json.load(open(path))
        if row.get('complete'):
            chosen = row['selected']['compact']['planner']
            out[row['job']['input']] = chosen['moves'] if chosen.get('passed') else None
    return out


def main():
    mine = frontier(sys.argv[1] if len(sys.argv) > 1 else ROOT/'experiments/mvp/frontier/runs/val-0.9')
    for name, other in (('K1b (8.2, 16 compacted candidates)', k1b()), ('block-rule, compacted', blockrule())):
        maps = [m for m in other if m in mine]
        both = [(mine[m], other[m]) for m in maps if mine[m] is not None and other[m] is not None]
        a, b = (sum(x)/len(both) for x in zip(*both))
        low, high = interval(both)
        print(f'{name}: {len(maps)} maps | passes frontier {sum(mine[m] is not None for m in maps)}, other {sum(other[m] is not None for m in maps)} | '
              f'{len(both)} mutual {a:.2f} vs {b:.2f} ({a/b-1:+.1%}, 95% {low:+.1%} to {high:+.1%}) | '
              f'fewer {sum(x < y for x, y in both)} more {sum(x > y for x, y in both)} equal {sum(x == y for x, y in both)}')
        print('   other passes, frontier does not:', [Path(m).stem for m in maps if mine[m] is None and other[m] is not None])
        print('   frontier passes, other does not:', [Path(m).stem for m in maps if mine[m] is not None and other[m] is None])


if __name__ == '__main__':
    main()
