"""D193 reports: K1R.md (K1r against null, 8.2 K1b, block-rule, and K1c when it has landed) and KCURVE.md. Numbers only."""
from __future__ import annotations

import json
from pathlib import Path
import statistics as st

import k1b_report as r
import kcurve
import pair_blockrule as b

HERE = Path(__file__).resolve().parent


def rows(folder):
    return {x['instance_id']: x for x in map(json.loads, (p.read_text() for p in Path(folder).glob('*.json')))}


def moves(arm):
    return {i: x['moves'] for i, x in arm.items()}


def reading(p):
    """The ±5 % band with equal passes (D196, D198): no measurable advantage here, not proof of equivalence."""
    return p['delta'] is not None and abs(p['delta']) <= .05+1e-9 and p['mine'] == p['rival']


def outside(p):
    """The reading's band is two-sided, so say which side a miss is on."""
    if p['delta'] is None or p['mine'] != p['rival']:
        return '**The condition is not met.**'
    side = 'fewer moves than null, so random is the better side' if p['delta'] < 0 else 'more moves than null, so null is the better side'
    return f"**The condition is not met as written: the gap is outside ±5 %, with {side} (sign p = {p['moves_p']:.3f}).**"


def write_k1r():
    k1r, null, trained = rows(HERE/'k1r/judged/model'), rows(HERE/'null/judged/model'), rows(HERE/'judged/model')
    table = b.blockrule_rows(b.SOURCE.read_text())
    rule = {i: table[b.map_name(x['input'])] for i, x in trained.items()}
    versus_null = r.pair(moves(k1r), moves(null))
    lines = ['# K1r: uniform random legal actions, no LLM (D193)', '',
             "55 small validation maps; no test or showcase input; CPU only. At each step one action uniformly among those the K1b mask stack allows (production + look-ahead + securing, D79), with K1b's rewind limits, "
             "16 samples, sample index as seed, then K1b's compaction and judge (600 s finisher, full checker, mask replay). Every K1r sample is random; K1b's sample 0 is greedy. Exact two-sided sign tests, ties dropped, no adjustment.", '',
             *r.HEAD, r.row_of('K1r vs null', versus_null), r.row_of('K1r vs 8.2 K1b', r.pair(moves(k1r), moves(trained))), r.row_of('K1r vs block-rule', r.pair(moves(k1r), rule))]
    k1c = HERE/'k1c/K1C.DONE'
    if k1c.exists():
        masked = rows(HERE/'k1c/judged/model')
        lines += [r.row_of('K1r vs rule-masked 8.2 (K1c arm)', r.pair(moves(k1r), moves(masked)))]
    tally = {k: sum(x['outcome'] == k for x in k1r.values()) for k in sorted({x['outcome'] for x in k1r.values()})}
    passed = [x for x in k1r.values() if x['moves'] is not None]
    lines += ['', f'**Outcomes.** K1r: {tally}. Complete candidates per map, of 16: K1r {sum(x["n_complete"] for x in k1r.values())/55:.1f}, null {sum(x["n_complete"] for x in null.values())/55:.1f}, 8.2 {sum(x["n_complete"] for x in trained.values())/55:.1f}.',
              f"**Selection.** Compacted on {sum(x['winner_compacted'] is True for x in passed)} of {len(passed)}; first ranked candidate passed on {sum(len(x['trials']) == 1 for x in passed)}.", '',
              '## Reading against the ±5 % band', '',
              'The band is the one in this report and in D196. D193 asks for the random control but sets no number, so the band was not pre-registered there (D198). A miss isn\'t a model win, and a result inside it is "no measurable advantage here", not proof of equivalence.', '',
              f"K1r vs null: moves {'—' if versus_null['delta'] is None else format(versus_null['delta']*100, '+.1f')+' %'} on {versus_null['mutual']} mutual maps, passes {versus_null['mine']} vs {versus_null['rival']}. "
              + ("**Within 5 % with equal passes: no measurable advantage for Glimmer here.**" if reading(versus_null) else outside(versus_null)), '']
    if not k1c.exists():
        lines += ['K1c had not landed when this was written.', '']
    (HERE/'K1R.md').write_text('\n'.join(lines))


def curves():
    return {arm: {x['instance_id']: x['points'] for x in rows(HERE/'kcurve'/arm).values()} for arm in kcurve.ARMS}


def write_kcurve():
    data = curves()
    at = lambda arm, k: {i: p[str(k)]['moves'] for i, p in data[arm].items()}
    lines = ['# k-curve: does fine-tuning reach a given quality in fewer tries? (D193)', '',
             "The same 55 small validation maps. For each k, the first k stored candidates in sample order (8.2 and null: sample 0 is greedy; K1r: all random), ranked by compacted cost, walked to the first plan that passes the K1b judge. "
             'Pass-eligible: maps with at least one complete candidate among the k. Exact two-sided sign tests, ties dropped, no adjustment.', '',
             '| k | Arm | Pass-eligible | Passed | Mean on own passes |', '|---:|---|---:|---:|---:|']
    for k in kcurve.KS:
        for arm in kcurve.ARMS:
            own = [v for v in at(arm, k).values() if v is not None]
            eligible = sum(p[str(k)]['eligible'] for p in data[arm].values())
            lines.append(f"| {k} | {arm} | {eligible} | {len(own)} | {st.mean(own) if own else float('nan'):.2f} |")
    lines += ['', *[x.replace('Arm vs baseline', 'k: A vs B').replace('Arm passed', 'A passed').replace('Baseline passed', 'B passed').replace('Only arm / only baseline', 'Only A / only B') for x in r.HEAD]]
    for left, right in (('8.2', 'null'), ('8.2', 'K1r'), ('null', 'K1r')):
        lines += [r.row_of(f'{k}: {left} vs {right}', r.pair(at(left, k), at(right, k))) for k in kcurve.KS]
    lines += ['', f"**New verdicts needed:** {sum(x['fresh_verdicts'] for arm in kcurve.ARMS for x in rows(HERE/'kcurve'/arm).values())}. k = 16 repeats the judge's result for each arm.", '']
    (HERE/'KCURVE.md').write_text('\n'.join(lines))


def write_all():
    write_k1r()
    write_kcurve()


if __name__ == '__main__':
    write_all()
