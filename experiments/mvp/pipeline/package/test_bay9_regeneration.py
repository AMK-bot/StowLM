"""Bay-nine handover preflight; fake freeze guard, no model or numerical work."""
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file
from experiments.mvp.pipeline.package import regenerate as build
from experiments.mvp.pipeline.freeze import verify as frozen

HERE = Path(__file__).resolve().parent


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / 'pipeline.json'
    path.write_text(json.dumps({'scope': 'pipeline', 'tree_sha256': 'a' * 64}) + '\n')
    return path


def forbidden(*args, **kwargs):
    raise AssertionError('preflight reached a reader, source assembly or numerical work')


@pytest.mark.parametrize('source', [None, '', 'planner_ui:chief-officer-42',
                                   'user_authorized_batch:package_regeneration', True])
def test_automatic_regeneration_rejects_confirmation_override_before_side_effects(
        tmp_path, monkeypatch, source):
    for name in ('perf_counter', 'load_pipeline_manifest', 'selected_inputs',
                 'seed_qualified_tables', '_runtime'):
        monkeypatch.setattr(build, name, forbidden)
    directory = tmp_path / 'must-not-be-created'
    with pytest.raises(ValueError, match='automatic hero regeneration requires.*batch confirmation source'):
        build.regenerate(out_dir=directory, confirmation_source=source)
    assert not directory.exists()


@pytest.mark.parametrize('supplied', ['neither', 'path_only', 'hash_only'])
def test_regeneration_requires_explicit_pipeline_path_and_hash(manifest, monkeypatch, supplied):
    monkeypatch.setattr(build, 'selected_inputs', forbidden)
    kwargs = {}
    if supplied == 'path_only':
        kwargs['pipeline_manifest'] = manifest
    if supplied == 'hash_only':
        kwargs['pipeline_sha256'] = sha256_file(manifest)
    with pytest.raises(ValueError, match='pipeline'):
        build.regenerate(out_dir=HERE / 'fixtures/.unit-preflight-only', **kwargs)
    assert not (HERE / 'fixtures/.unit-preflight-only').exists()


def test_pipeline_wrong_hash_fails_before_freeze_guard(manifest, monkeypatch):
    monkeypatch.setattr(frozen, 'require_frozen', forbidden)
    with pytest.raises(ValueError, match='SHA256|sha256|hash'):
        build.load_pipeline_manifest(manifest, 'b' * 64)


def test_pipeline_guard_identity_is_bound_to_exact_manifest_bytes(manifest, monkeypatch):
    digest = sha256_file(manifest)
    calls = []

    def guard(path, **kwargs):
        calls.append(Path(path).resolve())
        return {'manifest_sha256': digest, 'tree_digest': 'a' * 64}

    monkeypatch.setattr(frozen, 'require_frozen', guard)
    identity = build.load_pipeline_manifest(manifest, digest)
    assert identity['manifest_sha256'] == digest
    assert calls == [manifest.resolve()]


def test_pipeline_guard_cannot_return_another_identity(manifest, monkeypatch):
    monkeypatch.setattr(frozen, 'require_frozen', lambda *args, **kwargs:
                        {'manifest_sha256': 'c' * 64, 'tree_digest': 'a' * 64})
    with pytest.raises(ValueError, match='SHA256|sha256|hash|identity|changed'):
        build.load_pipeline_manifest(manifest, sha256_file(manifest))


@pytest.mark.parametrize('digest', ['short', 'A' * 64, 'z' * 64, 1])
def test_pipeline_hash_must_be_explicit_lowercase_sha256(manifest, monkeypatch, digest):
    monkeypatch.setattr(frozen, 'require_frozen', forbidden)
    with pytest.raises(ValueError, match='SHA256|sha256|hash'):
        build.load_pipeline_manifest(manifest, digest)


def test_film_manifest_cannot_replace_pipeline_manifest(manifest, monkeypatch):
    manifest.write_text(json.dumps({'scope': 'film', 'tree_sha256': 'a' * 64}) + '\n')
    digest = sha256_file(manifest)
    monkeypatch.setattr(frozen, 'require_frozen', lambda *args, **kwargs:
                        {'manifest_sha256': digest, 'tree_digest': 'a' * 64})
    with pytest.raises(ValueError, match='pipeline|scope|film'):
        build.load_pipeline_manifest(manifest, digest)


def test_pipeline_change_during_guard_is_rejected(manifest, monkeypatch):
    digest = sha256_file(manifest)

    def changing_guard(*args, **kwargs):
        manifest.write_text('{"scope":"pipeline","tree_sha256":"changed"}\n')
        return {'manifest_sha256': digest, 'tree_digest': 'a' * 64}

    monkeypatch.setattr(frozen, 'require_frozen', changing_guard)
    with pytest.raises(ValueError, match='SHA256|sha256|hash|changed'):
        build.load_pipeline_manifest(manifest, digest)


def test_default_hero_reading_is_radio_selected_bay_nine():
    radio = json.loads((HERE.parent / 'radio/hero-selection.json').read_text())
    selected, instruction, instance, source, instruction_path = build.selected_inputs(
        build.LATTICE / 'hero.json', 'hero')
    assert instruction['sentences'] == [radio['hero_sentence']]
    assert instruction['canonical_rules'] == [radio['canonical_rule']]
    assert radio['canonical_rule']['constraint'] == {'type': 'close_bay', 'bay': 9}
    assert instruction['instance_sha256'] == selected['instance_sha256']
    assert instance.id == selected['instance_id']
    assert source.is_file()
    assert instruction_path == (HERE.parent / 'radio/hero-selection.json').resolve()


def test_old_dg_instruction_cannot_replace_adopted_bay_nine():
    path = build.LATTICE / 'fixtures/hero-instruction.json'
    with pytest.raises(ValueError, match='adopted bay-9'):
        build.selected_inputs(build.LATTICE / 'hero.json', 'hero', path)


@pytest.mark.parametrize('arguments', [[], ['--pipeline-manifest', 'manifest.json'],
                                       ['--pipeline-sha256', 'a' * 64]])
def test_cli_requires_both_pipeline_arguments_before_work(arguments, monkeypatch):
    monkeypatch.setattr(build, 'regenerate', forbidden)
    with pytest.raises(SystemExit) as error:
        build.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize('mode', [None, 'live', 'recorded'])
def test_cli_routes_reader_mode_and_explicit_pipeline_identity(manifest, mode, monkeypatch, capsys):
    called = []

    def regenerate(**kwargs):
        called.append(kwargs)
        return {'passed': True}

    monkeypatch.setattr(build, 'regenerate', regenerate)
    digest = sha256_file(manifest)
    arguments = ['--pipeline-manifest', str(manifest), '--pipeline-sha256', digest]
    if mode is not None:
        arguments += ['--reader-mode', mode]
    assert build.main(arguments) == 0
    assert len(called) == 1
    assert called[0]['pipeline_manifest'] == manifest
    assert called[0]['pipeline_sha256'] == digest
    assert called[0]['reader_mode'] == (mode or 'live')
    assert '"passed": true' in capsys.readouterr().out
