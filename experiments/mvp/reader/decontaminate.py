"""Remove accidental exact sentence overlaps using hashes, never sealed labels.

This is corpus hygiene, not model/prompt tuning. The sealed artifact stays byte
identical. Removed text/labels are not written to training artifacts or logs.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def text_hash(sentence):
    return hashlib.sha256(sentence.casefold().strip().encode()).hexdigest()


def main():
    data = HERE / 'data'
    sealed_path = data / 'sealed.jsonl'
    hashes = {text_hash(json.loads(s)['sentence']) for s in sealed_path.read_text().splitlines() if s.strip()}
    evidence = {'policy': 'Remove exact casefold/strip sentence matches; never inspect/use sealed expected labels.',
                'sealed_sha256': hashlib.sha256(sealed_path.read_bytes()).hexdigest(), 'splits': {}}
    manifest_path = data / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    original_manifest_path = data / 'manifest.pre-decontamination.json'
    if original_manifest_path.exists():
        raise ValueError('Decontamination already performed; preserve original frozen evidence')
    prepared = {}
    family_counts = Counter()
    for name in ('train', 'valid', 'test'):
        path = data / f'{name}.jsonl'
        lines = path.read_text().splitlines()
        kept, removed = [], []
        for line in lines:
            if not line.strip():
                continue
            row = json.loads(line)
            digest = text_hash(row['sentence'])
            if digest in hashes:
                removed.append({'id': row['id'], 'sentence_sha256': digest})
            else:
                kept.append(line)
        if name == 'train' and len(kept) < 3000:
            raise ValueError('Decontamination would leave fewer than 3000 training rows')
        prepared[path] = '\n'.join(kept) + '\n'
        evidence['splits'][name] = {'before': len(kept) + len(removed), 'after': len(kept),
                                    'removed': removed, 'sha256': hashlib.sha256(prepared[path].encode()).hexdigest()}
        parsed = [json.loads(line) for line in kept]
        family_counts.update(r['family'] for r in parsed)
        previous = manifest['files'][path.name]
        previous.update(rows=len(kept), sha256=hashlib.sha256(prepared[path].encode()).hexdigest(),
                        categories=dict(sorted(Counter(r['category'] for r in parsed).items())),
                        sources=dict(sorted(Counter(r['source'] for r in parsed).items())))
    original_manifest_path.write_bytes(manifest_path.read_bytes())
    for path, raw in prepared.items():
        path.write_text(raw)
    for family in manifest['families']:
        family['rows'] = family_counts[family['family']]
    manifest['decontamination'] = evidence
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    (HERE / 'results/decontamination.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps({n: {'before': e['before'], 'after': e['after'], 'removed': len(e['removed'])}
                      for n, e in evidence['splits'].items()}, indent=2))


if __name__ == '__main__':
    main()
