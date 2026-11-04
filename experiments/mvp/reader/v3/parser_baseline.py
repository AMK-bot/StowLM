"""Freeze fair parser before exactly one sealed-v3 score. Prints aggregates only.

Run freeze after public-fixture tests. The exclusive score-start marker consumes
this arm even if scoring fails; never retry against this sealed set. Frozen
parser is self-contained and unaffected by later training/data/module changes.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'parser'
DEPENDENCIES = [HERE / 'fair_parser.py', HERE / 'test_fair_parser.py', HERE / 'parser_baseline.py',
                HERE / 'build_data.py',
                HERE.parent / 'schema.py', HERE.parent / 'evaluate.py',
                HERE.parent / 'contract.json', HERE.parent / 'vocabulary.md']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze():
    OUT.mkdir(exist_ok=True)
    manifest_path = OUT / 'freeze.json'
    if manifest_path.exists() or (OUT / 'fair_parser.frozen.py').exists():
        raise ValueError('Fair-parser freeze already exists; never overwrite after sealing.')
    # No sealed/test/data input is accepted or opened here.
    source = (HERE / 'fair_parser.py').read_bytes()
    with (OUT / 'fair_parser.frozen.py').open('xb') as stream:
        stream.write(source)
    record = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'author_blind_to_all_sealed_sentences': True,
              'sources': 'Public contract/vocabulary, public IMDG terminology, v3 validation-error grammar only.',
              'frozen_parser_sha256': sha(OUT / 'fair_parser.frozen.py'),
              'files': {str(path.relative_to(HERE.parent)): sha(path) for path in DEPENDENCIES},
              'policy': 'One sealed-v3 score, no post-score tuning or repeat; Spark 126/128 reference unchanged.'}
    with manifest_path.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'frozen': True, 'created_utc': record['created_utc'],
                      'frozen_parser_sha256': record['frozen_parser_sha256']}))


def score():
    from experiments.mvp.reader.v3.admission import admit
    admit()
    manifest = json.loads((OUT / 'freeze.json').read_text())
    frozen = OUT / 'fair_parser.frozen.py'
    if sha(frozen) != manifest['frozen_parser_sha256']:
        raise ValueError('Frozen parser changed.')
    # Contract/scorer identity must be unchanged; training grammar/data need not be.
    for relative, digest in manifest['files'].items():
        if sha(HERE.parent / relative) != digest:
            raise ValueError('Frozen dependency changed: ' + relative)
    spec = importlib.util.spec_from_file_location('frozen_fair_parser', frozen)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from experiments.mvp.reader.evaluate import score_predictions
    # Import and verify parser BEFORE any access to sealed sentences.
    sealed = HERE / 'sealed' / 'cases.jsonl'
    original = json.loads((HERE / 'sealed' / 'manifest.json').read_text())
    if sha(sealed) != original['cases_sha256']:
        raise ValueError('Original sealed-v3 cases changed.')
    with (OUT / 'score-started.json').open('x') as stream:
        json.dump({'started_utc': datetime.now(timezone.utc).isoformat(),
                   'frozen_parser_sha256': sha(frozen), 'one_shot': True}, stream)
    records = [json.loads(line) for line in sealed.read_text().splitlines() if line.strip()]
    predictions = [{'id': row['id'], 'prediction': module.parse(row['sentence'], row['context'])} for row in records]
    with (OUT / 'sealed-v3-predictions.jsonl').open('x') as stream:
        for prediction in predictions:
            stream.write(json.dumps(prediction, separators=(',', ':')) + '\n')
    report = score_predictions(records, predictions)
    report.update(backend='fair_handwritten_parser', frozen_parser_sha256=sha(frozen),
                  freeze_sha256=sha(OUT / 'freeze.json'), data_sha256=sha(sealed),
                  predictions_sha256=sha(OUT / 'sealed-v3-predictions.jsonl'),
                  spark_reference_exact_count=126, spark_reference_count=128,
                  one_shot=True, completed_utc=datetime.now(timezone.utc).isoformat())
    with (OUT / 'sealed-v3-report.json').open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k not in {'details', 'by_family'}}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'score'])
    args = parser.parse_args()
    {'freeze': freeze, 'score': score}[args.action]()


if __name__ == '__main__':
    main()
