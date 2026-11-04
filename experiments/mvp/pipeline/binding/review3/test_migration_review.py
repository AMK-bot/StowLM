"""Migration boundaries without installing live patches or creating workers."""
from copy import deepcopy
import pickle
import sys
from types import ModuleType

import pytest

from experiments.mvp.pipeline.binding.review3 import measurement, campaign


@pytest.fixture
def scope(monkeypatch):
    value = dict(version=3, core_source_files={'adapter.py': 'approved'},
                 planning_functions={'measure.profile': 'approved'},
                 planning_callables={'adapter.plan_with_rules': 'approved'},
                 export_delta_sha256='approved-export-delta', approved_core=True)
    monkeypatch.setattr(measurement, 'core_scope', lambda: deepcopy(value))
    monkeypatch.setattr(measurement, 'hatch_policy', lambda: {'sha256': 'portfolio-current'})
    return value


def make_identity(tmp_path, scope=None, *, kind='close_bay', implementation=None):
    source = tmp_path / 'public-dev.json'
    if not source.exists():
        source.write_text('{"synthetic identity test": true}')
    if implementation is None:
        implementation = {'source_files': {'adapter.py': 'approved'},
                          'planning_scope': deepcopy(scope), 'loaded_code_sha256': 'reporting-before'}
    return measurement.ORIGINAL_BUILD(input_path=source, cell='medium', profile='live-v2',
        rule={'schema_version': 'stowlm.reader/2', 'constraint': {'type': kind}},
        settings={'budget_s': 90., 'config': {'same': True}},
        engine_identity={'passed': True, 'manifest_sha256': 'pinned-native'},
        implementation=implementation)


@pytest.mark.parametrize('kind', ['close_bay', 'max_tiers', 'dg_keep_away'])
def test_approved_nonhatch_core_keeps_rows_despite_reporting_fingerprint_changes(tmp_path, scope, kind):
    old = make_identity(tmp_path, scope, kind=kind)
    expected = deepcopy(old)
    expected['payload']['implementation']['loaded_code_sha256'] = 'reporting-after'
    expected['payload']['implementation']['source_files']['measurement.py'] = 'approved-migration-reporting'
    expected['sha256'] = measurement.identity.digest(expected['payload'])
    assert measurement.compatibility({'measurement_identity': old}, expected) == (True, [])


@pytest.mark.parametrize('passed', [False, True])
def test_old_hatch_is_stale_even_when_its_identity_was_computed_after_activation(tmp_path, scope, passed):
    current_scope = dict(scope, hatch_policy=measurement.hatch_policy())
    expected = make_identity(tmp_path, current_scope, kind='one_port_per_hatch')
    # An in-flight old function could acquire the new fingerprint while still
    # executing the old single-mapping implementation. Identity equality alone
    # must never promote its pass or failure to portfolio evidence.
    row = {'passed': passed, 'measurement_identity': deepcopy(expected), 'evidence': {}}
    valid, reasons = measurement.compatibility(row, expected)
    assert not valid and 'did not execute' in reasons[0]
    row['evidence']['portfolio_policy_version'] = measurement.POLICY_VERSION
    assert measurement.compatibility(row, expected) == (True, [])


def test_old_hatch_policy_cannot_be_relabelled_by_an_evidence_version(tmp_path, scope):
    old_scope = dict(scope, hatch_policy={'sha256': 'old-policy'})
    old = make_identity(tmp_path, old_scope, kind='one_port_per_hatch')
    expected = make_identity(tmp_path, dict(scope, hatch_policy=measurement.hatch_policy()),
                             kind='one_port_per_hatch')
    row = {'measurement_identity': old, 'evidence': {'portfolio_policy_version': measurement.POLICY_VERSION}}
    assert not measurement.compatibility(row, expected)[0]


@pytest.mark.parametrize('changed', ['input_sha256', 'resolved_settings', 'engine', 'seed'])
def test_migration_never_ignores_input_budget_engine_or_seed_changes(tmp_path, scope, changed):
    old = make_identity(tmp_path, scope)
    expected = deepcopy(old)
    expected['payload'][changed] = {'changed': True}
    expected['sha256'] = measurement.identity.digest(expected['payload'])
    valid, reasons = measurement.compatibility({'measurement_identity': old}, expected)
    assert not valid and changed + ' changed' in reasons


def test_synthetic_identities_retain_exact_loaded_code_and_source_comparison(monkeypatch, tmp_path):
    old = make_identity(tmp_path, implementation={'source_sha256': 'before', 'loaded_code_sha256': 'before'})
    expected = make_identity(tmp_path, implementation={'source_sha256': 'after', 'loaded_code_sha256': 'after'})
    monkeypatch.setattr(measurement, 'core_scope', lambda: pytest.fail('synthetic identities entered migration'))
    assert not measurement.compatibility({'measurement_identity': old}, expected)[0]
    assert measurement.compatibility({'measurement_identity': old}, old) == (True, [])


def test_in_place_run_map_dispatch_preserves_captured_and_pickle_identity(monkeypatch):
    installed_before = campaign._INSTALLED
    module = ModuleType('_binding_review3_pickle_identity_test')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec('def run_map(job, output):\n    return ("old", job, output)', module.__dict__)
    original = module.run_map
    captured = original
    module._review3_original_run_map = measurement.clone(original)
    seen = []
    def handler(job, output, original_function):
        seen.append((job, output))
        return original_function(job, output)
    campaign.replace_code(original, campaign._run_map_shim, '_review3_run_map_handler', handler)
    assert captured is module.run_map and captured.__name__ == 'run_map'
    assert pickle.loads(pickle.dumps(captured)) is captured
    assert captured('job', 'out') == ('old', 'job', 'out') and seen == [('job', 'out')]
    assert campaign._INSTALLED is installed_before  # Never activates live code.


def test_supervisor_refresh_context_cannot_acquire_numerical_drain_work(monkeypatch, tmp_path):
    monkeypatch.setattr(campaign, '_DRAINING', False)
    monkeypatch.setattr(campaign, 'in_worker_boundary', lambda: False)
    monkeypatch.setattr(campaign, 'real_manifest', lambda _: {'workers': 3})
    monkeypatch.setattr(campaign, 'next_work', lambda _: pytest.fail('supervisor claimed work'))
    campaign.drain(tmp_path)
