"""D270 launch guard; synthetic manifests and no sealed/map/planner access."""
import csv
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import ModuleType

import pytest

from experiments.mvp.pipeline.sealed import common, driver, report

# The active native study pins canonical launch.py. Test the exact staged
# replacement until its detached installer can deploy after that study exits.
STAGED = Path(__file__).parent/'launch.py'
spec = importlib.util.spec_from_file_location('sealed_launch_d270', STAGED)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class FreezeDrift(RuntimeError):
    def __init__(self, drifts):
        self.drifts = drifts
        super().__init__('pipeline changed')


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(launch, 'wait_for_drain', lambda: None)
    monkeypatch.setattr(launch, 'check', lambda *_a, **_k: [])
    monkeypatch.setattr(launch, 'admission_bridge', lambda *_a, **_k: nullcontext())
    manifest = tmp_path/'manifest.json'
    manifest.write_text('{}\n')
    identity = dict(manifest_sha256=common.sha256(manifest), tree_digest='b'*64)
    module = ModuleType('experiments.mvp.pipeline.freeze.verify')
    module.FreezeDrift = FreezeDrift
    calls = []
    def require(path, *, full_weights=False):
        assert full_weights is True
        calls.append(Path(path))
        return dict(identity)
    module.require_frozen = require
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return manifest, identity, module, calls


def test_lazy_module_import_does_not_require_unlanded_freeze_api(monkeypatch):
    original = importlib.import_module
    def imported(name, *args, **kwargs):
        if name == 'experiments.mvp.pipeline.freeze.verify':
            pytest.fail('imported freeze verifier before launch')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(importlib, 'import_module', imported)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_fake_drift_exits_two_without_any_sealed_open_or_claim(frozen, monkeypatch, capsys):
    manifest, _, module, _ = frozen
    drifts = [dict(path='src/changed.py', reason='changed'), dict(path='adapter', reason='missing')]
    module.require_frozen = lambda *_a, **_k: (_ for _ in ()).throw(FreezeDrift(drifts))
    claim = manifest.parent/'SEALED-CLAIM.json'
    monkeypatch.setattr(driver, 'GLOBAL_CLAIM', claim)
    monkeypatch.setattr(launch, 'release', lambda *_: pytest.fail('release reached'))
    monkeypatch.setattr(driver, 'declared_sealed_maps', lambda: pytest.fail('sealed metadata reached'))
    from experiments.mvp.pipeline.sealed import worker
    monkeypatch.setattr(worker, '_load', lambda *_a, **_k: pytest.fail('sealed map opened'))
    read = Path.open
    def guarded(path, *args, **kwargs):
        if driver.is_sealed(path) or path.name in {'SEALED-CLAIM.json', 'STARTED.json'}:
            pytest.fail('sealed path or claim opened: '+str(path))
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    monkeypatch.setattr(sys, 'argv', ['launch', '--manifest', str(manifest)])
    with pytest.raises(SystemExit) as error:
        launch.main()
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert json.dumps(drifts, sort_keys=True) in stderr
    assert not claim.exists()


def test_unlanded_require_frozen_fails_closed(frozen):
    manifest, _, module, _ = frozen
    del module.require_frozen
    with pytest.raises(common.HarnessError, match='require_frozen is unavailable'):
        launch.launch(manifest)


def test_missing_manifest_spends_no_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, 'require_frozen', lambda _: pytest.fail('missing manifest must block first'))
    with pytest.raises(common.HarnessError, match='manifest is absent'):
        launch.launch(tmp_path/'missing.json')


def test_manifest_must_not_be_a_sealed_input_before_guard(frozen, monkeypatch):
    manifest, _, _, calls=frozen
    monkeypatch.setattr(driver,'is_sealed',lambda _:True)
    with pytest.raises(common.HarnessError,match='sealed input or result'):
        launch.require_frozen(manifest)
    assert calls==[]


def test_changed_manifest_or_malformed_identity_refuses(frozen):
    manifest, identity, module, _ = frozen
    identity['tree_digest'] = 'invalid'
    with pytest.raises(common.HarnessError, match='invalid frozen identity'):
        launch.require_frozen(manifest)
    identity['tree_digest'] = 'b'*64
    manifest.write_text('{"changed":true}')
    with pytest.raises(common.HarnessError, match='manifest changed after verification'):
        launch.require_frozen(manifest)


def test_shell_preflight_checks_before_any_study_read(frozen, monkeypatch):
    manifest, _, _, calls = frozen
    monkeypatch.setattr(launch, 'read_json', lambda _: pytest.fail('preflight read a study or map'))
    monkeypatch.setattr(sys, 'argv', ['launch', '--manifest', str(manifest), '--check-only'])
    launch.main()
    assert calls == [manifest]


def test_shell_returns_drift_exit_two_before_detaching(tmp_path):
    manifest=tmp_path/'manifest.json'
    manifest.write_text('{}\n')
    scripts=tmp_path/'bin'
    scripts.mkdir()
    calls=tmp_path/'calls.txt'
    uv=scripts/'uv'
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> '+shlex.quote(str(calls))+
                  '\nprintf "%s\\n" "Frozen pipeline drift: fake changed.py" >&2\nexit 2\n')
    uv.chmod(0o755)
    env=dict(os.environ,PATH=str(scripts)+os.pathsep+os.environ['PATH'])
    result=subprocess.run(['sh',str(Path(__file__).with_name('run_sealed.sh')),str(manifest)],
                          capture_output=True,text=True,env=env,timeout=10)
    assert result.returncode==2 and 'fake changed.py' in result.stderr
    commands=calls.read_text().splitlines()
    assert len(commands)==1 and '--check-only' in commands[0]
    assert '-c ' not in commands[0]  # No detached launcher invocation.


