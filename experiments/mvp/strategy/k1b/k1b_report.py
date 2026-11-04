"""D183 reports: two-step at 600 s paired with compacted 8.2 in COMPACT.md, and K1B.md with the verdict. Numbers only."""
from __future__ import annotations

import json
from math import comb
from pathlib import Path
import statistics as st

HERE = Path(__file__).resolve().parent
STRATEGY = HERE.parent
N = 55


def sign_p(a, b):
    n = a+b
    return min(1., 2*sum(comb(n, k) for k in range(min(a, b)+1))/2**n) if n else 1.


def pair(mine, rival):
    if set(mine) != set(rival) or len(mine) != N:
        raise SystemExit(f'arms cover {len(mine)} and {len(rival)} maps, not the same {N}')
    shared = [m for m in rival if mine[m] is not None and rival[m] is not None]
    only_l = [m for m in rival if mine[m] is not None and rival[m] is None]
    only_r = [m for m in rival if mine[m] is None and rival[m] is not None]
    fewer, more = sum(mine[m] < rival[m] for m in shared), sum(mine[m] > rival[m] for m in shared)
    left = st.mean(mine[m] for m in shared) if shared else None
    right = st.mean(rival[m] for m in shared) if shared else None
    return dict(mine=sum(v is not None for v in mine.values()), rival=sum(v is not None for v in rival.values()), only_left=only_l, only_right=only_r,
                passes_p=sign_p(len(only_l), len(only_r)), mutual=len(shared), mean_left=left, mean_right=right, delta=left/right-1 if shared else None,
                fewer=fewer, equal=len(shared)-fewer-more, more=more, moves_p=sign_p(fewer, more))


def verdict(p):
    """D183: K1's thresholds. Moves within +10 % on mutual maps and passes at least the baseline's minus 2."""
    moves, passes = p['delta'] is not None and p['delta'] <= .10+1e-9, p['mine'] >= p['rival']-2
    return dict(moves_met=moves, passes_met=passes, k1b_passes=moves and passes, model_only_map=bool(p['only_left']),
                unparks=moves and passes and bool(p['only_left']))


def show_p(value):
    return '< 0.0001' if value < .0001 else f'{value:.4f}'


def row_of(name, p):
    moves = '—' if p['delta'] is None else f"{p['mean_left']:.2f} vs {p['mean_right']:.2f} | {p['delta']*100:+.1f} %"
    return (f"| {name} | {p['mine']}/{N} | {p['rival']}/{N} | {len(p['only_left'])} / {len(p['only_right'])} ({p['passes_p']:.3f}) | {p['mutual']} | {moves} | "
            f"{p['fewer']} / {p['equal']} / {p['more']} ({show_p(p['moves_p'])}) |")


HEAD = ('| Arm vs baseline | Arm passed | Baseline passed | Only arm / only baseline (p) | Mutual | Mean moves | Δ | Fewer / equal / more (p) |', '|---|---:|---:|---:|---:|---:|---:|---:|')


def twostep_600():
    rows = [json.loads(p.read_text()) for p in (STRATEGY/'twostep-600').glob('*.json')]
    return {r['instance_id']: r.get('weighted_nonproductive') if r.get('passed') else None for r in rows}, rows


def pair_compact():
    text = (STRATEGY/'COMPACT.md').read_text()
    if '## Validation: fairness run' in text:
        return
    items = [json.loads(p.read_text()) for p in (STRATEGY/'compact-val/rows').glob('*.json')]
    after = {r['instance_id']: r['final_moves'] if r['final_passed'] else None for r in items}
    before = {r['instance_id']: r['before_moves'] if r['before_passed'] else None for r in items}
    rival, rows = twostep_600()
    counts = {}
    for r in rows:
        counts[r['outcome']] = counts.get(r['outcome'], 0)+1
    lines = ['', '## Validation: fairness run, two-step at a 600 s finisher', '',
             f"Two-step v4.4 on the same 55 maps with the 600 s finisher compaction had (`twostep-600/`, CPU only): {counts}. Same exact sign tests as above. "
             'Two-step here is uncompacted, and the 8.2 plans still had a 120 s finisher before compaction.', '', *HEAD,
             row_of('8.2 greedy', pair(before, rival)), row_of('8.2 greedy, compacted', pair(after, rival)), '']
    with open(STRATEGY/'COMPACT.md', 'a') as stream:
        stream.write('\n'.join(lines))


def write_report(target, manifest):
    model = [json.loads(p.read_text()) for p in (HERE/'judged/model').glob('*.json')]
    baseline = [json.loads(p.read_text()) for p in (HERE/'judged/baseline').glob('*.json')]
    mine, rival = {r['instance_id']: r['moves'] for r in model}, {r['instance_id']: r['moves'] for r in baseline}
    p = pair(mine, rival)
    v = verdict(p)
    tally = lambda rows: {k: sum(r['outcome'] == k for r in rows) for k in sorted({r['outcome'] for r in rows})}
    passed = [r for r in model if r['moves'] is not None]
    lines = ['# K1b: compaction inside sampling (D183)', '',
             f"55 small validation maps; no test or showcase input. Source `{manifest['source_pins_sha256'][:16]}…`, checker `{manifest['checker_epoch'][:16]}…`. "
             'Model: 8.2, greedy + 15 samples at T = 0.7, ≤ 32 rewinds, D167 flags, all candidates stored and compacted, ranked by compacted cost, then a 600 s V4 finisher, full checker and mask replay, cheapest first, stopping at the first pass. '
             'Baseline: two-step v4.4 at a 600 s finisher, also compacted, better of the two checked plans. Exact two-sided sign tests, ties dropped, no adjustment.', '',
             *HEAD, row_of('8.2 K1b', p), '',
             f"**Outcomes.** Model: {tally(model)}. Baseline: {tally(baseline)}.",
             f"**Maps only the model passes ({len(p['only_left'])}):** {', '.join(p['only_left']) or 'none'}.",
             f"**Maps only the baseline passes ({len(p['only_right'])}):** {', '.join(p['only_right']) or 'none'}.",
             f"**Selection.** Winner was greedy on {sum(r['winner_sample'] == 0 for r in passed)} of {len(passed)} passing maps, a sample on {sum(r['winner_sample'] != 0 for r in passed)}; compacted on {sum(r['winner_compacted'] is True for r in passed)}; "
             f"first ranked candidate passed on {sum(len(r['trials']) == 1 for r in passed)}. Baseline compaction used on {sum(r['compact_used'] for r in baseline)} maps.", '',
             '## Verdict against D183', '',
             f"- Moves within +10 % on mutual maps: {'met' if v['moves_met'] else 'not met'} ({'—' if p['delta'] is None else format(p['delta']*100, '+.1f')+' %'}).",
             f"- Passes ≥ baseline − 2: {'met' if v['passes_met'] else 'not met'} ({p['mine']} vs {p['rival']}).",
             f"- **K1b: {'PASS' if v['k1b_passes'] else 'FAIL'}.**",
             f"- Model passes a map the baseline fails: {'yes' if v['model_only_map'] else 'no'}. Training unparks on this route: **{'yes' if v['unparks'] else 'no'}** (K2 is judged separately).", '']
    Path(target).write_text('\n'.join(lines))
