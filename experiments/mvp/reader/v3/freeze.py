"""D224 immutable recipe/final hashes; no sealed sentence parsing.

Recipe freeze is the explicit launch-time source-selection operation. Never run
it speculatively while data/tests are being prepared. Once selected, the source
cannot change when v2 later finishes. V3 always runs independent of v2 gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

from experiments.mvp.reader.freeze_v2 import sha256, record, verify
from experiments.mvp.reader.v3.train import HERE, READER, ROOT, load_source_selection, select_source


def inputs(stage, selected=None):
    selected = selected or load_source_selection()
    from pathlib import Path
    source = Path(selected['source_adapter'])
    files = [HERE / name for name in ('config.json', 'train.py', 'gpu_run.py', 'evaluate.py',
                                      'gates.py', 'adoption.py', 'freeze.py', 'decoder.py', 'build_data.py',
                                      'build_domain.py', 'domain_words.py',
                                      'spark_client.py', 'sealed.py', 'admission.py')]
    files += [HERE / 'data-domain' / name for name in ('train.jsonl', 'valid.jsonl', 'manifest.json')]
    files += [HERE / 'results/source-selection.json']
    files += [READER / name for name in ('contract.json', 'schema.py', 'train.py', 'train_v2.py',
                                        'gpu_run.py', 'gates_v2.py', 'evaluate.py', 'freeze_v2.py')]
    files += [source / name for name in ('adapters.safetensors', 'adapter_config.json', 'STOPPED.json')]
    files += [ROOT / 'models/glimmer-30b-4bit' / name for name in
              ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja')]
    files += [ROOT / name for name in
              ('src/llm/stowlm/train.py', 'src/llm/instructions/schema.py',
               'src/llm/spark/client.py', 'src/llm/spark/spend.py', 'src/llm/spark/planner_budget.py')]
    files += [READER / 'vocabulary.md']
    if stage == 'final':
        files += [HERE / 'results/recipe-freeze.json', HERE / 'sealed.py']
        files += [HERE / 'adapter-v3' / name for name in
                  ('adapters.safetensors', 'adapter_config.json', 'STOPPED.json')]
        # Existing Spark outputs are fixed historical reference artifacts. Their
        # decoder hash is preserved, never relabelled as current-mask parity.
        files += [HERE / 'sealed' / name for name in
                  ('cases.jsonl', 'manifest.json', 'spark-summary.json', 'spark-provenance.json',
                   'spark-predictions.jsonl', 'overlap-audit.json', 'packet-manifest.json',
                   'reviewer_prompt.txt', 'reviewer-process.json', 'reviewer-accounting.json',
                   'claude_response.json')]
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('recipe', 'final'), default='recipe')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    output = HERE / 'results' / f'{args.stage}-freeze.json'
    if args.check:
        verify(json.loads(output.read_text()))
        load_source_selection()
        print(json.dumps({'verified': True, 'freeze': str(output)}))
        return
    if output.exists():
        raise ValueError('freeze exists: retain and verify with --check')
    if args.stage == 'recipe':
        selected = select_source()
    else:
        verify(json.loads((HERE / 'results/recipe-freeze.json').read_text()))
        selected = load_source_selection()
    frozen = {'version': 'reader-v3-freeze/2-d224', 'stage': args.stage,
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'files_sha256': record(inputs(args.stage, selected), ROOT),
              'source_selection_sha256': sha256(HERE / 'results/source-selection.json'),
              'source_adapter_sha256': selected['source_adapter_sha256'],
              'source_adapter': selected['source_adapter'],
              'selection': 'D224: v3 always runs; source selected once; validation only; no sealed-driven retraining.',
              'gates': {'R1_min_exact_pct': 90, 'R2_max_false_acceptances': 0,
                        'min_count': 120, 'min_loosening_count': 40, 'R3_max_shortfall_pp': 3,
                        'adoption_requires_v2_same_seal_reference': True},
              'output_policy': 'Same current schema mask for all local Glimmer arms; historical Spark fixed reference; no repair.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(frozen, indent=2) + '\n')
    print(json.dumps({'freeze': str(output), 'sha256': sha256(output)}))


if __name__ == '__main__':
    main()
