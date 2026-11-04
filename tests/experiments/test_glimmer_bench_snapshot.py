"""Offline snapshot validation never imports MLX or contacts the Hub."""
import importlib.util
import json
from pathlib import Path

import pytest


def validator():
    path = Path(__file__).resolve().parents[2] / 'experiments/glimmer-bench/validate_conversion.py'
    spec = importlib.util.spec_from_file_location('conversion_validation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cached_snapshot(tmp_path):
    repo, revision = 'meta-models/Muse-Glimmer-30B', 'c' * 40
    root = tmp_path / 'models--meta-models--Muse-Glimmer-30B'
    snapshot = root / 'snapshots' / revision
    snapshot.mkdir(parents=True)
    files = {name: b'{}' for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json')}
    files['chat_template.jinja'] = b'template'
    files['model.safetensors.index.json'] = json.dumps({
        'metadata': {'total_size': 1},  # tensor payload size is NOT shard file size
        'weight_map': {'a': 'model-00001-of-00002.safetensors', 'b': 'model-00002-of-00002.safetensors'},
    }).encode()
    for i in (1, 2):
        files[f'model-{i:05}-of-00002.safetensors'] = b'fake-shard'
    for name, content in files.items():
        (snapshot / name).write_bytes(content)
    entries = {name: {'size': len(content)} for name, content in files.items()}
    entries.update({'.gitattributes': {'size': 10}, '.eval_results/mmi.yaml': {'size': 20}})
    (root / 'trees').mkdir()
    (root / 'trees' / f'{revision}.json').write_text(json.dumps({'format_version': 1, 'files': entries}))
    return tmp_path, repo, revision, snapshot, root


def test_cached_snapshot_missing_non_weight_metadata_passes(cached_snapshot):
    cache, repo, revision, snapshot, _ = cached_snapshot
    path, evidence = validator().resolve_cached_snapshot(repo, revision, cache)
    assert path == snapshot
    assert evidence['missing_metadata_files'] == ['.eval_results/mmi.yaml', '.gitattributes']
    assert len(evidence['verified_shard_sizes']) == 2


@pytest.mark.parametrize('name', ['model-00002-of-00002.safetensors', 'config.json', 'tokenizer.json',
                                  'tokenizer_config.json', 'chat_template.jinja'])
def test_cached_snapshot_missing_required_file_fails(cached_snapshot, name):
    cache, repo, revision, snapshot, _ = cached_snapshot
    (snapshot / name).unlink()
    with pytest.raises(ValueError, match='missing required file'):
        validator().resolve_cached_snapshot(repo, revision, cache)


def test_cached_snapshot_truncated_shard_fails(cached_snapshot):
    cache, repo, revision, snapshot, _ = cached_snapshot
    (snapshot / 'model-00002-of-00002.safetensors').write_bytes(b'short')
    with pytest.raises(ValueError, match='size mismatch'):
        validator().resolve_cached_snapshot(repo, revision, cache)


def test_cached_snapshot_unknown_shard_size_fails(cached_snapshot):
    cache, repo, revision, _, root = cached_snapshot
    tree = root / 'trees' / f'{revision}.json'
    entries = json.loads(tree.read_text())
    del entries['files']['model-00002-of-00002.safetensors']['size']
    tree.write_text(json.dumps(entries))
    with pytest.raises(ValueError, match='no trusted cached size'):
        validator().resolve_cached_snapshot(repo, revision, cache)
