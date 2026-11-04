"""D311 development caches are runtime data, without expanding older pins."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import build, runtime_outputs
from experiments.mvp.pipeline.freeze import verify as verifier
from experiments.mvp.pipeline.freeze.test_runtime_verify import SCRATCH, put, runtime_tree

DEVELOPMENT_CACHE = 'experiments/mvp/pipeline/explain/fixtures/development/current-cache'


def test_registered_development_cache_accepts_data_and_excludes_from_inventory(tmp_path):
    root = Path(__file__).resolve().parents[4]
    config = json.loads((root / runtime_outputs.CONFIG).read_text())
    assert DEVELOPMENT_CACHE in config['directories']
    put(tmp_path, runtime_outputs.CONFIG, config)
    names = [DEVELOPMENT_CACHE + '/answer.json', DEVELOPMENT_CACHE + '/answer.lock']
    for name in names:
        put(tmp_path, name, '{}')
    policy = runtime_outputs.load(tmp_path)
    assert set(names).issubset(runtime_outputs.enumerate(tmp_path, policy))
    inventory = build.inventory_paths(tmp_path, import_closure=[], runtime_dirs=policy['directories'])
    assert not set(names).intersection(inventory)


def test_changed_config_keeps_only_hash_bound_old_policy(runtime_tree):
    root, manifest = runtime_tree
    put(root, runtime_outputs.CONFIG, {'schema_version': runtime_outputs.SCHEMA,
                                     'directories': [SCRATCH, DEVELOPMENT_CACHE]})
    put(root, SCRATCH + '/new.json', {'runtime': True})
    drifts = verifier.verify(manifest, root=root)
    expected = {'path': runtime_outputs.CONFIG, 'reason': 'changed',
                'expected': json.loads(manifest.read_text())['files'][runtime_outputs.CONFIG],
                'actual': verifier.build.sha256_file(root / runtime_outputs.CONFIG)}
    # Film verifies its pipeline dependency too, so the same changed pin can
    # appear twice; neither audit may widen the old runtime exclusions.
    assert drifts and all(row == expected for row in drifts)
    frozen = runtime_outputs.from_manifest(root, json.loads(manifest.read_text()), allow_changed_config=True)
    assert frozen['directories'] == [SCRATCH]
    assert not runtime_outputs.contains(DEVELOPMENT_CACHE + '/new.json', frozen)


def test_changed_config_cannot_authenticate_a_forged_snapshot(runtime_tree):
    root, manifest = runtime_tree
    value = json.loads(manifest.read_text())
    value['selection']['runtime_outputs']['directories'].append(DEVELOPMENT_CACHE)
    put(root, runtime_outputs.CONFIG, {'schema_version': runtime_outputs.SCHEMA,
                                     'directories': [SCRATCH, DEVELOPMENT_CACHE]})
    with pytest.raises(ValueError, match='differs from frozen pin'):
        runtime_outputs.from_manifest(root, value, allow_changed_config=True)
