"""Hash-only D219 recipe/final freezes. Never parses sealed sentences or outputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def record(paths: list[Path], root: Path = ROOT) -> dict[str, str]:
    return {str(path.resolve().relative_to(root.resolve())): sha256(path)
            for path in paths}


def verify(freeze: dict, root: Path = ROOT) -> None:
    if not freeze.get('files_sha256'):
        raise ValueError('empty freeze')
    for name, expected in freeze['files_sha256'].items():
        path = root / name
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f'frozen file changed or missing: {name}')


def inputs(stage: str) -> list[Path]:
    files = [HERE / name for name in (
        'contract.json', 'schema.py', 'evaluate.py', 'train.py', 'gpu_run.py',
        'build_data_v2.py', 'train_v2.py', 'gpu_run_v2.py', 'gates_v2.py', 'sealed_v2.py',
        'config-v2.json', 'freeze_v2.py')]
    files += [HERE / 'data-v2' / name for name in
              ('train.jsonl', 'valid.jsonl', 'test.jsonl', 'manifest.json')]
    files += [HERE / 'adapter-v1' / name for name in
              ('adapters.safetensors', 'adapter_config.json')]
    files += [ROOT / 'models/glimmer-30b-4bit/config.json',
              ROOT / 'src/llm/stowlm/train.py', ROOT / 'src/llm/instructions/schema.py']
    if stage == 'final':
        files += [HERE / 'results-v2/recipe-freeze.json']
        files += [HERE / 'adapter-v2' / name for name in
                  ('adapters.safetensors', 'adapter_config.json', 'STOPPED.json')]
        files += [HERE / 'sealed-v2' / name for name in (
            'cases.jsonl', 'manifest.json', 'spark-summary.json',
            'spark-provenance.json', 'overlap-audit.json')]
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('recipe', 'final'), default='recipe')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    output = args.output or HERE / 'results-v2' / f'{args.stage}-freeze.json'
    if args.check:
        verify(json.loads(output.read_text()))
        print(json.dumps({'verified': True, 'freeze': str(output)}))
        return
    if output.exists():
        raise ValueError('freeze exists; retain it and use --check')
    if args.stage == 'final':
        verify(json.loads((HERE / 'results-v2/recipe-freeze.json').read_text()))
    frozen = {'version': 'reader-d219-freeze/1', 'stage': args.stage,
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'files_sha256': record(inputs(args.stage)),
              'selection': 'Validation families only; latest stopped adapter; no sealed-driven selection.',
              'gates': {'R1_exact_match_pct_min': 90, 'R2_false_acceptance_count_max': 0,
                        'R2_loosening_count_min': 30, 'R3_spark_gap_pp_max': 3},
              'output_policy': 'Raw greedy reader output; no correction or parse repair.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(frozen, indent=2) + '\n')
    print(json.dumps({'freeze': str(output), 'sha256': sha256(output),
                      'file_count': len(frozen['files_sha256'])}))


if __name__ == '__main__':
    main()
