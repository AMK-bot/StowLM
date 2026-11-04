"""Tables for VALIDATION.md. Usage: report_rcspp.py <results_dir> <run.jsonl> [more runs ...].

A plan's own faults are everything but OUTSIDE_THE_PLAN in check_rcspp. Lashing on stacks of arrival boxes is shown in its own column: the model has no
exception for it, and its value depends on the plan's GM. Only default runs belong here; --mixed and --lash-changed-only rows are refused.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_rcspp import OUTSIDE_THE_PLAN


def own(faults):
    return {k: v for k, v in faults.items() if k not in OUTSIDE_THE_PLAN}


def read(path):
    return {r['name']: r for r in map(json.loads, open(path))}


def interval(pairs, draws=10000, seed=0):
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = [rng.choice(pairs) for _ in pairs]
        values.append(sum(a for a, _ in sample)/sum(b for _, b in sample)-1)
    values.sort()
    return values[int(.025*draws)], values[int(.975*draws)]


def main():
    results = Path(sys.argv[1])
    replay = read(results/'replay.jsonl')
    runs = {}
    for path in sys.argv[2:]:
        runs.update(read(path))
    assert not any(r.get('lash_changed_only') or r.get('mixed_override') is not None for r in runs.values()), 'sensitivity runs do not belong in these tables'

    print('## Published plans through the checker\n')
    print(f'{len(replay)} plans. Objective residual max {max(abs(r["objective_flat"]-r["file_objective"]) for r in replay.values()):.3f}, '
          f'ballast residual max {max(abs(r["ballast"]-r["file_ballast"]) for r in replay.values()):.3f}, '
          f'restows recomputed nonzero on {sum(r["restows"] > 0 for r in replay.values())}, hatch restows {sum(r["hatch_restows"] for r in replay.values())}.\n')
    plans, counts = Counter(), Counter()
    for row in replay.values():
        for key, value in row['faults'].items():
            plans[key] += 1
            counts[key] += value
    print('| Rule | Plans with a breach | Breaches |\n|---|---:|---:|')
    for key in sorted(plans):
        print(f'| {key} | {plans[key]} | {counts[key]:,} |')

    print('\n## Planner against published plans\n')
    print('| Ship | Runs | No fault of its own | Also none on arrival-only stacks | Boxes, ours vs published | Change | Ballast t | Objective at 50 a box | Change (95 %) | At file points, ours vs published | More / equal / fewer boxes |')
    print('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    by_ship = defaultdict(list)
    for name, row in runs.items():
        by_ship[name.split('_')[0]].append(row)
    for ship in ('s', 'm', 'l', 'xl'):
        rows = by_ship.get(ship)
        if not rows:
            continue
        clean = [r for r in rows if not own(r['faults'])]
        boxes = sum(r['loaded'] for r in rows), sum(r['published_loaded'] for r in rows)
        water = sum(r['ballast'] for r in rows), sum(r['published_ballast'] for r in rows)
        value = sum(r['objective_flat'] for r in rows), sum(r['published_objective'] for r in rows)
        low, high = interval([(r['objective_flat'], r['published_objective']) for r in rows])
        more = sum(r['loaded'] > r['published_loaded'] for r in rows)
        same = sum(r['loaded'] == r['published_loaded'] for r in rows)
        strict = sum(not r['faults'].get('arrival-only stack lashing') for r in clean)
        points = sum(r['objective_file'] for r in rows), sum(replay[r['name']]['objective_file'] for r in rows)
        print(f'| {ship} | {len(rows)} | {len(clean)} | {strict} | {boxes[0]/len(rows):,.0f} vs {boxes[1]/len(rows):,.0f} | {boxes[0]/boxes[1]-1:+.1%} | '
              f'{water[0]/len(rows):,.0f} vs {water[1]/len(rows):,.0f} | {value[0]/len(rows):,.0f} vs {value[1]/len(rows):,.0f} | '
              f'{value[0]/value[1]-1:+.1%} ({low:+.1%} to {high:+.1%}) | {points[0]/len(rows):,.0f} vs {points[1]/len(rows):,.0f} | {more} / {same} / {len(rows)-more-same} |')

    print('\n## Per instance\n')
    print('| Instance | List | Ours | Published | Ballast ours | Published | Restows | Objective ours | Published | Wall s | Master gap | Own faults |')
    print('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|')
    for name in sorted(runs, key=lambda n: ({'s': 0, 'm': 1, 'l': 2, 'xl': 3}[n.split('_')[0]], n)):
        r = runs[name]
        master = r.get('master') or {}
        gap = f'{1-master["objective"]/master["bound"]:.1%}' if master.get('bound') else 'greedy'
        print(f'| {name} | {r["list"]:,} | {r["loaded"]:,} | {r["published_loaded"]:,} | {r["ballast"]:,.0f} | {r["published_ballast"]:,.0f} | {r["restows"]} | '
              f'{r["objective_flat"]:,.0f} | {r["published_objective"]:,.0f} | {r["wall_s"]:.0f} | {gap} | {own(r["faults"]) or ""} |')


if __name__ == '__main__':
    main()
