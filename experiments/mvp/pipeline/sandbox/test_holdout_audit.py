"""Independent artifact audit: the fresh cohort cannot become another fit set."""
from collections import Counter
import json

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .benchmark import HERE


FIXTURES = HERE / 'fixtures'


def recorded(name):
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f'{name} is not recorded yet')
    return json.loads(path.read_text())


@pytest.fixture(scope='module')
def source_models():
    from stowage.model import Instance, MovePlan
    source = recorded('holdout.source.json')
    return (Instance.model_validate(source['instance']), MovePlan.model_validate(source['plan']))


def test_holdout_source_and_baseline_are_independent_and_input_bound(source_models):
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport
    instance, plan = source_models
    protocol = recorded('holdout.protocol.json')
    instance_sha = sha256_json(instance.model_dump(mode='json'))
    plan_sha = sha256_json(plan.model_dump(mode='json'))
    assert protocol['seed'] == 177017
    assert protocol['instance_sha256'] == instance_sha
    assert protocol['plan_sha256'] == plan_sha
    assert all(instance_sha != row['instance_sha256'] and plan_sha != row['plan_sha256']
               for row in protocol['different_from'])
    assert protocol['target_requests'] == 40
    assert protocol['quotas'] == {'swap': 20, 'empty': 20}
    assert protocol['max_requests_per_box'] == 2
    assert 'No gates or features tuned from holdout full outcomes' in protocol['post_outcome_policy']
    baseline = recorded('holdout.baseline.json')
    report = CheckReport.model_validate(baseline['report'])
    assert report.passed and report.count is not None
    assert all(part.ran and not part.error for part in report.parts.values())
    assert report_matches_inputs(report, instance, plan)


def test_request_manifest_freezes_40_clear_distinct_states_before_outcomes():
    manifest = recorded('holdout.manifest.json')
    protocol = recorded('holdout.protocol.json')
    draw = recorded('holdout.draw.json')
    assert sha256_file(FIXTURES / 'holdout.manifest.json') == (FIXTURES / 'holdout.manifest.sha256').read_text().strip()
    for field, name in (('source_sha256', 'holdout.source.json'),
                        ('baseline_sha256', 'holdout.baseline.json'),
                        ('protocol_sha256', 'holdout.protocol.json'),
                        ('draw_sha256', 'holdout.draw.json')):
        assert manifest[field] == sha256_file(FIXTURES / name)
    assert manifest['candidate_full_checks_started'] == 0
    assert manifest['frozen_unix'] > protocol['created_unix']
    assert manifest['seed'] == protocol['seed'] == draw['seed']
    assert draw['pool_sha256'] == sha256_json(draw['pool'])
    assert draw['protocol_sha256'] == manifest['protocol_sha256']
    rows = manifest['requests']
    assert len(rows) == 40
    assert [r['request_index'] for r in rows] == list(range(40))
    assert Counter(r['kind'] for r in rows) == {'swap': 20, 'empty': 20}
    boxes = Counter(r['box_id'] for r in rows)
    assert len(boxes) >= 20 and max(boxes.values()) <= 2
    assert len({r['requested_state_sha256'] for r in rows}) == 40
    excluded = {tuple(row) for row in draw['excluded_prior_pairs']}
    for row in rows:
        assert row['frozen_fast']['legal'] and row['frozen_fast']['screen'] == 'fast'
        assert row['requested_state_sha256'] == row['reached_state_sha256']
        assert row['instance_sha256'] == manifest['instance_sha256']
        assert (row['box_id'], row['target_slot']) not in excluded
        assert draw['pool'][row['draw_index']] == [row['kind'], row['box_id'], row['target_slot']]
    # Historical snapshots remain historical; later helper edits must not be
    # misrepresented by comparing the archive to today's source files.
    for name, digest in manifest['source_sha256_files'].items():
        assert sha256_file(FIXTURES / 'holdout.gates-v3' / name) == digest


