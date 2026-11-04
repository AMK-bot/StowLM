"""Summarize the full-checker rows; never count unfinished witnesses as passes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
PARTS = ('hatch_now', 'hatch_later', 'relocations', 'restow_now', 'restow_later')


def report(directory):
    baseline = json.loads((LAB / 'baseline.json').read_text())['maps']
    rows = [json.loads(p.read_text()) for p in Path(directory).glob('*.json')
            if p.name != 'summary.json']
    rows = [r for r in rows if r.get('instance_id')]
    result = {}
    for size in ('tiny', 'small', 'medium'):
        group = [r for r in rows if r.get('size') == size]
        if not group:
            continue
        pairs = [(r, baseline[r['instance_id']]) for r in group
                 if r.get('passed') and baseline.get(r['instance_id'], {}).get('passed')]
        old = {k: sum(b['parts'][k] for _, b in pairs) for k in PARTS}
        new = {k: sum(r['parts'][k] for r, _ in pairs) for k in PARTS}
        old_total = sum(b['lifts'] for _, b in pairs)
        failures = {}
        for r in group:
            if not r.get('passed'):
                reason = r.get('failure_reason', 'unknown')
                failures[reason] = failures.get(reason, 0) + 1
        result[size] = dict(
            maps=len(group), passes=sum(bool(r.get('passed')) for r in group),
            paired=len(pairs), baseline_parts=old, candidate_parts=new,
            part_change={k: new[k] - old[k] for k in PARTS},
            baseline_hatch_share=((old['hatch_now'] + old['hatch_later']) / old_total
                                  if old_total else None),
            baseline_relocation_share=old['relocations'] / old_total if old_total else None,
            failures=failures,
            constructor_witnesses=sum('packing' in r.get('method_metadata', {}) for r in group),
            constructor_order_failures=sum(bool(r.get('method_metadata', {}).get('order_failures'))
                                           for r in group),
            legal_carrier_rows=sum(bool(r.get('tries')) for r in group)
                               if size == 'medium' else None,
            cut_solves=sum(r.get('method_metadata', {}).get('small_cuts', {}).get('cut', 0)
                           for r in group),
            enumerations=sum(r.get('method_metadata', {}).get('small_cuts', {}).get('enumeration', 0)
                             for r in group),
            replay_improvements=sum(r.get('method_metadata', {}).get('small_cuts', {}).get('improvements', 0)
                                    for r in group),
        )
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    args = parser.parse_args()
    print(json.dumps(report(args.directory), indent=2, sort_keys=True))