def test_p21_authority_replaces_legacy_manifest_and_source_checks(frozen, monkeypatch):
    manifest, identity, _, _ = frozen
    data = dict(schema_version='stowlm.pipeline-freeze/1', scope='pipeline',
                tree_sha256=identity['tree_digest'])
    files = {str(manifest.parent/'runs/public-evidence.json'):'c'*64}
    module = ModuleType('experiments.mvp.pipeline.sealed.fixtures.d275.protocol')
    calls=[]
    def verify(path):
        calls.append(path)
        return data, files
    module.verify_pipeline_manifest=verify
    monkeypatch.setitem(sys.modules,module.__name__,module)
    imported=launch.importlib.import_module
    def protocol_module(name,*args,**kwargs):
        if name in {module.__name__,'experiments.mvp.pipeline.sealed.protocol'}:
            return module
        return imported(name,*args,**kwargs)
    monkeypatch.setattr(launch.importlib,'import_module',protocol_module)
    monkeypatch.setattr(driver, 'source_inventory', lambda: pytest.fail('legacy source census reached'))
    monkeypatch.setattr(driver, 'normalize_files', lambda _: pytest.fail('legacy runs-path ban reached'))
    got, pins = launch._manifest(manifest, identity)
    assert got == data and pins == files
    assert calls==[manifest]
    assert 'task' not in got and 'standard_profile' not in got
    data['tree_sha256']='d'*64
    with pytest.raises(common.HarnessError,match='identity changed'):
        launch._manifest(manifest, identity)


def sample_row(status='no_checked_plan'):
    return dict(map_id='synthetic', map_sha256='c'*64, cohort='tiny15', arm='pipeline',
                status=status, passed=False, full_checker_pass=False, ship_id='feeder_500')


def test_raw_rows_bind_identity_before_audit_preserving_original_bytes(frozen, tmp_path, monkeypatch):
    manifest, identity, _, _ = frozen
    out = tmp_path/'job'
    out.mkdir()
    def child(*_args, **_kw):
        common.atomic_json(out/'row.json', sample_row())
        return sample_row()
    monkeypatch.setattr(driver, 'run_child', child)
    before = dict(driver=driver.run_child, write=driver.atomic_json, report=report.write_report)
    with launch._release_context(manifest, identity):
        result = driver.run_child({}, out)
        raw = common.read_json(out/'row.json')
        assert all(result[key] == raw[key] == value for key, value in identity.items())
        assert raw['worker_original_sha256'] == common.sha256(out/'worker-row.original.json')
        assert common.read_json(out/'worker-row.original.json') == sample_row()
        common.atomic_json(out/'audit-row.json', dict(raw, audited_target_sha256=common.sha256(out/'row.json')))
    assert common.read_json(out/'audit-row.json')['audited_target_sha256'] == common.sha256(out/'row.json')
    assert (driver.run_child, driver.atomic_json, report.write_report) == (before['driver'], before['write'], before['report'])


def test_prepass_is_not_modified_and_all_error_rows_and_reports_carry_identity(frozen, tmp_path, monkeypatch):
    manifest, identity, _, _ = frozen
    out = tmp_path/'report'
    out.mkdir()
    monkeypatch.setattr(driver, 'run_child', lambda *_a, **_kw: [dict(map_id='prepass')])
    rows = [sample_row('error')]
    metadata = dict(mode='sealed', expected_maps=[dict(map_id='synthetic', cohort='tiny15', ship_id='feeder_500')],
                    complete=False, failure_reason='Injected audit failure')
    with launch._release_context(manifest, identity):
        assert driver.run_child({}, out, prepare=True) == [dict(map_id='prepass')]
        driver.atomic_json(out/'result-row.json', rows[0])
        driver.atomic_json(out/'rows.json', rows)
        final = report.write_report(rows, out, metadata=metadata)
    for row in (common.read_json(out/'result-row.json'), common.read_json(out/'rows.json')[0]):
        assert all(row[k] == v for k, v in identity.items())
    for value in (final, final['metadata'], common.read_json(out/'report.json')):
        assert all(value[k] == v for k, v in identity.items())
    assert final['provisional']
    csv_row = next(csv.DictReader((out/'per-map.csv').open()))
    assert all(csv_row[k] == v for k, v in identity.items())
    assert all(v in (out/'REPORT.md').read_text() for v in identity.values())


def test_row_identity_conflicts_and_manifest_substitution_refuse(frozen, tmp_path):
    manifest, identity, _, _ = frozen
    with pytest.raises(common.HarnessError, match='different frozen identity'):
        launch._tag(dict(sample_row(), tree_digest='d'*64), identity)
    with launch._release_context(manifest, identity):
        with pytest.raises(common.HarnessError, match='substitute'):
            driver.verify_pipeline_manifest(tmp_path/'other-manifest.json')


def test_release_uses_original_p21_path_identity_and_no_legacy_schema(frozen, tmp_path, monkeypatch):
    manifest, identity, _, calls = frozen
    study = tmp_path/'study'
    study.mkdir()
    common.atomic_json(study/'launch-config.json', dict(mode='sealed'))
    monkeypatch.setattr(launch, '_manifest', lambda path, pinned: (dict(schema_version='stowlm.pipeline-freeze/1'), {}))
    monkeypatch.setattr(driver, 'validate_release_support', lambda *_: None)
    def release(config, out):
        assert calls == [manifest]
        assert config['pipeline_manifest'] == str(manifest)
        assert all(config[key] == value for key, value in identity.items())
        return dict(accepted=True)
    monkeypatch.setattr(launch, 'release', release)
    assert launch.launch(manifest, study=study, out_dir=tmp_path/'final') == dict(accepted=True)
