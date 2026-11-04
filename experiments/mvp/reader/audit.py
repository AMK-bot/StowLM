"""CPU-only artifact checks; never changes labels or derives training from sealed text."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.mvp.reader.schema import canonicalize, messages


def audit(directory):
    paths = {name: directory / f'{name}.jsonl' for name in ('train', 'valid', 'test', 'sealed')}
    all_rows, summary = {}, {}
    for name, path in paths.items():
        if not path.exists():
            if name == 'test':
                continue
            raise ValueError(f'missing {name} artifact')
        rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
        all_rows[name] = rows
        ids = [row['id'] for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError(f'duplicate ids in {name}')
        for row in rows:
            canonicalize(row['expected'], row['context'])
            if 'messages' in row:
                expected_messages = messages(row['sentence'], row['context'])
                if row['messages'][:-1] != expected_messages:
                    raise ValueError(f'prompt mismatch in {name}')
                if row['messages'][-1]['role'] != 'assistant':
                    raise ValueError('training record lacks assistant target')
                if canonicalize(row['messages'][-1]['content'], row['context']) != canonicalize(row['expected'], row['context']):
                    raise ValueError('SFT target differs from expected')
        summary[name] = {'rows': len(rows), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'kinds': dict(Counter(row.get('kind', row.get('category', 'unspecified')) for row in rows))}
    if len(all_rows['train']) < 3000 or len(all_rows['sealed']) < 100:
        raise ValueError('insufficient training/sealed size')
    for left, lrows in all_rows.items():
        for right, rrows in all_rows.items():
            if left >= right:
                continue
            lsent = {r['sentence'].casefold().strip() for r in lrows}
            rsent = {r['sentence'].casefold().strip() for r in rrows}
            if lsent & rsent:
                raise ValueError(f'sentence leakage {left}/{right}')
            if 'sealed' not in (left, right):
                lf = {r['family'] for r in lrows}
                rf = {r['family'] for r in rrows}
                if lf & rf:
                    raise ValueError(f'family leakage {left}/{right}')
    summary['checks'] = ['strict targets valid in ship context', 'SFT prompts and targets match schema',
                         'train >=3000', 'sealed >=100', 'no cross-split sentence overlap',
                         'whole generator/paraphrase families held out']
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(__file__).parent / 'data')
    parser.add_argument('--out', type=Path, default=Path(__file__).parent / 'results/artifact-audit.json')
    args = parser.parse_args()
    result = audit(args.data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
