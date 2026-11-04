"""Offline D275 protocol checks on fake manifests; no held-out inputs or models."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed.common import HERE, HarnessError, object_hash
from experiments.mvp.pipeline.freeze.paths import reject_symlinks


def load_protocol():
    path = HERE/'fixtures/d275/protocol.py'
    spec = importlib.util.spec_from_file_location('sealed_d275_protocol_tests', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def example(tmp_path, monkeypatch):
    protocol = load_protocol()
    root = tmp_path/'repo'
    root.mkdir()
    selection_path = root/protocol.PROFILE_SELECTION
    selection_path.parent.mkdir(parents=True)
    kwargs = dict(budget_s=300., finalize_s=100., seed=0,
                  config=dict(assignment_tries=1, warm_polish=False, work_count_search=False))
    selection = dict(schema_version='stowlm.profile-selection/1', measured=True,
        decision_authority='leader', decision_kind='explicit_partial_study_selection',
        user_authorized=True, stopped=True,
        profiles=dict(standard=dict(engine_kwargs=kwargs, engine_timeout_s=1800.)))
    selection_path.write_text(json.dumps(selection))
    evidence = root/'experiments/mvp/pipeline/reader_final/runs/public/evidence.json'
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"public_development_evidence":true}')
    manifest = root/'experiments/mvp/pipeline/freeze/runs/pipeline-epoch/manifest.json'
    manifest.parent.mkdir(parents=True)
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (selection_path, evidence)}
    data = dict(schema_version='stowlm.pipeline-freeze/1', scope='pipeline', task='2.1',
        status='frozen', dry_run=False, readiness=dict(ready=True), files=files, tree_sha256='a'*64)
    manifest.write_text(json.dumps(data))
    registration_path = root/'registration.json'
    registration = dict(arms=dict(pipeline=dict(profile='standard'), instruction=dict(profile='standard')))
    registration_path.write_text(json.dumps(registration))
    from engine.config import Config
    runtime = dict(kwargs, config=Config(**kwargs['config']), name='standard', engine_timeout_s=1800.,
                   selection_kind='explicit_partial_study_selection')
    calls = []
    def guard(path, **options):
        calls.append((path, options))
        return dict(manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), tree_digest='a'*64)
    monkeypatch.setattr(protocol, '_dependencies', lambda: (guard, reject_symlinks))
    def verify():
        return protocol.verify_pipeline_manifest(manifest, root=root,
            registration_path=registration_path, profile_loader=lambda name: runtime)
    return SimpleNamespace(**locals())


def test_named_standard_1800_with_expanded_native_defaults_and_runs_evidence_passes(example):
    data, files = example.verify()
    assert data['scope'] == 'pipeline'
    assert str(example.evidence) in files
    assert set(files) == {str(example.evidence), str(example.selection_path)}
    assert example.calls[0][1]['full_weights'] is True
    assert example.calls[0][1]['root'] == example.root
    identity = example.protocol.verified_identity(example.manifest, root=example.root)
    assert identity['tree_digest'] == 'a'*64
    assert len(identity['manifest_sha256']) == 64


@pytest.mark.parametrize('change', ['wall', 'native_config', 'budget', 'runtime_name', 'pending'])
def test_exact_selected_runtime_recipe_is_required(example, change):
    if change == 'wall':
        example.runtime['engine_timeout_s'] = 900.
    elif change == 'budget':
        example.runtime['budget_s'] = 150.
    elif change == 'runtime_name':
        example.runtime['name'] = 'live'
    elif change == 'pending':
        example.runtime['pending_measurement'] = True
    else:
        from dataclasses import replace
        example.runtime['config'] = replace(example.runtime['config'], assignment_tries=2)
    with pytest.raises(HarnessError, match='recipe|name|provisional'):
        example.verify()


def test_registered_numeric_record_and_semantic_hash_are_enforced(example):
    record = dict(name='standard', decision='D265', budget_s=300, finalize_s=100,
        seed=0, engine_timeout_s=1800, profile_selection=example.protocol.PROFILE_SELECTION,
        engine_kwargs_sha256=object_hash(example.protocol.expanded_engine_kwargs(example.kwargs)))
    example.registration['execution'] = dict(registered_profile=record)
    example.registration_path.write_text(json.dumps(example.registration))
    example.verify()
    record['engine_kwargs_sha256'] = 'f'*64
    example.registration_path.write_text(json.dumps(example.registration))
    with pytest.raises(HarnessError, match='registered engine recipe'):
        example.verify()


@pytest.mark.parametrize('scope', ['film', 'composite', None])
def test_only_actual_pipeline_layer_is_accepted_before_guard(example, scope):
    example.data['scope'] = scope
    example.manifest.write_text(json.dumps(example.data))
    with pytest.raises(HarnessError, match='pipeline layer'):
        example.verify()
    assert not example.calls


def test_wrong_registered_arm_names_cannot_select_a_different_named_recipe(example):
    example.registration['arms']['instruction']['profile'] = 'live'
    example.registration_path.write_text(json.dumps(example.registration))
    with pytest.raises(HarnessError, match='same named standard'):
        example.verify()


def test_drift_stays_explicit_without_opening_inputs_or_invoking_profile_loader(example, monkeypatch):
    class Drift(RuntimeError):
        drifts = [dict(path='src/example.py', reason='changed')]
    def drift(*args, **kwargs):
        assert kwargs['full_weights'] is True
        raise Drift('changed')
    monkeypatch.setattr(example.protocol, '_dependencies', lambda: (drift, reject_symlinks))
    with pytest.raises(HarnessError, match='src/example.py') as caught:
        example.protocol.verify_pipeline_manifest(example.manifest, root=example.root,
            registration_path=example.registration_path,
            profile_loader=lambda name: pytest.fail('drift must precede profile loading'))
    assert caught.value.drifts == Drift.drifts


def test_sealed_input_pin_is_rejected_before_authoritative_hashing(example):
    private = 'experiments/mvp/pipeline/maps/sealed/never-open.json'
    example.data['files'][private] = 'b'*64
    example.manifest.write_text(json.dumps(example.data))
    with pytest.raises(HarnessError, match='sealed inputs'):
        example.verify()
    assert not example.calls
    assert not (example.root/private).exists()


def test_symlink_manifest_is_rejected_before_read_or_guard(example):
    link = example.manifest.with_name('linked.json')
    link.symlink_to(example.manifest.name)
    with pytest.raises(HarnessError, match='symlink'):
        example.protocol.verified_identity(link, root=example.root)
    assert not example.calls


def test_missing_lazy_guard_fails_closed_before_any_manifest_open(tmp_path, monkeypatch):
    protocol = load_protocol()
    original = protocol.importlib.import_module
    def missing(name):
        if name == 'experiments.mvp.pipeline.freeze.verify':
            raise ImportError('not yet installed')
        return original(name)
    monkeypatch.setattr(protocol.importlib, 'import_module', missing)
    with pytest.raises(HarnessError, match='require_frozen is unavailable'):
        protocol.verified_identity(tmp_path/'unopened.json', root=tmp_path)


def test_missing_pending_flag_requires_the_adopted_leader_selection(example):
    example.selection['user_authorized'] = False
    example.selection_path.write_text(json.dumps(example.selection))
    key = str(example.selection_path.relative_to(example.root))
    example.data['files'][key] = hashlib.sha256(example.selection_path.read_bytes()).hexdigest()
    example.manifest.write_text(json.dumps(example.data))
    with pytest.raises(HarnessError, match='provisional'):
        example.verify()
