"""rev-k1c P3: the final K2-LADDER.md, rebuilt from the judged rows once both ladder arms are done, because the running drivers hold the older report code.
A wall-budget comparison: up to 16 samples within 3,600 s, so a slower arm buys fewer tries. Usage: ladder_report.py"""
from __future__ import annotations

import json
from pathlib import Path

import k2ladder

HERE = Path(__file__).resolve().parent


def rows(folder):
    return {x['map_id']: x for x in map(json.loads, (p.read_text() for p in Path(folder).glob('*.json')))}


def label(row):
    if row['moves'] is not None:
        return 'pass'
    if not row['n_complete']:
        return 'no complete plan'
    return 'finisher limit' if any(t.get('timed_out') for t in row['trials']) else 'fail'


def chance_clears(model, chance, sweep):
    """The D194 wins, and which of them chance also passes; a win counts against chance only if K1r fails that map."""
    wins = [m for m, r in model.items() if r['moves'] is not None and sweep[m] and not sweep[m]['default'] and not sweep[m]['rule']]
    return wins, [m for m in wins if chance[m]['moves'] is not None]


def write():
    model, chance = rows(HERE/'k2/judged'), rows(HERE/'k2r/judged')
    if set(model) != set(chance) or len(model) != 18:
        raise SystemExit(f'ladder arms cover {len(model)} and {len(chance)} maps')
    sweep = {m: k2ladder.sweep(m) for m in model}
    mark = lambda s, arm: 'pending' if s is None else ('pass' if s[arm] else 'fail')
    lines = ['# K2 on the fill ladder (D194, D195)', '',
             "**A wall-budget comparison of up to 16 samples, not of 16 tries.** Each arm gets 3,600 s per map, and the clock includes setup and is checked inside sampling. A sample cut by the limit isn't stored. A slower arm therefore buys fewer tries, so read the reached and complete counts beside every result. No fixed-k comparison is made here. "
             "18 train maps of the fill sweep: 40 % and 60 % requested fill, low mass, all three ships; read-only. Arms: 8.2, greedy + 15 samples at T = 0.7; K1r, 16 uniform random legal actions with no LLM. Both: 32 rewinds, D167 mask stack, candidates compacted, cheapest first through a 1,200 s V4 finisher (the sweep's medium budget, not the small maps' 600 s), full checker and mask replay, at most 4 tries per map (the small maps have no cap). "
             'Default and rule pass if the raw or compacted plan passed in `fillsweep/runs/main`.', '',
             '| Map | Ship | Fill | Boxes | 8.2 reached / complete | K1r reached / complete | 8.2 actions per box | K1r actions per box | 8.2 | Moves | K1r | Moves | Default | Rule |', '|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---:|---|---|']
    apb = lambda r: '—' if r['actions_per_box'] is None else f"{r['actions_per_box']:.2f}"
    value = lambda r: '—' if r['moves'] is None else f"{r['moves']:g}"
    for m in sorted(model, key=lambda x: (model[x]['fill'], model[x]['n_loads'])):
        a, c = model[m], chance[m]
        lines.append(f"| {m} | {a['ship']} | {a['fill']} | {a['n_loads']} | {a['samples_reached']}/16, {a['n_complete']} | {c['samples_reached']}/16, {c['n_complete']} | {apb(a)} | {apb(c)} | {label(a)} | {value(a)} | {label(c)} | {value(c)} | {mark(sweep[m], 'default')} | {mark(sweep[m], 'rule')} |")
    lines += ['', '| Ship | Fill | Maps | 8.2 passed | K1r passed | Default | Rule | Sweep pending | 8.2 reached / complete | K1r reached / complete |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for ship in ('feeder_500', 'feeder_800', 'feeder_1700'):
        for fill in (40, 60):
            keys = [m for m, r in model.items() if r['ship'] == ship and r['fill'] == fill]
            done = [m for m in keys if sweep[m]]
            count = lambda arm, field: sum(arm[m][field] for m in keys)
            lines.append(f"| {ship} | {fill} | {len(keys)} | {sum(model[m]['moves'] is not None for m in keys)} | {sum(chance[m]['moves'] is not None for m in keys)} | {sum(sweep[m]['default'] for m in done)} | {sum(sweep[m]['rule'] for m in done)} | {len(keys)-len(done)} | "
                         f"{count(model, 'samples_reached')} / {count(model, 'n_complete')} | {count(chance, 'samples_reached')} / {count(chance, 'n_complete')} |")
    wins, shared = chance_clears(model, chance, sweep)
    pending = [m for m, r in model.items() if r['moves'] is not None and sweep[m] is None]
    lines += ['', '## Verdict against D194', '', f"**K2-ladder: {'MET' if wins else 'not met'}.** 8.2 passes a map both default and rule fail: {', '.join(wins) or 'none'}."]
    if wins:
        lines.append(f"**Against chance:** K1r also passes {len(shared)} of those: {', '.join(shared) or 'none'}. Only the others count as more than chance.")
    if pending:
        lines.append(f"Sweep still pending on maps 8.2 passes, so this can change: {', '.join(pending)}.")
    lines.append('')
    (HERE/'K2-LADDER.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    write()
