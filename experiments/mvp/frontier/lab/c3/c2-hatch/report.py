"""Join exact lower bounds with fresh independent score.py checks."""
from pathlib import Path
from collections import Counter
import json

HERE = Path(__file__).resolve().parent


def main():
    groups = {'quick_tiny': 'checked-quick', 'quick_small': 'checked-quick',
              'validation_small': 'checked-validation'}
    report = {}
    for group, checked_folder in groups.items():
        rows = []
        for path in sorted((HERE / 'results/bounds' / group).glob('*.json')):
            if path.name == 'SUMMARY.json':
                continue
            row = json.loads(path.read_text())
            checked_path = HERE / 'results' / checked_folder / f'{row["instance_id"]}.json'
            checked = json.loads(checked_path.read_text()) if checked_path.exists() else {}
            if checked and checked['input_sha256'] != row['input_sha256']:
                raise AssertionError('bound and upper-bound input digests differ')
            upper = checked.get('lifts') if checked.get('passed') else None
            lower = row['result']['lower_bound']
            if upper is not None and lower > upper+1e-7:
                raise AssertionError('lower bound exceeds fresh independently checked upper')
            rows.append(dict(instance_id=row['instance_id'], input=row['input'],
                             input_sha256=row['input_sha256'], lower_bound=lower,
                             checked_upper=upper, gap=upper-lower if upper is not None else None,
                             proved=upper is not None and abs(upper-lower)<1e-7,
                             proof_family=row['result'].get('proof_family'),
                             support_flags=row['result'].get('support_flags'),
                             source_config=row['config'],
                             replay_legal=bool(checked.get('tries')),
                             full_checker_passed=bool(checked.get('passed')),
                             bound_status=row['result']['status'], bound_seconds=row['wall_s'],
                             check_seconds=checked.get('wall_s'),
                             bound_evidence=str(path.relative_to(HERE)),
                             checked_evidence=str(checked_path.relative_to(HERE))))
        passed = [r for r in rows if r['checked_upper'] is not None]
        report[group] = dict(maps=len(rows), proved=sum(r['proved'] for r in rows),
                             replay_legal=sum(r['replay_legal'] for r in rows),
                             full_checker_passes=len(passed),
                             mean_gap=sum(r['gap'] for r in passed)/len(passed) if passed else None,
                             bound_seconds=sum(r['bound_seconds'] for r in rows),
                             check_seconds=sum(r['check_seconds'] or 0 for r in rows),
                             proved_by_family=dict(Counter(r['proof_family'] for r in rows if r['proved'])),
                             rows=rows)
    (HERE / 'results/CERTIFICATES.json').write_text(json.dumps(report, indent=2)+'\n')
    lines = []
    for group, data in report.items():
        lines += [f'## {group}', '',
                  '| Map | Checked upper | Lower | Gap | Proved | Bound seconds |',
                  '| --- | ---: | ---: | ---: | :---: | ---: |']
        for row in data['rows']:
            lines.append(f"| {row['instance_id']} | {row['checked_upper']} | {row['lower_bound']} | {row['gap']} | {'yes' if row['proved'] else 'no'} | {row['bound_seconds']:.3f} |")
        lines += ['', f"{data['proved']}/{data['maps']} proved; {data['full_checker_passes']} fresh full checks; {data['bound_seconds']:.3f}s bounds, {data['check_seconds']:.3f}s checking.", '']
    (HERE / 'results/TABLE.md').write_text('\n'.join(lines))
    print(json.dumps({g:{k:v for k,v in r.items() if k!='rows'} for g,r in report.items()}, indent=2))


if __name__ == '__main__':
    main()
