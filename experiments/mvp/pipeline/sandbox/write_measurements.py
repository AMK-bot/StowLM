"""Publish only complete, recountable measured cohorts into the README."""
from __future__ import annotations

import json
from pathlib import Path

from .benchmark import summary

HERE = Path(__file__).resolve().parent


def publish():
    cohorts = []
    for filename in ('benchmark190.json', 'benchmark354.json'):
        data = json.loads((HERE/'fixtures'/filename).read_text())
        rows = data['moves']
        if not data.get('complete') or len(rows) < 50 or not data['baseline_passed']:
            raise ValueError(f'{filename}: measurement is incomplete')
        stats = summary(rows)
        if stats != data['summary'] or not (stats['full_legal'] and stats['full_illegal']):
            raise ValueError(f'{filename}: summary or legal/illegal mix is invalid')
        cohorts.append((filename,data,stats))
    lines = [
        'Measured development results, from completed recorded runs. Seconds are wall time.', '',
        '| Boxes | Moves (full legal / illegal) | Fast median / p95 | Full median / p95 | Fast says legal, full disagrees |',
        '| --- | --- | --- | --- | --- |',
    ]
    for filename,data,s in cohorts:
        f, full = s['fast_response'],s['full_response']
        denominator = s['fast_legal']
        rate = f"{100*s['false_legal']/denominator:.1f}%" if denominator else 'undefined'
        lines.append(f"| [{data['n_boxes']}](fixtures/{filename}) | {s['moves']} ({s['full_legal']} / {s['full_illegal']}) "
                     f"| {f['median_s']:.3f} / {f['p95_s']:.3f} | {full['median_s']:.3f} / {full['p95_s']:.3f} "
                     f"| {s['false_legal']} of {denominator} fast-legal ({rate}); {s['false_legal']} of {s['moves']} all drags |")
    lines.extend(['', 'The false-legal denominator is the number of clear fast screens, not the number of all drags. '
                  'The small, deliberately selected clear subset is not an estimate of arbitrary-drag accuracy. '
                  'No fast screen is an approval.', ''])
    for filename,data,s in cohorts:
        end_load = [r['load_after']['load1'] for r in data['moves']]
        lines.append(f"- {data['n_boxes']} boxes: admission load1 {s['load1_min']:.2f}–{s['load1_max']:.2f}; "
                     f"end readings {min(end_load):.2f}–{max(end_load):.2f}; cold source-plan full check "
                     f"{data['baseline_check_s']:.3f} s. Cohort: {data['selected_fast_clear']} clear / "
                     f"{data['selected_fast_flagged']} flagged screens; {data['workers']} workers. "
                     f"[Recorded rows and source hashes](fixtures/{filename}).")
    lines.extend(['', 'Worker cold imports are included in response times. Fleet-admission waits and cohort selection '
                  'are excluded; they are experiment scheduling, not work performed by `try_move`. '
                  'p95 uses the nearest-rank definition. Timings exclude explanation rendering/network.'])
    path = HERE/'README.md'
    before, remainder = path.read_text().split('<!-- MEASUREMENTS -->',1)
    _, after = remainder.split('<!-- /MEASUREMENTS -->',1)
    path.write_text(before+'<!-- MEASUREMENTS -->\n'+'\n'.join(lines)+'\n<!-- /MEASUREMENTS -->'+after)
    return '\n'.join(lines)


if __name__ == '__main__':
    print(publish())
