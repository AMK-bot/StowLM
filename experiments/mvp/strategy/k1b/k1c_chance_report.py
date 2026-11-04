"""D196 report: K1c's recipe under chance. Numbers only."""
from __future__ import annotations

import json
from pathlib import Path
import statistics as st

import k1b_report as r
import k1c_report as c
import pair_blockrule as b

HERE = Path(__file__).resolve().parent
ARMS = {'8.2': HERE/'k1c', 'random': HERE/'k1c-random', 'null': HERE/'k1c-null'}
UNCONSTRAINED = {'8.2': HERE/'judged/model', 'random': HERE/'k1r/judged/model', 'null': HERE/'null/judged/model'}
LABEL = {'8.2': '8.2', 'random': 'uniform random', 'null': 'untrained Glimmer'}


def rows(folder):
    return {x['instance_id']: x for x in map(json.loads, (p.read_text() for p in Path(folder).glob('*.json')))}


def moves(arm):
    return {i: x['moves'] for i, x in arm.items()}


def ready_arms():
    return [arm for arm, folder in ARMS.items() if len(list((folder/'judged/model').glob('*.json'))) == 55]


def reading(p):
    """D196: within 5 % of K1c on moves with equal passes means the gain isn't the model's."""
    return p['delta'] is not None and abs(p['delta']) <= .05+1e-9 and p['mine'] == p['rival']


def read_out(p):
    if reading(p):
        return "**Within 5 % with equal passes: K1c's gain isn't the model's.**"
    if p['delta'] is None or p['mine'] != p['rival']:
        return f"**The condition is not met: passes {p['mine']} vs {p['rival']}.**"
    side = 'fewer moves than K1c' if p['delta'] < 0 else 'more moves than K1c'
    return f"**The condition is not met as written: outside ±5 %, with {side} (sign p = {p['moves_p']:.3f}).**"


def greedy_rows(arm):
    return rows(HERE/'k1c-greedy'/arm)


def write():
    trained = rows(UNCONSTRAINED['8.2'])
    table = b.blockrule_rows(b.SOURCE.read_text())
    rule = {i: table[b.map_name(x['input'])] for i, x in trained.items()}
    arms = ready_arms()
    masked = {a: moves(rows(ARMS[a]/'judged/model')) for a in arms}
    selected = {a: c.with_fallback(masked[a], moves(rows(UNCONSTRAINED[a]))) for a in arms}
    lines = ['# K1c against chance (D196)', '',
             "55 small validation maps; no test or showcase input; no training. K1c's exact recipe, with the port rule in the mask: same seeds, compaction, judge (600 s finisher, full checker, mask replay) and fallback. "
             "Each arm's fallback is its own unconstrained run: K1b for 8.2, K1r for random, the null arm for untrained Glimmer. Random picks uniformly among legal actions, and its first sample is random too. "
             "Exact two-sided sign tests, ties dropped, no adjustment.", '', *r.HEAD]
    for a in arms:
        lines.append(r.row_of(f'K1c {LABEL[a]} vs block-rule', r.pair(selected[a], rule)))
        if a != '8.2':
            lines.append(r.row_of(f'K1c {LABEL[a]} vs K1c 8.2', r.pair(selected[a], selected['8.2'])))
    for a in arms:
        lines.append(r.row_of(f'Rule-masked alone, {LABEL[a]}, vs block-rule', r.pair(masked[a], rule)))
        if a != '8.2':
            lines.append(r.row_of(f'Rule-masked alone, {LABEL[a]}, vs 8.2', r.pair(masked[a], masked['8.2'])))
    lines += ['', '| Arm | Outcomes, rule-masked alone | Complete of 16 | Fallback maps | Winner greedy / sample | Winner compacted |', '|---|---|---:|---:|---:|---:|']
    for a in arms:
        arm = rows(ARMS[a]/'judged/model')
        passed = [x for x in arm.values() if x['moves'] is not None]
        tally = {k: sum(x['outcome'] == k for x in arm.values()) for k in sorted({x['outcome'] for x in arm.values()})}
        plain = moves(rows(UNCONSTRAINED[a]))
        lines.append(f"| {LABEL[a]} | {tally} | {sum(x['n_complete'] for x in arm.values())/55:.1f} | {sum(selected[a][i] == plain[i] and selected[a][i] != masked[a][i] for i in rule)} | "
                     f"{sum(x['winner_sample'] == 0 for x in passed)} / {sum(x['winner_sample'] != 0 for x in passed)} | {sum(x['winner_compacted'] is True for x in passed)} |")
    lines += ['', '## Greedy alone inside the rule', '',
              "The first sample only: greedy for 8.2 and untrained Glimmer, one uniform draw for random. Compacted, then the same judge. Maps with no complete first sample count as no pass. No fallback.", '',
              '| Arm | Complete first samples | Passed | Mean on own passes |', '|---|---:|---:|---:|']
    firsts = {}
    for a in arms:
        arm = greedy_rows(a)
        if len(arm) != 55:
            continue
        firsts[a] = {i: x['moves'] for i, x in arm.items()}
        own = [v for v in firsts[a].values() if v is not None]
        lines.append(f"| {LABEL[a]} | {sum(x['complete'] for x in arm.values())} | {len(own)} | {st.mean(own) if own else float('nan'):.2f} |")
    lines += ['', *r.HEAD]
    for a in firsts:
        lines.append(r.row_of(f'{LABEL[a]} first sample vs block-rule', r.pair(firsts[a], rule)))
    for a in firsts:
        if a != '8.2' and '8.2' in firsts:
            lines.append(r.row_of(f'{LABEL[a]} first sample vs 8.2 greedy', r.pair(firsts[a], firsts['8.2'])))
    lines += ['', '## Pre-registered reading (D196)', '']
    if 'random' in arms:
        p = r.pair(selected['random'], selected['8.2'])
        lines.append(f"Random vs K1c: moves {'—' if p['delta'] is None else format(p['delta']*100, '+.1f')+' %'} on {p['mutual']} mutual maps, passes {p['mine']} vs {p['rival']}. {read_out(p)}")
    if 'null' not in arms:
        lines.append('Untrained Glimmer had not landed when this was written.')
    lines.append('')
    (HERE/'K1C-CHANCE.md').write_text('\n'.join(lines))
