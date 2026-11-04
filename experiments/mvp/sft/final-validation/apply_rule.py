"""D152: apply the pre-registered final-adapter rule and write experiments/mvp/sft/FINAL-CHOICE.md. Reads saved rows; no GPU.

Rule (D152, fixed before any 8.3 result): 8.3 replaces 8.2 iff, on audit30 + reserve25 together, 8.3 passes >= 8.2's passes - 1
AND its mean weighted moves on mutual maps are >= 5 % lower; otherwise 8.2 stays. Reserve25 is reported separately, tiny val48
is a diagnostic. Refuses to decide if a set is incomplete, a verification was unavailable, epochs differ or securing failed.
Sign tests: exact two-sided binomial on discordant maps, ties dropped, as in val-audit30-twostep/paired.py.
Usage: apply_rule.py [--stand-in] [--out PATH]   (--stand-in reads 8.2's rows as 8.3 to test the pipeline; it never writes the real file)
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import glob
import hashlib
import json
from math import comb
from pathlib import Path
import statistics as st
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
EXPECTED = {'audit30': 30, 'reserve25': 25, 'tiny48': 48}
BASELINE_AUDIT30 = ROOT/'experiments/mvp/exit/deployment-d137/audit-baseline/summary.json'
TWOSTEP_AUDIT30 = ROOT/'experiments/mvp/sft/val-audit30-twostep/120'


def sign_p(a, b):
    n = a+b
    return min(1.0, 2*sum(comb(n, k) for k in range(min(a, b)+1))/2**n) if n else 1.0


def adapter_rows(path):
    return json.loads(Path(path).read_text())


def outcomes(rows):
    return {r['instance_id']: (r['weighted_moves'] if r.get('passed') is True else None) for r in rows}


def twostep(folder):
    rows = [json.loads(Path(p).read_text()) for p in sorted(glob.glob(str(Path(folder)/'*.json')))]
    return {r['instance_id']: (r.get('weighted_nonproductive') if r.get('passed') is True else None) for r in rows}


def failure_kind(row):
    if row.get('passed') is True:
        return ''
    verification = row.get('verification') or {}
    if row.get('unavailable') or verification.get('unavailable'):
        return 'unavailable'
    if verification.get('finisher_timeout'):
        return 'finisher time limit'
    return 'dead end' if 'no complete own greedy plan' in str(verification.get('error')) else 'failed'


def pair(left, right):
    keys = sorted(left)
    only_left = sum(left[m] is not None and right[m] is None for m in keys)
    only_right = sum(right[m] is not None and left[m] is None for m in keys)
    shared = [m for m in keys if left[m] is not None and right[m] is not None]
    fewer_left = sum(left[m] < right[m] for m in shared)
    fewer_right = sum(left[m] > right[m] for m in shared)
    return dict(passes_left=sum(left[m] is not None for m in keys), passes_right=sum(right[m] is not None for m in keys),
                only_left=only_left, only_right=only_right, passes_p=sign_p(only_left, only_right), mutual=len(shared),
                mean_left=st.mean(left[m] for m in shared) if shared else None,
                mean_right=st.mean(right[m] for m in shared) if shared else None,
                fewer_left=fewer_left, fewer_right=fewer_right, equal=len(shared)-fewer_left-fewer_right,
                moves_p=sign_p(fewer_left, fewer_right))


def decide(new, old):
    """The D152 rule on two {map: moves or None} dicts over the same maps. Exact arithmetic at the 5 % boundary."""
    if set(new) != set(old):
        raise ValueError('rule needs the same maps for both adapters')
    passes_new, passes_old = sum(v is not None for v in new.values()), sum(v is not None for v in old.values())
    shared = [m for m in new if new[m] is not None and old[m] is not None]
    if not shared:
        return dict(replace=False, passes_new=passes_new, passes_old=passes_old, passes_ok=passes_new >= passes_old-1, mutual=0,
                    mean_new=None, mean_old=None, relative_gain=None, moves_ok=False)
    total_new = sum(Fraction(str(new[m])) for m in shared)
    total_old = sum(Fraction(str(old[m])) for m in shared)
    passes_ok = passes_new >= passes_old-1
    moves_ok = total_old > 0 and (total_old-total_new)/total_old >= Fraction(5, 100)
    return dict(replace=passes_ok and moves_ok, passes_new=passes_new, passes_old=passes_old, passes_ok=passes_ok, mutual=len(shared),
                mean_new=float(total_new/len(shared)), mean_old=float(total_old/len(shared)),
                relative_gain=float((total_old-total_new)/total_old) if total_old else None, moves_ok=moves_ok)


def d158(chosen, two):
    """D158 outcome test, validation half only, so the label is provisional: 9.1 can only keep or lower it."""
    keys = sorted(chosen)
    passes_c, passes_t = sum(chosen[m] is not None for m in keys), sum(two.get(m) is not None for m in keys)
    shared = [m for m in keys if chosen[m] is not None and two.get(m) is not None]
    if not shared:
        return dict(label='Worse', delta=None, passes_chosen=passes_c, passes_two_step=passes_t, mutual=0, mean_chosen=None, mean_two_step=None)
    total_c = sum(Fraction(str(chosen[m])) for m in shared)
    total_t = sum(Fraction(str(two[m])) for m in shared)
    delta = total_c/total_t-1
    if delta <= Fraction(-5, 100) and passes_c >= passes_t-1:
        label = 'Better'
    elif abs(delta) < Fraction(5, 100) and passes_c >= passes_t-2:
        label = 'Par'
    else:
        label = 'Worse'
    return dict(label=label, delta=float(delta), passes_chosen=passes_c, passes_two_step=passes_t, mutual=len(shared),
                mean_chosen=float(total_c/len(shared)), mean_two_step=float(total_t/len(shared)))


def three_way(new, old, two):
    shared = [m for m in sorted(old) if new.get(m) is not None and old[m] is not None and two.get(m) is not None]
    return (len(shared), *(st.mean(arm[m] for m in shared) if shared else None for arm in (new, old, two)))


def refusals(name, rows, expected, epoch):
    problems = []
    if len(rows) != expected or len({r['instance_id'] for r in rows}) != expected:
        problems.append(f'{name}: {len(rows)} rows, expected {expected}')
    for row in rows:
        if failure_kind(row) == 'unavailable':
            problems.append(f"{name}: verification unavailable on {row['instance_id']}")
        securing = (row.get('generation_timings') or {}).get('securing') or {}
        if securing.get('bounds_available') is not True or securing.get('unavailable') or securing.get('fail_open'):
            problems.append(f"{name}: securing not active on {row['instance_id']}")
    epochs = {r.get('audit_outcome_epoch_id') for r in rows}
    if epochs != {epoch}:
        problems.append(f'{name}: outcome epochs {sorted(map(str, epochs))}, expected {epoch}')
    return problems


def fmt(value):
    return '—' if value is None else f'{value:g}'


def fp(value):
    return f'{value:.4g}'


def pair_line(name, left_name, right_name, p):
    means = f"{p['mean_left']:.2f} vs {p['mean_right']:.2f}" if p['mutual'] else '—'
    return (f"| {name} | {left_name} vs {right_name} | {p['passes_left']} vs {p['passes_right']} | {p['only_left']} / {p['only_right']} "
            f"({fp(p['passes_p'])}) | {p['mutual']} | {means} | {p['fewer_left']} / {p['fewer_right']} / {p['equal']} ({fp(p['moves_p'])}) |")


def map_table(new, old, two, kinds):
    lines = ['| Map | 8.3 | 8.2 | Two-step | 8.3 not passed |', '|---|---:|---:|---:|---|']
    for key in sorted(old):
        lines.append(f"| `{key}` | {fmt(new.get(key))} | {fmt(old[key])} | {fmt(two.get(key))} | {kinds.get(key, '')} |")
    return lines


def sets_for(stand_in):
    label = '8.2' if stand_in else '8.3'
    new = {name: adapter_rows(HERE/label/name/'rows.json') for name in EXPECTED if not (stand_in and name == 'audit30')}
    old = {'audit30': adapter_rows(BASELINE_AUDIT30), 'reserve25': adapter_rows(HERE/'8.2/reserve25/rows.json'),
           'tiny48': adapter_rows(HERE/'8.2/tiny48/rows.json')}
    if stand_in:
        new['audit30'] = adapter_rows(BASELINE_AUDIT30)
    two = {'audit30': twostep(TWOSTEP_AUDIT30), 'reserve25': twostep(HERE/'twostep/reserve25'), 'tiny48': twostep(HERE/'twostep/tiny48')}
    return new, old, two


def build(stand_in):
    new_rows, old_rows, two = sets_for(stand_in)
    epoch = old_rows['reserve25'][0].get('audit_outcome_epoch_id')
    problems = []
    for name, expected in EXPECTED.items():
        problems += refusals(f'8.3 {name}', new_rows[name], expected, epoch)
        problems += refusals(f'8.2 {name}', old_rows[name], expected, epoch)
        if len(two[name]) != expected:
            problems.append(f'two-step {name}: {len(two[name])} maps, expected {expected}')
        if {r['instance_id'] for r in new_rows[name]} != {r['instance_id'] for r in old_rows[name]}:
            problems.append(f'{name}: 8.3 and 8.2 maps differ')
    new = {name: outcomes(rows) for name, rows in new_rows.items()}
    old = {name: outcomes(rows) for name, rows in old_rows.items()}
    combined_new = {**new['audit30'], **new['reserve25']}
    combined_old = {**old['audit30'], **old['reserve25']}
    if len(combined_new) != 55:
        problems.append(f'rule set has {len(combined_new)} maps, expected 55')
    common = set(combined_new) & set(combined_old)  # only differs when `problems` already refuses a decision
    verdict = decide({k: combined_new[k] for k in common}, {k: combined_old[k] for k in common})
    return new_rows, new, old, two, combined_new, combined_old, verdict, problems, epoch


def render(stand_in):
    new_rows, new, old, two, combined_new, combined_old, verdict, problems, epoch = build(stand_in)
    combined_two = {**two['audit30'], **two['reserve25']}
    decided = not problems
    choice = ('**NOT DECIDED**: the checks below failed' if not decided else
              '**8.3 replaces 8.2**' if verdict['replace'] else '**8.2 stays**')
    lines = ['# Final adapter choice (D152)', '']
    if stand_in:
        lines += ['**PIPELINE TEST ONLY: 8.2 rows stand in for 8.3. This is not a result.**', '']
    lines += [f'Outcome: {choice}.', '',
              'Rule, fixed in D152 before any 8.3 result: 8.3 replaces 8.2 iff, on audit30 + reserve25 together (55 small val maps, same',
              'recipe), 8.3 passes ≥ (8.2\'s passes − 1) **and** its mean weighted moves on mutual maps are ≥ 5 % lower. Otherwise 8.2 stays.', '']
    if verdict['mutual']:
        lines += ['| Condition | Value | Met |', '|---|---:|---|',
                  f"| Passes ≥ 8.2 − 1 | {verdict['passes_new']} vs {verdict['passes_old']}/55 | {'yes' if verdict['passes_ok'] else 'no'} |",
                  f"| Mutual moves ≥ 5 % lower | {verdict['mean_new']:.2f} vs {verdict['mean_old']:.2f} on {verdict['mutual']} maps ({100*verdict['relative_gain']:+.1f} % gain) | {'yes' if verdict['moves_ok'] else 'no'} |", '']
    if decided:
        label = '8.3' if verdict['replace'] else '8.2'
        outcome = d158(combined_new if verdict['replace'] else combined_old, combined_two)
        pair_two = pair(combined_new if verdict['replace'] else combined_old, {k: combined_two.get(k) for k in combined_new})
        delta = '—' if outcome['delta'] is None else f"{100*outcome['delta']:+.1f} %"
        lines += [f'## D158 outcome test on validation (provisional): **{outcome["label"]}**', '',
                  f'Final adapter {label} vs two-step v4.4 on the 55 small validation maps. Tiny never decides. Provisional because only',
                  'validation exists; 9.1 test small can only keep or lower this label.', '',
                  '| Measure | Value | Better needs | Par needs |', '|---|---:|---|---|',
                  f"| Δ moves on mutual maps | {delta} ({outcome['mean_chosen']:.2f} vs {outcome['mean_two_step']:.2f}, {outcome['mutual']} maps) | ≤ −5 % | |Δ| < 5 % |",
                  f"| Passes | {outcome['passes_chosen']} vs {outcome['passes_two_step']} | ≥ two-step − 1 | ≥ two-step − 2 |",
                  f"| Sign tests | passes {pair_two['only_left']} / {pair_two['only_right']} (p = {fp(pair_two['passes_p'])}); fewer moves {pair_two['fewer_left']} / {pair_two['fewer_right']} / {pair_two['equal']} equal (p = {fp(pair_two['moves_p'])}) | | |", '',
                  '| Set | Maps all three pass | 8.3 | 8.2 | Two-step |', '|---|---:|---:|---:|---:|']
        for name, a, b, c in (('audit30 + reserve25', combined_new, combined_old, combined_two), ('audit30', new['audit30'], old['audit30'], two['audit30']),
                              ('reserve25', new['reserve25'], old['reserve25'], two['reserve25']), ('tiny val48 (diagnostic)', new['tiny48'], old['tiny48'], two['tiny48'])):
            n, x, y, z = three_way(a, b, c)
            lines.append(f'| {name} | {n} | {x:.2f} | {y:.2f} | {z:.2f} |' if n else f'| {name} | 0 | — | — | — |')
        lines.append('')
    if problems:
        lines += ['**Checks failed; the rule was not applied:**', ''] + [f'- {p}' for p in problems] + ['']
    lines += ['**Reading:** validation only. One run per adapter and no repeat-run noise estimate, so any gain is "consistent with" the',
              'relabelling, not "caused by" it (D152). Audit30 already picked 8.2, so reserve25 is the fresher set. Tiny val48 isn\'t part of the rule.', '',
              f'Recipe: `run_adapter.py`, greedy, mask + securing + look-ahead, V4 finisher 120 s, full checker. Outcome epoch `{epoch}`.',
              'Two-step: live v4.4, 120 s. Sign tests: exact two-sided binomial on discordant maps, ties dropped, no multiple-test adjustment.', '',
              '## Summary', '',
              '| Set | Pair (A vs B) | Passes | Only A / only B (sign p) | Mutual | Mean A vs B | Fewer moves A / B / equal (sign p) |',
              '|---|---|---:|---:|---:|---:|---:|']
    for name, a, b, left, right in (
            ('audit30 + reserve25', '8.3', '8.2', combined_new, combined_old), ('audit30 + reserve25', '8.3', 'two-step', combined_new, combined_two),
            ('audit30 + reserve25', '8.2', 'two-step', combined_old, combined_two),
            ('audit30', '8.3', '8.2', new['audit30'], old['audit30']), ('audit30', '8.3', 'two-step', new['audit30'], two['audit30']),
            ('reserve25', '8.3', '8.2', new['reserve25'], old['reserve25']), ('reserve25', '8.3', 'two-step', new['reserve25'], two['reserve25']),
            ('tiny val48 (diagnostic)', '8.3', '8.2', new['tiny48'], old['tiny48']), ('tiny val48 (diagnostic)', '8.3', 'two-step', new['tiny48'], two['tiny48']),
            ('tiny val48 (diagnostic)', '8.2', 'two-step', old['tiny48'], two['tiny48'])):
        lines.append(pair_line(name, a, b, pair(left, {k: right.get(k) for k in left})))
    lines.append('')
    for name, title in (('audit30', 'audit30'), ('reserve25', 'reserve25 (reported separately)'), ('tiny48', 'tiny val48 (diagnostic, not in the rule)')):
        kinds = {r['instance_id']: failure_kind(r) for r in new_rows[name]}
        lines += [f'## Per map: {title}', ''] + map_table(new[name], old[name], two[name], kinds) + ['']
    sources = [BASELINE_AUDIT30, HERE/'8.2/reserve25/rows.json', HERE/'8.2/tiny48/rows.json'] + ([] if stand_in else [HERE/'8.3'/n/'rows.json' for n in EXPECTED])
    lines += ['## Sources', ''] + [f'- `{p.relative_to(ROOT)}` sha256 `{hashlib.sha256(p.read_bytes()).hexdigest()[:16]}…`' for p in sources]
    return '\n'.join(lines)+'\n', decided, verdict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stand-in', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.stand_in and args.out is None:
        raise SystemExit('--stand-in needs --out: it never writes the real FINAL-CHOICE.md')
    target = args.out or ROOT/'experiments/mvp/sft/FINAL-CHOICE.md'
    missing = [] if args.stand_in else [str(HERE/'8.3'/n/'rows.json') for n in EXPECTED if not (HERE/'8.3'/n/'rows.json').exists()]
    if missing:
        raise SystemExit('8.3 rows not ready: '+', '.join(missing))
    text, decided, verdict = render(args.stand_in)
    target.write_text(text)
    print(json.dumps(dict(path=str(target), decided=decided, **verdict)))
    sys.exit(0 if decided else 2)


if __name__ == '__main__':
    main()
