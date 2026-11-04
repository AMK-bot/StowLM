"""CPU-only regressions for benchmark provenance and throughput."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'experiments/glimmer-bench' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bench():
    return load_script('bench_long')


def test_resume_identity_tracks_weights_prompt_versions_and_path(tmp_path, bench):
    model = tmp_path / 'one' / 'same-name'
    model.mkdir(parents=True)
    weights = model / 'weights.safetensors'
    weights.write_bytes(b'old')
    versions = {'mlx': '1'}
    def key(path=model, text='prompt', deps=versions):
        return bench.build_key(path, 10, 2, 20, 'none', text, deps)
    original = key()
    legacy = {name: original[name] for name in ('model', 'prompt_len', 'batch', 'max_out', 'mode')}
    assert not all(legacy.get(name) == value for name, value in original.items())
    weights.write_bytes(b'new')  # same size; directory name unchanged
    assert key() != original
    assert key(text='changed') != key()
    assert key(deps={'mlx': '2'}) != key()
    other = tmp_path / 'two' / 'same-name'
    other.mkdir(parents=True)
    (other / weights.name).write_bytes(b'new')
    assert key(path=other) != key()


def test_one_token_has_no_decode_throughput(bench):
    assert bench.throughput_metrics(4, 3.0, {1: 3.0}) == {
        'tok_s_agg_to_1': None, 'tok_s_agg_seg_1': None,
    }


def test_decode_throughput_preserves_existing_units(bench):
    assert bench.throughput_metrics(4, 1.0, {3: 3.0, 5: 7.0}) == {
        'tok_s_agg_to_3': 4.0, 'tok_s_agg_seg_3': 4.0,
        'tok_s_agg_to_5': 2.7, 'tok_s_agg_seg_5': 2.0,
    }


def test_conversion_is_pinned_and_records_settings():
    convert = load_script('convert')
    metadata = convert.conversion_provenance(4, {'mlx-lm': 'test'})
    assert metadata['source_revision'] == 'cec94fcfd6512f2cfe708138c32533b346ea4f69'
    assert metadata['q_bits'] == 4
    assert metadata['q_group_size'] == 64
    assert metadata['software'] == {'mlx-lm': 'test'}


def test_conversion_passes_pinned_revision_and_writes_metadata(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace

    convert = load_script('convert')
    calls = []
    original = lambda self, weights: weights
    model_class = type('Model', (), {'sanitize': original})
    monkeypatch.setitem(sys.modules, 'mlx_lm', SimpleNamespace(convert=lambda *a, **kw: calls.append((a, kw))))
    monkeypatch.setitem(sys.modules, 'mlx_lm.models', SimpleNamespace(muse_glimmer=SimpleNamespace(Model=model_class)))
    monkeypatch.setattr(convert.importlib.metadata, 'version', lambda name: 'test')
    monkeypatch.setattr(sys, 'argv', ['convert.py', str(tmp_path), '3'])
    convert.main()
    assert calls[0][1]['revision'] == convert.SOURCE_REVISION
    assert calls[0][1]['q_bits'] == 3
    assert json.loads((tmp_path / 'conversion_provenance.json').read_text())['q_bits'] == 3
    assert model_class.sanitize is original
