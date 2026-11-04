"""D275 build scopes use temporary trees, without suites or model loads."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from experiments.mvp.pipeline.freeze import build
from experiments.mvp.pipeline.freeze.verify import FreezeDrift, verify
from experiments.mvp.pipeline.freeze.test_freeze import tree, put


def frozen_pipeline(root, path=None):
    manifest = build.build_manifest(root)
    assert manifest['readiness']['ready'], manifest['readiness']
    path = path or root / build.FREEZE / 'manifest-pipeline.json'
    put(root, path.relative_to(root).as_posix(), manifest)
    # The showcase publisher binds the mutable store after layer one exists.
    put(root, build.PIPELINE + '/lattice/store/publication-receipt.json',
        {'pipeline_sha256': build.sha256_file(path)})
    return path, manifest


def film_files(root):
    put(root, 'app/v6.py', '# Film runtime\n')
    put(root, 'experiments/mvp/ui/fixtures/example.json', {'synthetic': True})


def test_default_pipeline_excludes_film_but_keeps_app_transitive_imports(tree):
    put(tree, 'app/v6.py', 'import outside.helper\n')
    dependency = 'outside/helper.py'
    put(tree, dependency, 'VALUE=1\n')
    put(tree, 'experiments/mvp/ui/fixtures/film.json', {'unfinished': True})
    link = tree / 'experiments/mvp/ui/data-link.json'
    link.symlink_to(tree / build.PIPELINE / 'lattice/store/plan.json')
    value = build.build_manifest(tree, no_weights=True)
    assert value['scope'] == 'pipeline'
    assert dependency in value['files'] and dependency in value['source_files']
    assert dependency in value['selection']['import_closure']['paths']
    for selected in (value['files'], value['source_files'], value['symlinks'],
                     value['selection']['import_closure']['paths'],
                     value['selection']['import_closure_additions']):
        assert all(build.in_scope(name, 'pipeline') for name in selected)
    assert 'app' not in value['selection']['source_roots']
    assert not build.inventory_symlinks(tree, import_closure=[], scope='pipeline')


def test_pipeline_ignores_app_ui_changes_and_malformed_film_evidence(tree):
    film_files(tree)
    path, manifest = frozen_pipeline(tree)
    put(tree, 'app/v6.py', '# Film changed after pipeline freeze\n')
    put(tree, 'experiments/mvp/ui/new.json', '{ malformed JSON')
    put(tree, 'experiments/mvp/ui/fill-v6.json', {'{{unfilled}}': 'app/missing.json'})
    assert verify(path, root=tree) == []
    assert build.build_manifest(tree, no_weights=True)['readiness']['ready']


def test_film_requires_pipeline_and_records_exact_reference(tree):
    film_files(tree)
    path, pipeline = frozen_pipeline(tree)
    film = build.build_manifest(tree, no_weights=True, scope='film', pipeline_manifest=path)
    assert film['scope'] == 'film'
    assert film['pipeline_layer'] == {'path': path.relative_to(tree).as_posix(),
                                      'manifest_sha256': build.sha256_file(path),
                                      'tree_digest': pipeline['tree_sha256']}
    assert 'app/v6.py' in film['files'] and 'app/v6.py' in film['source_files']
    assert 'experiments/mvp/ui/fixtures/example.json' in film['files']
    assert film['readiness']['ready'], film['readiness']


def test_film_inherits_verified_pipeline_weight_hashes_without_rehash(tree, monkeypatch):
    film_files(tree)
    path, pipeline = frozen_pipeline(tree)
    original = build.sha256_file
    hashed = []
    def record(path):
        name = Path(path).relative_to(tree).as_posix()
        hashed.append(name)
        return original(path)
    monkeypatch.setattr(build, 'sha256_file', record)
    film = build.build_manifest(tree, scope='film', pipeline_manifest=path.relative_to(tree))
    weights = pipeline['base_model']['weight_files']
    assert not set(weights) & set(hashed)
    assert film['base_model']['weight_files'] == weights
    assert film['base_model']['weights_inherited'] is True
    assert film['base_model']['weights_hashed'] is False


@pytest.mark.parametrize('drift', ['source', 'manifest'])
def test_film_refuses_pipeline_drift_during_inventory(tree, monkeypatch, drift):
    film_files(tree)
    path, pipeline = frozen_pipeline(tree)
    original = build._prompts
    def change_layer(root, files):
        if drift == 'source':
            put(tree, 'src/pinned.py', '# concurrent pipeline change\n')
        else:
            replacement = dict(pipeline, created_utc='changed after initial verification')
            put(tree, path.relative_to(tree).as_posix(), replacement)
        return original(root, files)
    monkeypatch.setattr(build, '_prompts', change_layer)
    with pytest.raises((FreezeDrift, ValueError), match='changed'):
        build.build_manifest(tree, no_weights=True, scope='film', pipeline_manifest=path)


@pytest.mark.parametrize('failure', ['missing', 'dry', 'changed', 'wrong_scope'])
def test_film_refuses_missing_dry_changed_or_nonpipeline_layer(tree, failure):
    path = tree / build.FREEZE / 'manifest-pipeline.json'
    if failure != 'missing':
        value = build.build_manifest(tree, no_weights=failure == 'dry')
        if failure == 'wrong_scope':
            value['scope'] = 'film'
        put(tree, path.relative_to(tree).as_posix(), value)
        if failure == 'changed':
            put(tree, 'src/pinned.py', '# changed after freeze\n')
    with pytest.raises((FreezeDrift, ValueError)):
        build.build_manifest(tree, no_weights=True, scope='film', pipeline_manifest=path)


def test_film_rejects_pipeline_reference_outside_repository(tree, tmp_path):
    outside = put(tmp_path, 'outside.json', {})
    with pytest.raises(ValueError, match='outside repository'):
        build.build_manifest(tree, no_weights=True, scope='film', pipeline_manifest=outside)


def test_film_rejects_pipeline_manifest_inside_repo_outside_freeze(tree):
    _, value = frozen_pipeline(tree)
    copied = put(tree, 'other/pipeline.json', value)
    with pytest.raises(ValueError, match='JSON file inside freeze'):
        build.build_manifest(tree, no_weights=True, scope='film', pipeline_manifest=copied)


def test_suite_receipts_are_separate_per_scope(tree):
    manifest_path, _ = frozen_pipeline(tree)
    pipeline_receipt = build.FREEZE + '/suites/pipeline/counts.json'
    film_receipt = build.FREEZE + '/suites/film/counts.json'
    put(tree, pipeline_receipt, {'tests': 3})
    put(tree, film_receipt, {'tests': 4})
    pipeline = build.inventory_paths(tree, scope='pipeline', import_closure=[])
    film = build.inventory_paths(tree, scope='film', import_closure=[])
    assert pipeline_receipt not in pipeline and film_receipt not in pipeline
    assert pipeline_receipt in film and film_receipt in film
    assert verify(manifest_path, root=tree) == []
    put(tree, pipeline_receipt, {'tests': 30})
    put(tree, film_receipt, {'tests': 40})
    assert verify(manifest_path, root=tree) == []


@pytest.mark.parametrize('filename', ['manifest.json', 'manifest-pipeline.json', 'manifest-film.json'])
def test_no_weights_cli_refuses_every_final_output_filename(tree, filename, monkeypatch):
    output = tree / build.FREEZE / filename
    monkeypatch.setattr(sys, 'argv', ['build.py', '--root', str(tree), '--no-weights', '--out', str(output)])
    with pytest.raises(SystemExit) as exit:
        build.main()
    assert exit.value.code == 2
    assert not output.exists()


def test_cli_defaults_to_pipeline_manifest_in_fake_tree(tree, monkeypatch, capsys):
    (tree / build.FREEZE).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, 'argv', ['build.py', '--root', str(tree)])
    build.main()
    output = tree / build.FREEZE / 'manifest-pipeline.json'
    assert json.loads(output.read_text())['scope'] == 'pipeline'
    assert json.loads(capsys.readouterr().out)['scope'] == 'pipeline'
    assert not (tree / build.FREEZE / 'manifest-film.json').exists()
