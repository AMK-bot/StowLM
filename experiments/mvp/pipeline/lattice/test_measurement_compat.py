from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.lattice import measurement_compat as compat, store
from experiments.mvp.pipeline.lattice.binding_sources import runtime_source_files

FCE = 'fcecb88273afb20d55cf9edcb242f34366b3bb564de8be1e3330681ce490738f'
NINETY = '90b5a28b07bec0e8b72f2bfe962663161f5a0ed4fad4e08cd9384f1c3c413ddb'


@pytest.fixture
def audited(tmp_path, monkeypatch):
    audit = json.loads(compat.AUDIT.read_bytes())
    root = tmp_path / 'repo/experiments/mvp/pipeline/binding'
    extras = ('measurements/logic3-before.json', 'review3/init-export-delta.json', 'provenance-pins.json',
              '_logic3_activation.py', '_logic3_reporting.py', '_logic3_watchdog.py')
    for name in (*audit['canonical_runtime_files'], *extras):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compat.BINDING / name, target)
    observations = tmp_path / 'observations'
    observations.mkdir()
    monkeypatch.setattr(compat, 'OBSERVATIONS', observations)
    monkeypatch.setattr(store, 'ROOT', root.parents[3])
    return root, audit, observations


def row(before=compat.CANONICAL_SHA256, after=FCE):
    return dict(binding_source_sha256=before, binding_source_after_sha256=after, canonical_rules=[])


@pytest.mark.parametrize('before,after', [(compat.CANONICAL_SHA256, FCE), (FCE, FCE), (FCE, NINETY),
                                        (NINETY, NINETY), (NINETY, compat.CANONICAL_SHA256)])
def test_exact_historical_full_maps_normalize_without_changing_row(audited, before, after):
    root, audit, _ = audited
    assert sha256_json(audit['expanded_source_maps'][FCE]) == FCE
    assert sha256_json(audit['expanded_source_maps'][NINETY]) == NINETY
    value = row(before, after)
    original = deepcopy(value)
    proof = compat.measurement_compatibility(value, binding_root=root)
    assert proof['current_binding_source_sha256'] == compat.CANONICAL_SHA256
    assert proof['audit_sha256'] == compat.AUDIT_SHA256
    assert store._binding_source_matches(value)
    assert value == original


@pytest.mark.parametrize('name', ['__init__.py', 'adapter.py', 'allocation.py', 'review3/api.py', 'review3/hatch.py'])
def test_runtime_mutation_and_injected_deadline_import_fail_closed(audited, name):
    root, _, _ = audited
    path = root / name
    path.write_bytes(path.read_bytes() + b'\nfrom experiments.mvp.pipeline.binding.deadline import worker\n')
    assert compat.measurement_compatibility(row(), binding_root=root) is None
    assert not store._binding_source_matches(row())


def test_unknown_folders_stay_tracked_while_only_root_deadline_is_excluded(audited):
    root, audit, _ = audited
    deadline = root / 'deadline'
    deadline.mkdir()
    (deadline / '__init__.py').write_text('# measurement package\n')
    (deadline / 'worker.py').write_text('# measurement worker\n')
    assert set(runtime_source_files(root)) == set(audit['canonical_runtime_files'])
    unknown = root / 'unreviewed'
    unknown.mkdir()
    (unknown / '__init__.py').write_text('# new runtime package\n')
    (unknown / 'policy.py').write_text('# new runtime policy\n')
    assert 'unreviewed/policy.py' in runtime_source_files(root)
    assert compat.measurement_compatibility(row(), binding_root=root) is None


def _observe(files, audit, destination, **changes):
    fingerprint = sha256_json(files)
    record = dict(fingerprint=fingerprint, source_files=files, stable_double_read=True,
                  canonical_fingerprint=compat.CANONICAL_SHA256,
                  canonical_runtime_files=audit['canonical_runtime_files'], runtime_imports_deadline=False)
    record.update(changes)
    (destination / (fingerprint + '.json')).write_text(json.dumps(record))
    return fingerprint


def test_new_alias_needs_stable_exact_observation_with_only_deadline_extras(audited):
    root, audit, observations = audited
    files = dict(audit['expanded_source_maps'][NINETY])
    files['deadline/worker.py'] = '3' * 64
    fingerprint = sha256_json(files)
    assert compat.measurement_compatibility(row(fingerprint, fingerprint), binding_root=root) is None
    _observe(files, audit, observations)
    assert compat.measurement_compatibility(row(fingerprint, fingerprint), binding_root=root)
    files['unknown/policy.py'] = '4' * 64
    unsafe = _observe(files, audit, observations)
    assert compat.measurement_compatibility(row(unsafe, unsafe), binding_root=root) is None


@pytest.mark.parametrize('change', [dict(stable_double_read=False), dict(runtime_imports_deadline=True),
                                  dict(canonical_fingerprint='0' * 64), dict(canonical_runtime_files={})])
def test_bad_observation_metadata_never_qualifies(audited, change):
    root, audit, observations = audited
    files = dict(audit['expanded_source_maps'][NINETY])
    files['deadline/worker.py'] = '3' * 64
    fingerprint = _observe(files, audit, observations, **change)
    assert compat.measurement_compatibility(row(fingerprint, fingerprint), binding_root=root) is None


def test_observed_map_tamper_and_unobserved_fingerprint_reject(audited):
    root, audit, observations = audited
    files = dict(audit['expanded_source_maps'][NINETY])
    files['deadline/worker.py'] = '3' * 64
    fingerprint = _observe(files, audit, observations)
    path = observations / (fingerprint + '.json')
    record = json.loads(path.read_text())
    record['source_files']['adapter.py'] = '0' * 64
    path.write_text(json.dumps(record))
    assert compat.measurement_compatibility(row(fingerprint, fingerprint), binding_root=root) is None
    assert compat.measurement_compatibility(row('c' * 64, 'c' * 64), binding_root=root) is None


def test_official_old_nonhatch_bridge_composes_but_old_hatch_does_not(audited):
    from experiments.mvp.pipeline.lattice.binding_compat import LEGACY_SHA256, TRANSITIONAL_SHA256
    for before in (LEGACY_SHA256, TRANSITIONAL_SHA256):
        value = row(before, FCE)
        proof = store.binding_compatibility(value)
        assert proof and proof['measurement_compatibility']['current_binding_source_sha256'] == compat.CANONICAL_SHA256
        value['canonical_rules'] = [{'constraint': {'type': 'one_port_per_hatch'}}]
        assert store.binding_compatibility(value) is None
