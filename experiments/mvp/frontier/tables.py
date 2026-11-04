"""Tables for RESULTS.md: passes, fallback count, paired means with bootstrap intervals, by ship size.

Usage: tables.py <run_dir> <val|test|unseen> [--also <dir> ...]. --also adds rows from a longer-finisher pass: a map's result
changes only if the extra row passes and is cheaper, and the table says so. The in-run two-step had the same finisher budget and
the same machine load as the frontier plans; stored rows had 120 s.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import rows, sign, stored_test, stored_val


def origin(row):
    """Where a passing plan came from, by content: a start's label, 'search', or 'construction' for a yard-first or master start
    that search didn't improve. A tie between runs reads as search, so its count is a ceiling. Rows don't store the plan's origin."""
    label = next(t.get('label') for t in row['tries'] if t.get('passed') and t.get('weighted') == row['weighted'])
    if label in ('twostep', 'blocked'):
        return label
    improved = any(r.get('best') == row['weighted'] and r['start_objective'] > r['best'] for r in row.get('runs', []))
    return 'search' if improved else 'construction'


def fallback(row):
    """True when the plan returned is a start finished as it stood, not one the search found."""
    return bool(row.get('passed')) and origin(row) != 'search'


def interval(pairs, draws=10000, seed=0):
    """Paired bootstrap over maps of the change in mean weighted lifts, as a share of the other arm's mean."""
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = [rng.choice(pairs) for _ in pairs]
        other = sum(b for _, b in sample)
        values.append(sum(a for a, _ in sample)/other-1 if other else 0.)
    values.sort()
    return values[int(.025*draws)], values[int(.975*draws)]


def line(name, ids, mine, other):
    mutual = [i for i in ids if mine.get(i) is not None and other.get(i) is not None]
    head = f'{name:34s} passes {sum(mine.get(i) is not None for i in ids)}/{len(ids)} vs {sum(other.get(i) is not None for i in ids)}/{len(ids)}'
    if not mutual:
        print(head, '| no mutual maps')
        return
    pairs = [(mine[i], other[i]) for i in mutual]
    a, b = (sum(x)/len(mutual) for x in zip(*pairs))
    fewer, more = sum(x < y for x, y in pairs), sum(x > y for x, y in pairs)
    low, high = interval(pairs)
    print(f'{head} | {len(mutual)} mutual {a:.2f} vs {b:.2f} ({a/b-1:+.1%}, 95% {low:+.1%} to {high:+.1%}) '
          f'| fewer {fewer} more {more} equal {len(mutual)-fewer-more} p={sign(fewer, more):.2g}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run')
    parser.add_argument('which', choices=('val', 'test', 'unseen'))
    parser.add_argument('--also', action='append', default=[])
    args = parser.parse_args()
    run = rows(args.run)
    mine = {i: r.get('weighted') if r.get('passed') else None for i, r in run.items()}
    changed = []
    for extra in args.also:
        for i, r in rows(extra).items():
            value = r.get('weighted') if r.get('passed') else None
            if value is not None and (mine.get(i) is None or value < mine[i]):
                changed.append((i, mine.get(i), value))
                mine[i] = value
    if changed:
        print('changed by the longer finisher:', changed)
    same_run = lambda key: {i: (r.get(key) or {}).get('weighted') if (r.get(key) or {}).get('passed') else None for i, r in run.items()}
    inrun, blocked = same_run('twostep_finish'), same_run('blocked_finish')
    stored_two, model = ({}, {}) if args.which == 'unseen' else stored_val() if args.which == 'val' else stored_test()
    size = {i: re.search(r'feeder_(\d+)', r['input']).group(1) for i, r in run.items()}
    for rung in ('tiny', 'small'):
        ids = sorted(i for i in run if f'.{rung}.' in i)
        if not ids:
            continue
        print(f'--- {rung}: {len(ids)} maps; plan was an unsearched start on {sum(fallback(run[i]) for i in ids)}; '
              f'errors {sum("error" in run[i] for i in ids)}; '
              f'wall mean {sum(run[i].get("wall_s", 0) for i in ids)/len(ids):.0f} s, max {max(run[i].get("wall_s", 0) for i in ids):.0f} s')
        line('vs two-step, same run and budget', ids, mine, inrun)
        if any(i in blocked and blocked[i] is not None for i in ids):
            line('vs block-rule, same run and budget', ids, mine, blocked)
        if stored_two:
            line('vs two-step, stored (120 s)', ids, mine, stored_two)
            line('vs StowLM, stored', ids, mine, model)
        groups = defaultdict(list)
        for i in ids:
            groups[size[i]].append(i)
        for ship in sorted(groups, key=int):
            line(f'  feeder_{ship}, vs same-run two-step', groups[ship], mine, inrun)


if __name__ == '__main__':
    main()
