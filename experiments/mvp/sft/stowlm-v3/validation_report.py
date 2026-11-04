"""D152 report: 8.2 / 8.3 / two-step map by map on audit30, reserve25 and tiny48, into VALIDATION.md and docs/evaluation.md.
Numbers only; logic-v4-decode applies the D152 choice rule. Sign tests are exact two-sided on discordant maps, ties dropped.
Usage: validation_report.py [--no-docs] [--stand-in-83 <label>]  (the stand-in only tests the report on other rows)
"""
from __future__ import annotations

import argparse
from itertools import combinations
import json
from math import comb
from pathlib import Path
import statistics as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FINAL = ROOT/'experiments/mvp/sft/final-validation'
AUDIT82 = ROOT/'experiments/mvp/exit/deployment-d137/parent-audit-8.2/audit-baseline/summary.json'
AUDIT_TWO = ROOT/'experiments/mvp/sft/val-audit30-twostep/120'
EVAL = ROOT/'docs/evaluation.md'
ANCHOR = '**8.1 SFT (30 Oct 00:14):**'
SETS = ('audit30', 'reserve25', 'tiny48')
SIZE = dict(audit30=30, reserve25=25, tiny48=48)


def sign_p(a, b):
    n = a+b
    return min(1., 2*sum(comb(n, k) for k in range(min(a, b)+1))/2**n) if n else 1.


def adapter_rows(path):
    return {r['instance_id']: r['weighted_moves'] if r['passed'] is True else None for r in json.loads(Path(path).read_text())}


def two_step_rows(folder):
    rows = [json.loads(p.read_text()) for p in Path(folder).glob('*.json')]
    return {r['instance_id']: r.get('weighted_nonproductive') if r['passed'] is True else None for r in rows}


def arms(name, stand_in):
    label = stand_in or '8.3'
    new = adapter_rows(FINAL/label/name/'rows.json')
    if name == 'audit30':
        return {'8.2': adapter_rows(AUDIT82), '8.3': new, 'two-step': two_step_rows(AUDIT_TWO)}
    return {'8.2': adapter_rows(FINAL/'8.2'/name/'rows.json'), '8.3': new, 'two-step': two_step_rows(FINAL/'twostep'/name)}


def pair(left, right):
    shared = [m for m in left if left[m] is not None and right[m] is not None]
    only_l = sum(left[m] is not None and right[m] is None for m in left)
    only_r = sum(right[m] is not None and left[m] is None for m in left)
    fewer_l, fewer_r = sum(left[m] < right[m] for m in shared), sum(left[m] > right[m] for m in shared)
    return dict(only_left=only_l, only_right=only_r, passes_p=sign_p(only_l, only_r), mutual=len(shared),
                mean_left=st.mean(left[m] for m in shared) if shared else None,
                mean_right=st.mean(right[m] for m in shared) if shared else None,
                fewer_left=fewer_l, fewer_right=fewer_r, equal=len(shared)-fewer_l-fewer_r, moves_p=sign_p(fewer_l, fewer_r))


def tables(name, data):
    common = [m for m in data['8.2'] if all(a[m] is not None for a in data.values())]
    lines = [f"**{name}** ({SIZE[name]} maps; {len(common)} passed by all three):", '',
             f'| Arm | Passed / {SIZE[name]} | Mean on own passes | Mean on the {len(common)} maps all three pass |', '|---|---:|---:|---:|']
    for arm, values in data.items():
        own = [v for v in values.values() if v is not None]
        lines.append(f"| {arm} | {len(own)} | {st.mean(own):.2f} | {st.mean(values[m] for m in common):.2f} |")
    lines += ['', '| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Fewer moves A / B / equal (sign p) |', '|---|---:|---:|---:|---:|']
    for left, right in combinations(data, 2):
        p = pair(data[left], data[right])
        lines.append(f"| {left} vs {right} | {p['only_left']} / {p['only_right']} ({p['passes_p']:.3f}) | {p['mutual']} | "
                     f"{p['mean_left']:.2f} vs {p['mean_right']:.2f} | {p['fewer_left']} / {p['fewer_right']} / {p['equal']} ({p['moves_p']:.3f}) |")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-docs', action='store_true')
    parser.add_argument('--stand-in-83')
    parser.add_argument('--out', type=Path, default=HERE/'VALIDATION.md')
    args = parser.parse_args()
    data = {name: arms(name, args.stand_in_83) for name in SETS if (FINAL/(args.stand_in_83 or '8.3')/name/'rows.json').exists()}
    for name, group in data.items():
        for arm, values in group.items():
            if len(values) != SIZE[name] or set(values) != set(group['8.2']):
                raise SystemExit(f'{name}: {arm} does not cover the same {SIZE[name]} maps')
    body = [line for name, group in data.items() for line in (*tables(name, group), '')]
    common = ['Validation only; no test or showcase input. Same audit recipe for 8.2 and 8.3 (greedy batched decode, mask + securing + look-ahead, '
              'V4 finisher, full checker, layered mask replay); two-step through `ablation/twostep_val.py`. Finisher 120 s, except the 8.2 audit30 row: '
              'its 90 s parent audit equals its 120 s loop audit on all 30 maps. Exact two-sided sign tests on discordant maps, ties dropped, no '
              'multiple-test adjustment. One training run per adapter, so a difference is "consistent with" the relabelling, not caused by it. '
              'The D152 rule is applied by logic-v4-decode, not here.']
    per_map = ['## Per-map weighted moves (— = not passed)', '']
    for name, group in data.items():
        per_map += [f'### {name}', '', '| Map | 8.2 | 8.3 | two-step |', '|---|---:|---:|---:|']
        per_map += [f"| {m} | "+' | '.join('—' if g[m] is None else f'{g[m]:g}' for g in group.values())+' |' for m in sorted(group['8.2'])]
        per_map.append('')
    args.out.write_text('\n'.join(['# 8.3 validation (D151/D152)', '', *common, '', *body, *per_map]))
    if not args.no_docs:
        text = EVAL.read_text()
        if '8.2 / 8.3 / two-step on' in text:
            raise SystemExit('docs/evaluation.md already has this section')
        section = ['**8.2 / 8.3 / two-step on the final validation sets (D152; validation only, not 9.1; `experiments/mvp/sft/stowlm-v3/VALIDATION.md`):**',
                   *common, '', *body]
        EVAL.write_text(text.replace(ANCHOR, '\n'.join(section)+'\n'+ANCHOR, 1))


if __name__ == '__main__':
    main()
