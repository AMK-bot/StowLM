from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

import pytest

from experiments.mvp.pipeline.lattice import binding_compat as compat, store


@pytest.fixture
def reviewed(tmp_path):
    root = tmp_path / 'repo/experiments/mvp/pipeline/binding'
    names = compat.CORE_FILES + compat.REVIEW_FILES + compat.TRANSITIONAL_FILES + (
        'measurements/logic3-before.json', 'review3/init-export-delta.json', 'provenance-pins.json')
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compat.BINDING / name, target)
    return root


def old_row(*rules):
    return dict(canonical_rules=list(rules), binding_source_sha256=compat.LEGACY_SHA256,
                binding_source_after_sha256=compat.TRANSITIONAL_SHA256, computed_utc='2026-11-02T02:20:00Z')


@pytest.mark.parametrize('origin', [compat.LEGACY_SHA256, compat.TRANSITIONAL_SHA256])
def test_reviewed_nonhatch_bridge_preserves_history(reviewed, origin):
    row = old_row({'constraint': {'type': 'dg_keep_away'}})
    row['binding_source_sha256'] = origin
    before = deepcopy(row)
    proof = compat.nonhatch_compatibility(row, binding_root=reviewed)
    assert proof and proof['planning_origin_binding_source_sha256'] == origin
    assert proof['native_checker_and_all_frame_regating_required'] is True
    assert proof['reviewed_api_sha256'] == compat.API_SHA256
    assert row == before


def test_old_hatch_and_mixed_hatch_never_qualify(reviewed):
    hatch = {'constraint': {'type': 'one_port_per_hatch'}}
    assert compat.nonhatch_compatibility(old_row(hatch), binding_root=reviewed) is None
    assert compat.nonhatch_compatibility(old_row({'constraint': {'type': 'close_bay'}}, hatch), binding_root=reviewed) is None


@pytest.mark.parametrize('name', ['adapter.py', 'allocation.py', 'hooks.py', 'admission.py',
                                 'confirmation.py', 'provenance.py', 'runtime.py',
                                 'review3/api.py', 'review3/__init__.py', 'provenance-pins.json',
                                 'measurements/logic3-before.json', 'review3/init-export-delta.json'])
def test_any_unapproved_core_wrapper_pin_or_approval_change_fails(reviewed, name):
    path = reviewed / name
    path.write_bytes(path.read_bytes() + b'\n# unapproved change\n')
    assert compat.nonhatch_compatibility(old_row(), binding_root=reviewed) is None


def test_malformed_pinned_approval_fails_closed(reviewed, monkeypatch):
    path = reviewed / 'measurements/logic3-before.json'
    path.write_text('{"source_files": {}, "identities": []}')
    monkeypatch.setattr(compat, 'APPROVAL_SHA256', store.sha256_file(path))
    assert compat.nonhatch_compatibility(old_row(), binding_root=reviewed) is None


def test_init_must_be_exact_reviewed_append(reviewed):
    path = reviewed / '__init__.py'
    path.write_bytes(path.read_bytes().replace(b'__all__ +=', b'__all__  +='))
    assert compat.nonhatch_compatibility(old_row(), binding_root=reviewed) is None


def test_unknown_runtime_and_unknown_fingerprint_fail_closed(reviewed):
    assert compat.nonhatch_compatibility(old_row() | {'binding_source_sha256': '0' * 64}, binding_root=reviewed) is None
    (reviewed / 'identity.py').write_text('# unreviewed runtime\n')
    assert compat.nonhatch_compatibility(old_row(), binding_root=reviewed) is None


def test_transitional_requires_exact_current_loader_reconstruction(reviewed):
    row = old_row() | {'binding_source_sha256': compat.TRANSITIONAL_SHA256}
    path = reviewed / compat.TRANSITIONAL_FILES[0]
    path.write_bytes(path.read_bytes() + b'\n# changed launcher\n')
    assert compat.nonhatch_compatibility(row, binding_root=reviewed) is None
    assert compat.nonhatch_compatibility(old_row() | {'binding_source_after_sha256': compat.LEGACY_SHA256}, binding_root=reviewed)


def test_fresh_gate_can_name_old_core_origin_explicitly(reviewed):
    current = compat.nonhatch_compatibility(old_row(), binding_root=reviewed)['current_binding_source_sha256']
    row = old_row() | dict(binding_source_sha256=current, binding_source_after_sha256=current,
                         planning_origin_binding_source_sha256=compat.LEGACY_SHA256)
    assert compat.nonhatch_compatibility(row, binding_root=reviewed)
    assert row['binding_source_sha256'] == current


