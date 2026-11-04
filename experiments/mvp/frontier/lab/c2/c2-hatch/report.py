"""Combine fresh score.py upper bounds and certified relaxation lower bounds."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
C1 = HERE.parents[1] / 'c1'


def main():
    bound_folder = HERE / 'results/bounds/final-hold'
    bound_summary = json.loads((bound_folder / 'SUMMARY.json').read_text())
    if bound_summary['bound_source_sha256'] != hashlib.sha256((HERE / 'bound.py').read_bytes()).hexdigest():
        raise AssertionError('final bound source differs from measured certificate source')
    rows = []
    for path in sorted((HERE / 'results/quick').glob('*.json')):
        checked = json.loads(path.read_text())
        if checked.get('size') != 'small':
            continue
        name = checked['instance_id']
        bounds = []
        for directory in (C1 / 'fx-exact/results/bounds-small-final', HERE / 'results/bounds/final-hold'):
            candidate = directory / f'{name}.json'
            if candidate.exists():
                data = json.loads(candidate.read_text())
                if data['input_sha256'] != checked['input_sha256']:
                    raise AssertionError(f'input digest mismatch: {name}')
                result = data.get('result', data)
                bounds.append((result['lower_bound'], str(candidate)))
        lower, evidence = max(bounds)
        upper = checked['lifts'] if checked['passed'] else None
        if upper is not None and lower > upper + 1e-7:
            raise AssertionError(f'invalid certificate: {name} {lower} > {upper}')
        rows.append(dict(instance_id=name, input=checked['input'],
                         input_sha256=checked['input_sha256'],
                         lifts=upper, lower_bound=lower,
                         gap=upper-lower if upper is not None else None,
                         proved=upper is not None and abs(upper-lower) < 1e-7,
                         replay_legal=bool(checked.get('tries')),
                         passed=checked['passed'], lower_bound_evidence=evidence,
                         checked_evidence=str(path), parts=checked.get('parts'),
                         wall_s=checked['wall_s']))
    paired = [r for r in rows if r['gap'] is not None]
    report = dict(maps=len(rows), replay_legal=sum(r['replay_legal'] for r in rows),
                  full_checker_passes=len(paired), proved=sum(r['proved'] for r in rows),
                  mean_gap=sum(r['gap'] for r in paired)/len(paired) if paired else None,
                  gap_denominator=len(paired), rows=rows,
                  sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in HERE.glob('*.py')})
    (HERE / 'results/CERTIFICATES.json').write_text(json.dumps(report, indent=2)+'\n')
    lines = ['| quick_small map | Checked lifts | Lower bound | Gap | Proved |',
             '| --- | ---: | ---: | ---: | :---: |']
    for row in rows:
        lines.append(f"| {row['instance_id']} | {row['lifts']} | {row['lower_bound']} | {row['gap']} | {'yes' if row['proved'] else 'no'} |")
    lines.append(f"\n{report['proved']}/10 proved; mean gap {report['mean_gap']} lifts over {len(paired)} fully checked maps.\n")
    (HERE / 'results/TABLE.md').write_text('\n'.join(lines))
    print('\n'.join(lines))
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows', 'sources')}, indent=2))


if __name__ == '__main__':
    main()
