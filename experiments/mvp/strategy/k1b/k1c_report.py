"""D190 K1c report: rule-masked 8.2 against block-rule on the same 55 maps, with the fallback and the portfolio. Numbers only."""
from __future__ import annotations

import json
from pathlib import Path

import k1b_report as r
import pair_blockrule as b

HERE = Path(__file__).resolve().parent


def with_fallback(masked, unconstrained):
    """Block-rule's selection: the rule plan replaces the unconstrained one only if it costs strictly less, or if that one failed."""
    result = {}
    for key, value in masked.items():
        other = unconstrained[key]
        result[key] = value if value is not None and (other is None or value < other) else other
    return result


def portfolio(*arms):
    """Per-map best checked cost of the arms; every value is a plan that passed."""
    return {key: min((arm[key] for arm in arms if arm[key] is not None), default=None) for key in arms[0]}


def verdict(p):
    """D190: at least 5 % fewer moves than block-rule on mutual maps, with passes at least block-rule's."""
    moves, passes = p['delta'] is not None and p['delta'] <= -.05+1e-9, p['mine'] >= p['rival']
    return dict(moves_met=moves, passes_met=passes, passed=moves and passes)


def rows(folder):
    return {x['instance_id']: x for x in map(json.loads, (p.read_text() for p in Path(folder).glob('*.json')))}


def write_report(target, manifest, audits):
    masked_rows, k1b_rows = rows(HERE/'k1c/judged/model'), rows(HERE/'judged/model')
    table = b.blockrule_rows(b.SOURCE.read_text())
    rule = {i: table[b.map_name(x['input'])] for i, x in k1b_rows.items()}
    masked, k1b = {i: x['moves'] for i, x in masked_rows.items()}, {i: x['moves'] for i, x in k1b_rows.items()}
    fallback = with_fallback(masked, k1b)
    best = portfolio(k1b, masked, rule)
    tested, alone = r.pair(fallback, rule), r.pair(masked, rule)
    v = verdict(tested)
    tally = {k: sum(x['outcome'] == k for x in masked_rows.values()) for k in sorted({x['outcome'] for x in masked_rows.values()})}
    passed = [x for x in masked_rows.values() if x['moves'] is not None]
    bad = {k: w for k, w in audits.items() if w}
    lines = ['# K1c: 8.2 with the port rule in the mask (D190)', '',
             f"55 small validation maps; no test or showcase input; no training. Source `{manifest['source_pins_sha256'][:16]}…`, checker `{manifest['checker_epoch'][:16]}…`. "
             "K1b's recipe with `--port-rule`, which removes every placement the D185 rule forbids, through the instruction layer under look-ahead and securing; same compaction and judge. "
             "**K1c** is block-rule's own selection: the rule-masked plan replaces K1b's unconstrained plan only if strictly cheaper, or if that one failed. The rule-masked arm is also shown alone. "
             "Block-rule is the 'Compact selected' column of `blockrule/BLOCKRULE.md`. Exact two-sided sign tests, ties dropped, no adjustment.", '',
             *r.HEAD, r.row_of('K1c vs block-rule', tested), r.row_of('Rule-masked alone vs block-rule', alone), r.row_of('Rule-masked alone vs K1b', r.pair(masked, k1b)),
             r.row_of('Portfolio (best of K1b, rule-masked, rule) vs block-rule', r.pair(best, rule)), '',
             f"**Outcomes, rule-masked alone.** {tally}. Complete candidates per map, of 16: {sum(x['n_complete'] for x in masked_rows.values())/55:.1f}, K1b {sum(x['n_complete'] for x in k1b_rows.values())/55:.1f}.",
             f"**Selection.** Fallback used on {sum(fallback[i] == k1b[i] and fallback[i] != masked[i] for i in fallback)} maps. Rule-masked winner greedy on {sum(x['winner_sample'] == 0 for x in passed)} of {len(passed)}; compacted on {sum(x['winner_compacted'] is True for x in passed)}.",
             f"**Rule audit of the rule-masked winners:** {len(audits)-len(bad)} of {len(audits)} clean" + (f"; violations on {sorted(bad)}." if bad else '.'),
             f"**Portfolio.** {sum(v2 is not None for v2 in best.values())}/55; the portfolio is chosen by checked cost per map, after the fact, so it's a validation number only.", '',
             '## Verdict against D190', '',
             f"- At least 5 % fewer moves than block-rule on mutual maps: {'met' if v['moves_met'] else 'not met'} ({'—' if tested['delta'] is None else format(tested['delta']*100, '+.1f')+' %'}).",
             f"- Passes ≥ block-rule's: {'met' if v['passes_met'] else 'not met'} ({tested['mine']} vs {tested['rival']}).",
             f"- **K1c: {'PASS' if v['passed'] else 'FAIL'}.**", '']
    Path(target).write_text('\n'.join(lines))
