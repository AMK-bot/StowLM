"""Code5 P1: neither a refreshed manifest nor a late identity can certify release."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.binding import adapter
from experiments.mvp.pipeline.binding import provenance
from experiments.mvp.pipeline.binding.test_adapter import Candidate, _plan, _verdict
from experiments.mvp.pipeline.binding.test_hooks import fixture_instance


@pytest.mark.parametrize('change', ['remove_native', 'rewrite_native', 'rewrite_engine'])
def test_manifest_contents_cannot_redefine_the_frozen_release(monkeypatch, change):
    native = adapter.ENGINE/'native-source-manifest.json'
    read_bytes, read_text = Path.read_bytes, Path.read_text
    changed_file = None
    changed_bytes = b'changed native checker source with a matching refreshed manifest'
    if change == 'remove_native':
        path, replacement = native, b'{}'
    elif change == 'rewrite_native':
        current = json.loads(read_bytes(native))
        name = next(iter(current))
        changed_file = adapter.ROOT/name
        current[name] = hashlib.sha256(changed_bytes).hexdigest()
        path, replacement = native, json.dumps(current).encode()
    else:
        current = json.loads(read_bytes(adapter.MANIFEST))
        current['unreviewed_metadata'] = 'rewritten manifest with unchanged engine digest'
        path, replacement = adapter.MANIFEST, json.dumps(current).encode()
    monkeypatch.setattr(Path, 'read_bytes', lambda self: replacement if self == path
        else changed_bytes if self == changed_file else read_bytes(self))
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **kw:
        replacement.decode() if self == path else read_text(self, *a, **kw))
    identity = adapter.verify_manifest()
    assert not identity['passed']
    assert identity['source_files'] == 245 and identity['native_files'] == 257
    assert identity['errors']


def test_native_file_is_still_checked_when_its_manifest_entry_is_removed(monkeypatch):
    native = adapter.ENGINE/'native-source-manifest.json'
    original = Path.read_bytes
    frozen = json.loads(original(native))
    name = next(iter(frozen))
    target = adapter.ROOT/name
    def altered(path):
        if path == native:
            return b'{}'
        if path == target:
            return b'drift hidden by truncated manifest'
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', altered)
    identity = adapter.verify_manifest()
    assert not identity['passed'] and name in identity['changed_native']


def test_frozen_pin_configuration_is_itself_digest_checked(monkeypatch):
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path:
        b'{}' if path == provenance.PINS else original(path))
    identity = adapter.verify_manifest()
    assert not identity['passed']
    assert any('pin configuration digest mismatch' in error for error in identity['errors'])


def _identity():
    return dict(passed=True, manifest_sha256='engine', native_manifest_sha256='native',
        source_tree_sha256='tree', pins_sha256='pins', engine_file_map_sha256='enginefiles',
        native_file_map_sha256='nativefiles', identity_sha256='identity', source_files=245,
        native_files=257)


def _cheap_release(monkeypatch):
    instance = fixture_instance()
    plan = _plan(instance)
    report, row = _verdict(True)
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {})
    monkeypatch.setattr(adapter, 'require_clean', lambda *a, **kw: None)
    monkeypatch.setattr(adapter, 'check', lambda *a: report)
    monkeypatch.setattr(adapter, 'inspect', lambda *a: row)
    return instance, plan, report


def test_initial_failed_native_identity_stops_before_search(monkeypatch):
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: dict(_identity(), passed=False))
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *a, **kw: pytest.fail('search reached'))
    result, evidence = adapter.plan_with_rules(fixture_instance(), [])
    assert result is None and evidence['status'] == 'constraint_unavailable'


@pytest.mark.parametrize('field', ['manifest_sha256', 'native_manifest_sha256', 'source_tree_sha256', 'identity_sha256'])
def test_final_release_requires_identical_initial_identity_even_if_both_pass(monkeypatch, field):
    instance, plan, _ = _cheap_release(monkeypatch)
    identities = iter([_identity(), dict(_identity(), **{field: 'new identity'})])
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: next(identities))
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *a, **kw:
        (Candidate(status='ok', plan=plan), {}))
    result, evidence = adapter.plan_with_rules(instance, [])
    assert result is None and evidence['status'] == 'constraint_unavailable'
    assert not evidence['plan_released'] and not evidence['passed']


def test_incumbent_is_not_published_under_changed_native_identity(monkeypatch):
    import stowage.checker as checker
    instance, plan, report = _cheap_release(monkeypatch)
    report.model_dump_json = lambda: '{}'
    monkeypatch.setattr(checker, 'check', lambda *a: report)
    identities = iter([_identity(), dict(_identity(), native_manifest_sha256='drift'), _identity()])
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: next(identities))
    def run(original, **kwargs):
        checker.check(original, plan)
        return None, {}
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', run)
    events = []
    result, evidence = adapter.plan_with_rules(instance, [], on_incumbent=events.append)
    assert result is None and events == []
    assert evidence['rejected_publications'][0]['identity']['native_manifest_sha256'] == 'drift'
