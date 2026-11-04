"""Collect independently recorded per-vessel validation and timing artifacts."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    paths = sorted(ROOT.glob('validation_feeder_*.json'))
    if len(paths) != 3:
        raise RuntimeError('three production feeder validation files required')
    combined = dict(validation=[], timings=[])
    for path in paths:
        result = json.loads(path.read_text())
        combined['validation'].extend(result['validation'])
        combined['timings'].extend(result['timings'])
    (ROOT/'results.json').write_text(json.dumps(combined, indent=2)+'\n')
    lines = ['# Production KCS interpolation validation results', '',
        'Direct polygon free-trim equilibrium is the reference. Reported fractions divide each error by the applicable IACS UR L5 tolerance; the target is ≤0.2. All errors below are measured interpolation errors, not allowances on stability criteria.', '',
        '| Feeder | Density kg/m³ | Valid curves / outside / failures | GZ max / p99 m | GZ max / p99 L5 fraction | Area max / p99 m rad | Area max / p99 L5 fraction | GM max / p99 m | GM max / p99 L5 fraction |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for item in combined['validation']:
        values=[]
        for quantity in ('gz_m','area_mrad','gm_m'):
            stat=item['errors'][quantity]
            values += [f"{stat['max_absolute']:.6g} / {stat['p99_absolute']:.6g}",
                       f"{stat['max_l5_fraction']:.6g} / {stat['p99_l5_fraction']:.6g}"]
        lines.append('| '+ ' | '.join([item['vessel'],str(item['density_kg_m3']),
            f"{item['completed_samples']} / {len(item['outside_grid_cases'])} / {len(item['failures'])}",*values])+' |')
    lines += ['', 'Broad holdout states span the complete geometric grid at displacements from production lightship through upright scantling displacement. Dry, fully submerged, below-lightship and above-scantling states are counted separately. Outside-grid free-trim curves are fallback coverage checks, not discarded interpolation errors.', '',
        '| Feeder | Density | Broad samples / failures | GZ max / p99 m | GZ max / p99 L5 fraction | Below lightship / above scantling rejected |',
        '|---|---:|---:|---:|---:|---:|']
    for item in combined['validation']:
        broad=item['broad_geometric_validation']; stat=broad['errors']
        lines.append(f"| {item['vessel']} | {item['density_kg_m3']:g} | {broad['completed_samples']} / {len(broad['failures'])} | {stat['max_absolute']:.6g} / {stat['p99_absolute']:.6g} | {stat['max_l5_fraction']:.6g} / {stat['p99_l5_fraction']:.6g} | {broad['rejected_below_lightship']} / {broad['rejected_above_scantling']} |")
    lines += ['', '| Feeder | Warm median / max s | Direct s | Table used | Exact loading equilibrium | Whole-curve fallback | Same violation IDs |',
        '|---|---:|---:|---:|---:|---:|---|']
    for item in combined['timings']:
        m=item['metrics']
        lines.append(f"| {item['vessel']} | {item['median_seconds']:.6g} / {item['max_seconds']:.6g} | {item['direct_seconds']:.6g} | {m.get('geometry_table_used',0):g} | {m.get('geometry_loading_equilibrium_direct',0):g} | {m.get('geometry_table_limit_fallback',0):g} | {item['direct_violation_ids']==item['interpolated_violation_ids']} |")
    lines += ['', 'Reproduction, interpolation scheme, empirical scope and assumptions are described in [README.md](README.md). Raw random conditions, nonzero-TCG equilibrium errors, error distributions and per-repeat timings are in [results.json](results.json).']
    (ROOT/'report.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
