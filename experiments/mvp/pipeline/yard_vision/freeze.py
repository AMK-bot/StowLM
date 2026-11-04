"""Freeze the synthetic camera experiment before any model scoring."""
from datetime import datetime, timezone
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def freeze():
    destination = HERE / 'fixtures' / 'frozen.json'
    if destination.exists():
        raise FileExistsError('Frozen set exists; do not replace after scoring')
    paths = (ROOT / 'experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines()[:6]
    cases = [{'id': f'case-{i+1:02}', 'split': 'development' if i < 2 else 'held_out',
              'source': path, 'source_sha256': sha256_file(ROOT / path)}
             for i, path in enumerate(paths)]
    config = {
        'synthetic': True, 'mode': 'generated',
        'sampling': 'First six quick_small paths; first two development, next four held out. Full source yard states.',
        'arms': ['clean_render', 'repainted_photo'],
        'held_out_policy': 'Freeze before scoring; one read per arm, no prompt or validator tuning after development.',
        'pair_gate': 'Inspect repaint against clean render: exact box count and per-stack height/position. Discard changed or unverifiable scenes; count discards separately.',
        'ship_gate': 'No silent wrong complete stacks on held out; all ambiguity becomes Questions. A tiny synthetic pilot is not real-photo validation.',
        'budget_usd': 5, 'sam_usd_per_call': 0.0025, 'muse_image_usd_per_call': 0.01,
        'deadline_utc': '2026-11-02T03:46:33+00:00', 'cases': cases,
    }
    record = {**config, 'frozen_utc': datetime.now(timezone.utc).isoformat(), 'config_sha256': sha256_json(config)}
    destination.write_text(json.dumps(record, indent=2) + '\n')
    return record


if __name__ == '__main__':
    print(json.dumps(freeze(), indent=2))