def test_primary_number_uses_only_40_fresh_postfreeze_canonical_checks(source_models):
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport
    from .moves import prepare_move
    instance, plan = source_models
    manifest = recorded('holdout.manifest.json')
    ready = recorded('holdout.readiness.json')
    results = recorded('holdout.results.json')
    if not results['complete']:
        pytest.skip('Frozen holdout full checks are still running')
    assert len(results['moves']) == 40
    assert results['manifest_sha256'] == sha256_file(FIXTURES / 'holdout.manifest.json')
    rows = {row['request_index']: row for row in results['moves']}
    assert set(rows) == set(range(40))
    misses = 0
    for frozen in manifest['requests']:
        row = rows[frozen['request_index']]
        assert row['manifest_sha256'] == results['manifest_sha256']
        assert row['full_origin'] == 'fresh_canonical_call'
        assert row['canonical_invocations'] == 1
        assert row['started_unix'] > manifest['frozen_unix']
        assert row['canonical_call']['started_unix'] > manifest['frozen_unix']
        assert row['canonical_call']['started_unix'] > ready['created_unix']
        assert row['session']['readiness_sha256'] == sha256_file(FIXTURES / 'holdout.readiness.json')
        assert row['session']['source_sha256'] == ready['source_sha256']
        assert row['session']['runtime_gate_code_sha256'] == manifest['runtime_gate_code_sha256']
        assert row['frozen_fast_legal'] and row['runtime_fast_agrees_with_frozen_legal']
        assert row['fast']['legal']
        assert row['kind'] == frozen['kind']
        assert (row['box_id'], row['target_slot']) == (frozen['box_id'], frozen['target_slot'])
        prepared = prepare_move(instance, plan, row['box_id'], row['target_slot'])
        assert prepared.replay.legal
        assert sha256_json(prepared.plan.model_dump(mode='json')) == frozen['candidate_plan_sha256']
        assert row['canonical_call']['plan_sha256'] == frozen['candidate_plan_sha256']
        assert row['canonical_call']['instance_sha256'] == manifest['instance_sha256']
        assert row['canonical_call']['input_binding_valid']
        report = CheckReport.model_validate(row['canonical_call']['report'])
        assert report_matches_inputs(report, instance, prepared.plan)
        assert {'moves', 'rules', 'voyage', 'stability', 'strength', 'visibility',
                'lashing', 'constraints'} <= set(report.parts)
        if report.passed:
            assert all(part.ran and not part.error for part in report.parts.values())
        elif any(not part.ran or part.error for part in report.parts.values()):
            # A canonical numerical failure is a real false-clear, not a row
            # to discard or replace; the checker must fail closed for it.
            assert any(violation.severity == 'hard' for violation in report.violations)
        assert report.count is not None
        assert row['full']['legal'] == report.passed
        assert row['full']['gauges']['lift_counts']['candidate_now'] == report.count.total_lifts_now
        misses += not report.passed
    assert results['summary']['fresh_canonical_checks'] == 40
    assert results['summary']['frozen_screen_clear'] == 40
    assert results['summary']['false_legal'] == misses
    assert results['summary']['false_legal_fraction'] == misses / 40


def test_guard_repair_preserves_the_original_requests_and_dates_new_attribution():
    manifest = recorded('holdout.manifest.json')
    audit = recorded('holdout.marshal-audit.json')
    preservation = recorded('holdout.recovery-preservation.json')
    proof = recorded('holdout.recovery-proof.json')
    ready = recorded('holdout.readiness.json')
    requests_sha = sha256_json(manifest['requests'])
    assert requests_sha == audit['preserved_requests_sha256']
    assert requests_sha == preservation['requests_sha256'] == proof['preserved_requests_sha256']
    assert audit['marshal_changed'] and audit['same_function_and_code'] and audit['same_constant_object']
    assert audit['all_archives_match_recorded'] and audit['all_current_match_recorded']
    assert all(audit['actual_gate_code_equals_archived_recompile'].values())
    assert preservation['candidate_full_checks_started'] == proof['candidate_full_checks_started'] == 0
    assert manifest['recovery']['preservation_sha256'] == sha256_file(FIXTURES / 'holdout.recovery-preservation.json')
    assert manifest['recovery']['proof_sha256'] == sha256_file(FIXTURES / 'holdout.recovery-proof.json')
    assert ready['created_unix'] > manifest['frozen_unix']
    assert ready['candidate_full_checks_started'] == 0
    assert ready['manifest_sha256'] == sha256_file(FIXTURES / 'holdout.manifest.json')
    assert ready['source_fingerprint'] == sha256_json(ready['source_sha256'])
    assert ready['runtime_gate_code_sha256'] == manifest['runtime_gate_code_sha256']
    assert {'src/stowage/vessel/hydrostatics.py', 'src/stowage/checker/full.py',
            'src/stowage/frozen.py', 'params/fleet/feeder_800.toml'} <= set(ready['source_sha256'])
