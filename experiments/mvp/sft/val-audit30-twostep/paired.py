"""Paired table 8.1 / 8.2 / two-step on the 30 val audit maps; writes paired-<budget>.json and prints the markdown.
Sign tests are exact two-sided binomial on discordant maps, ties dropped (as in the D147 correction).
Usage: uv run python experiments/mvp/sft/val-audit30-twostep/paired.py <finalize_s>
"""
from __future__ import annotations

from itertools import combinations
import json
from math import comb
from pathlib import Path
import statistics as st
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
AUDITS = ROOT/'experiments/mvp/exit/deployment-d137'


def sign_p(a, b):
    n = a+b
    return min(1.0, 2*sum(comb(n, k) for k in range(min(a, b)+1))/2**n) if n else 1.0


def audit(label):
    rows = json.loads((AUDITS/f'parent-audit-{label}/audit-baseline/summary.json').read_text())
    return {r['instance_id']: r['weighted_moves'] if r['passed'] is True else None for r in rows}


def twostep(budget):
    rows = [json.loads(p.read_text()) for p in (HERE/budget).glob('*.json')]
    return ({r['instance_id']: r.get('weighted_nonproductive') if r['passed'] is True else None for r in rows},
            {r['instance_id']: r['outcome'] for r in rows})


def pair(left, right):
    only_l = sum(left[m] is not None and right[m] is None for m in left)
    only_r = sum(right[m] is not None and left[m] is None for m in left)
    shared = [m for m in left if left[m] is not None and right[m] is not None]
    fewer_l = sum(left[m] < right[m] for m in shared)
    fewer_r = sum(left[m] > right[m] for m in shared)
    return dict(passes_only_left=only_l, passes_only_right=only_r, passes_sign_p=sign_p(only_l, only_r), shared=len(shared),
                mean_left=st.mean(left[m] for m in shared) if shared else None,
                mean_right=st.mean(right[m] for m in shared) if shared else None,
                fewer_left=fewer_l, fewer_right=fewer_r, equal=len(shared)-fewer_l-fewer_r,
                moves_sign_p=sign_p(fewer_l, fewer_r))


def main():
    budget = sys.argv[1]
    two, outcomes = twostep(budget)
    arms = {'8.1': audit('8.1'), '8.2': audit('8.2'), 'two-step': two}
    if not all(set(arm) == set(arms['8.1']) and len(arm) == 30 for arm in arms.values()):
        raise SystemExit('arms do not cover the same 30 maps')
    allthree = [m for m in two if all(arm[m] is not None for arm in arms.values())]
    result = dict(finalize_s=budget, passes={k: sum(v is not None for v in a.values()) for k, a in arms.items()},
                  own_mean={k: st.mean(v for v in a.values() if v is not None) for k, a in arms.items()},
                  all_three_n=len(allthree), all_three_mean={k: st.mean(a[m] for m in allthree) for k, a in arms.items()},
                  twostep_outcomes={o: list(outcomes.values()).count(o) for o in set(outcomes.values())},
                  pairs={f'{l} vs {r}': pair(arms[l], arms[r]) for l, r in combinations(arms, 2)}, maps=arms)
    (HERE/f'paired-{budget}.json').write_text(json.dumps(result, indent=1, sort_keys=True)+'\n')
    print(f"| Arm | Passed / 30 | Mean on own passes | Mean on the {len(allthree)} maps all three pass |\n|---|---:|---:|---:|")
    for k in arms:
        print(f"| {k} | {result['passes'][k]} | {result['own_mean'][k]:.2f} | {result['all_three_mean'][k]:.2f} |")
    print('\n| Pair (A vs B) | Passes: only A / only B (sign p) | Mutual maps | Mean A vs B | Fewer moves A / B / equal (sign p) |\n|---|---:|---:|---:|---:|')
    for name, p in result['pairs'].items():
        print(f"| {name} | {p['passes_only_left']} / {p['passes_only_right']} ({p['passes_sign_p']:.3f}) | {p['shared']} | "
              f"{p['mean_left']:.2f} vs {p['mean_right']:.2f} | {p['fewer_left']} / {p['fewer_right']} / {p['equal']} ({p['moves_sign_p']:.3f}) |")
    print('\ntwo-step outcomes:', result['twostep_outcomes'])


if __name__ == '__main__':
    main()
