"""Two manifest layers, using synthetic files and real scoped inventories."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import build, evidence
from experiments.mvp.pipeline.freeze import verify as verifier


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) if isinstance(value, dict) else value)
    return path


def _manifest(root, scope, layer=None):
    selection = evidence.inventory(root, scope=scope)
    files = {name: build.sha256_file(root / name)
             for name in build.inventory_paths(root, no_weights=False, import_closure=[],
                                               evidence_selection=selection, scope=scope)}
    sources = {name: files[name] for name in build.source_paths(root, import_closure=[], scope=scope)}
    result = {'schema_version': verifier.SCHEMA, 'task': '2.1', 'status': 'frozen',
              'scope': scope, 'dry_run': False, 'readiness': {'ready': True},
              'files': files, 'source_files': sources, 'tree_sha256': build.tree_digest(sources),
              'base_model': {'weight_files': {}}, 'model_weight_files': [], 'weight_stats': {},
              'selection': {'import_closure': {'paths': []}}, 'evidence': selection}
    if layer is not None:
        result['pipeline_layer'] = layer
    return result


def _layer(root, path):
    value = json.loads(path.read_text())
    return {'path': path.relative_to(root).as_posix(),
            'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'tree_digest': value['tree_sha256']}


@pytest.fixture
def layers(tmp_path):
    root = tmp_path / 'repo'
    _write(root / 'src/runtime.py', '# synthetic pipeline\n')
    _write(root / 'app/v6.py', '# synthetic film\n')
    _write(root / 'experiments/mvp/ui/cache/v6-hero/hero.json', {'synthetic': True})
    pipeline_path = root / build.FREEZE / 'manifest-pipeline.json'
    _write(pipeline_path, _manifest(root, 'pipeline'))
    film_path = root / build.FREEZE / 'manifest-film.json'
    _write(film_path, _manifest(root, 'film', _layer(root, pipeline_path)))
    return root, pipeline_path, film_path


def test_both_scope_guards_accept_valid_layers(layers):
    root, pipeline, film = layers
    for path in (pipeline, film):
        result = verifier.require_frozen(path, root=root)
        assert result['manifest_sha256'] == build.sha256_file(path)


def test_pipeline_scope_ignores_app_and_ui_changes_and_additions(layers):
    root, pipeline, film = layers
    _write(root / 'app/v6.py', '# film edit after pipeline freeze\n')
    _write(root / 'app/late.py', '# another film module\n')
    _write(root / 'experiments/mvp/ui/cache/v6-hero/hero.json', {'changed': True})
    assert verifier.verify(pipeline, root=root) == []
    verifier.require_frozen(pipeline, root=root)
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(film, root=root)
    assert any(row['path'] == 'app/v6.py' for row in error.value.drifts)


def test_dependency_manifest_byte_change_drifts_even_with_identical_tree(layers):
    root, pipeline, film = layers
    value = json.loads(pipeline.read_text())
    value['created_utc'] = 'different timestamp; identical source tree'
    _write(pipeline, value)
    verifier.require_frozen(pipeline, root=root)
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(film, root=root)
    assert any(row['reason'] == 'pipeline_manifest_changed' for row in error.value.drifts)


def test_dependency_new_valid_tree_is_not_the_pinned_layer(layers):
    root, pipeline, film = layers
    _write(root / 'src/runtime.py', '# a new valid pipeline\n')
    _write(pipeline, _manifest(root, 'pipeline'))
    verifier.require_frozen(pipeline, root=root)
    drifts = verifier.verify(film, root=root)
    assert any(row['reason'] == 'pipeline_tree_changed' for row in drifts)
    assert any(row['reason'] == 'pipeline_manifest_changed' for row in drifts)


def test_dependency_drift_is_checked_even_when_not_among_film_files(layers, monkeypatch):
    root, pipeline, film = layers
    value = json.loads(film.read_text())
    value['files'].pop('src/runtime.py')
    value['source_files'].pop('src/runtime.py')
    value['tree_sha256'] = build.tree_digest(value['source_files'])
    _write(film, value)
    original = build.inventory_paths
    monkeypatch.setattr(build, 'inventory_paths', lambda *args, **kwargs:
                        [name for name in original(*args, **kwargs)
                         if kwargs.get('scope') != 'film' or name != 'src/runtime.py'])
    _write(root / 'src/runtime.py', '# changed only in dependency\n')
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(film, root=root)
    assert any(row['path'] == 'src/runtime.py' and row['reason'] == 'changed'
               for row in error.value.drifts)


@pytest.mark.parametrize('kind', ['film', 'cycle', 'outside', 'absolute', 'symlink', 'missing'])
def test_film_dependency_rejects_unsafe_paths_and_nonpipeline_layers(layers, kind):
    root, pipeline, film = layers
    value = json.loads(film.read_text())
    if kind in {'film', 'cycle'}:
        value['pipeline_layer'] = _layer(root, film)
    elif kind == 'outside':
        value['pipeline_layer']['path'] = build.FREEZE + '/../outside.json'
    elif kind == 'absolute':
        value['pipeline_layer']['path'] = str(pipeline)
    elif kind == 'symlink':
        alias = pipeline.with_name('pipeline-alias.json')
        alias.symlink_to(pipeline)
        value['pipeline_layer']['path'] = alias.relative_to(root).as_posix()
    else:
        value.pop('pipeline_layer')
    _write(film, value)
    with pytest.raises(verifier.FreezeDrift):
        verifier.require_frozen(film, root=root)


def test_pipeline_manifest_cannot_declare_another_layer(layers):
    root, pipeline, _ = layers
    value = json.loads(pipeline.read_text())
    value['pipeline_layer'] = _layer(root, pipeline)
    _write(pipeline, value)
    with pytest.raises(verifier.FreezeDrift) as error:
        verifier.require_frozen(pipeline, root=root)
    assert any(row['reason'] == 'pipeline_layer_on_pipeline' for row in error.value.drifts)


def test_dry_film_requires_a_frozen_dependency(layers):
    root, pipeline, film = layers
    value = json.loads(film.read_text())
    value['dry_run'] = True
    _write(film, value)
    assert verifier.verify(film, root=root) == []
    dependency = json.loads(pipeline.read_text())
    dependency['dry_run'] = True
    _write(pipeline, dependency)
    value['pipeline_layer'] = _layer(root, pipeline)
    _write(film, value)
    assert any(row['reason'] == 'dry_manifest' for row in verifier.verify(film, root=root))


def test_pipeline_uses_filtered_cached_import_closure(layers, monkeypatch):
    root, pipeline, _ = layers
    value = json.loads(pipeline.read_text())
    value['selection']['import_closure']['paths'] = ['src/runtime.py', 'app/v6.py',
                                                   'experiments/mvp/ui/tool.py']
    _write(pipeline, value)
    original = build.inventory_paths
    calls = []
    def inventory(*args, **kwargs):
        calls.append(kwargs['import_closure'])
        return original(*args, **kwargs)
    monkeypatch.setattr(build, 'inventory_paths', inventory)
    assert verifier.verify(pipeline, root=root) == []
    assert calls == [['src/runtime.py']]


def test_film_full_weights_applies_to_pipeline_dependency(layers, monkeypatch):
    root, _, film = layers
    original = verifier._verify
    calls = []
    def check(manifest, **kwargs):
        calls.append((manifest['scope'], kwargs['check_weights']))
        return original(manifest, **kwargs)
    monkeypatch.setattr(verifier, '_verify', check)
    verifier.require_frozen(film, root=root, full_weights=True)
    assert ('pipeline', True) in calls and ('film', True) in calls


def test_legacy_manifest_without_scope_is_pipeline(layers):
    root, pipeline, _ = layers
    value = json.loads(pipeline.read_text())
    value.pop('scope')
    _write(pipeline, value)
    verifier.require_frozen(pipeline, root=root)