@pytest.fixture
def historical_screen(reviewed, monkeypatch):
    root = reviewed.parents[3]
    here = reviewed.parent / 'lattice'
    source = here / 'screening/base.json'
    source.parent.mkdir(parents=True)
    proof = compat.nonhatch_compatibility(old_row(), binding_root=reviewed)
    files = {}
    for name, digest in proof['legacy_runtime_files'].items():
        path = reviewed / name
        os.utime(path, (300 if name == '__init__.py' else 100,) * 2)
        files[str(path.relative_to(root))] = dict(sha256=digest, mtime_s=100)
    started = datetime.fromtimestamp(200, timezone.utc).isoformat()
    source.with_suffix('.progress.json').write_text(json.dumps(dict(pid=42, started_utc=started)))
    source.with_suffix('.progress.source.json').write_text(json.dumps(dict(pid=42, worker_started_unix=200., source_files=files)))
    monkeypatch.setattr(store, 'ROOT', root)
    monkeypatch.setattr(store, 'HERE', here)
    row = old_row() | dict(source_screen=str(source.relative_to(root)))
    return row, reviewed, source


def test_source_screen_allows_only_approved_init_delta_after_start(historical_screen):
    row, _, _ = historical_screen
    before = deepcopy(row)
    assert store._binding_source_matches(row)
    assert row == before
    store._screen_source_guard(row)
    assert row['binding_source_sha256'] == compat.LEGACY_SHA256
    assert row['binding_source_after_sha256'] == compat.TRANSITIONAL_SHA256
    assert row['source_screen_provenance']['approved_init_export_delta'] is True


@pytest.mark.parametrize('change', ['core_mtime', 'recorded_init_mtime', 'recorded_hash'])
def test_source_screen_does_not_skip_other_timestamp_or_snapshot_checks(historical_screen, change):
    row, root, source = historical_screen
    if change == 'core_mtime':
        os.utime(root / 'adapter.py', (301, 301))
    else:
        path = source.with_suffix('.progress.source.json')
        sidecar = json.loads(path.read_text())
        recorded = sidecar['source_files']['experiments/mvp/pipeline/binding/__init__.py']
        if change == 'recorded_init_mtime':
            recorded['mtime_s'] = 201
        else:
            recorded['sha256'] = '0' * 64
        path.write_text(json.dumps(sidecar))
    assert not store._binding_source_matches(row)


@pytest.mark.parametrize('gate_passed', [False, True])
def test_approved_legacy_lookup_still_runs_and_obeys_fresh_gate(reviewed, monkeypatch, tmp_path, gate_passed):
    from experiments.mvp.pipeline.lattice import gates
    from experiments.mvp.pipeline.contract.schema import sha256_json
    monkeypatch.setattr(store, 'ROOT', reviewed.parents[3])
    monkeypatch.setattr(store, '_gated', {})
    row = old_row() | dict(instance={'synthetic': True}, instance_sha256=sha256_json({'synthetic': True}),
                          plan={'id': 'legacy-test'}, engine_version='0.13.0', engine_manifest_sha256='b' * 64,
                          status='checked_plan')
    destination = tmp_path / 'store'
    path = store.save_entry(row, store_dir=destination)
    saved_before = path.read_bytes()
    assert json.loads(saved_before)['binding_compatibility']['reviewed_api_sha256'] == compat.API_SHA256
    monkeypatch.setattr(store, '_current_token', lambda value: (sha256_json(value), 'test-epoch'))
    monkeypatch.setattr(gates, 'engine_identity', lambda: dict(passed=True, engine_version='0.13.0',
                                                           engine_manifest_sha256='b' * 64))
    calls = []
    monkeypatch.setattr(gates, 'gate_plan', lambda *args, **kwargs: calls.append(args) or
                        dict(passed=gate_passed, checker={'passed': gate_passed}, lifts=7, checked_utc='now'))
    result = store.lookup(row['instance_sha256'], [], store_dir=destination)
    if gate_passed:
        assert result['binding_compatibility']['planning_origin_binding_source_sha256'] == compat.LEGACY_SHA256
        assert result['binding_compatibility']['current_binding_source_sha256'] == store.binding_fingerprint()
        assert result['binding_source_sha256'] == compat.LEGACY_SHA256
        assert result['computed_utc'] == row['computed_utc']
    else:
        assert result is None
    assert len(calls) == 1
    assert path.read_bytes() == saved_before
