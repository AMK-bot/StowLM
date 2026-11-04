"""Summarize fresh scorecard rows, separating construction from fallback passes."""
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent


def summarize(directory):
    rows = []
    for path in sorted(directory.glob('*.json')):
        row = json.loads(path.read_text())
        if not isinstance(row, dict) or not row.get('instance_id'):
            continue
        meta = row.get('method_metadata', {})
        constructed = meta.get('constructor_complete_mask_legal', 0)
        tried = [a for a in row.get('tries', []) if a.get('candidate') == 0] if constructed else []
        if not constructed:
            outcome = 'no complete native constructor stow'
        elif not tried:
            outcome = 'unfinished: stored fallback selected first'
        elif tried[-1].get('passed'):
            outcome = 'fresh full-checker pass'
        else:
            outcome = tried[-1].get('failure_reason', 'rejected')
        rows.append(dict(instance_id=row['instance_id'], size=row.get('size'),
                         passed=row.get('passed'), lifts=row.get('lifts'),
                         constructed=constructed, constructor_outcome=outcome,
                         constructor_pass=bool(tried and tried[-1].get('passed')),
                         constructor_evidence=meta.get('constructor_evidence'),
                         failure_reason=row.get('failure_reason'),
                         propose_s=row.get('propose_s'), wall_s=row.get('wall_s'),
                         candidate_errors=row.get('candidate_errors'),
                         pattern=meta.get('pattern_model'), chain=meta.get('chain_model'),
                         optimized=meta.get('optimized_patterns') or meta.get('optimized_mixed')))
    return dict(rows=rows, maps=len(rows), passes=sum(bool(r['passed']) for r in rows),
                constructed=sum(bool(r['constructed']) for r in rows),
                constructor_passes=sum(r['constructor_pass'] for r in rows),
                wall_s=sum(r['wall_s'] or 0 for r in rows))


def main():
    result = {name: summarize(HERE / 'results' / name) for name in
              ('quick30-f300', 'medium12-pattern120-f300', 'medium12-optimized120-f300',
               'medium12-mixed120-f300')}
    target = HERE / 'results' / 'construction-summary.json'
    target.write_text(json.dumps(result, indent=2) + '\n')
    for name, row in result.items():
        print(name, {k: v for k, v in row.items() if k != 'rows'})
        for r in row['rows']:
            print(' ', r['instance_id'], r['constructor_outcome'], r['lifts'])


if __name__ == '__main__':
    main()
