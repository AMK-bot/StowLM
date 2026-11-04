"""The profiling recorder must not mislabel stale/busy readings as low load."""
from .profile_full import _comparable, admitted


def test_loaded_profile_records_canonical_coverage_and_bound_count():
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport, Instance, MovePlan
    from .benchmark import HERE, read_fixture
    from .moves import prepare_move
    data = read_fixture(HERE / 'fixtures/profile190_loaded.json')
    source = read_fixture(HERE / 'fixtures/envelope190.json')
    instance = Instance.model_validate(source['instance'])
    plan = MovePlan.model_validate(source['plan'])
    candidate = prepare_move(instance, plan, data['box_id'], data['target_slot']).plan
    report = CheckReport.model_validate(data['report'])
    assert data['complete'] and data['measurement']['profiled']
    assert data['measurement']['passed'] and report.passed
    assert report_matches_inputs(report, instance, candidate)
    assert not report_matches_inputs(report, instance, plan)
    assert data['measurement']['count'] == report.count.model_dump(mode='json')
    assert data['measurement']['all_parts_ran']
    assert all(part.ran for part in report.parts.values())
    assert {'moves', 'rules', 'voyage', 'stability', 'strength', 'visibility',
            'lashing', 'constraints'} <= set(report.parts)
    conditions = {key.rsplit(':', 1)[0] for key in report.parts if key.startswith('condition:')}
    assert len(conditions) == 3
    for condition in conditions:
        assert all(f'{condition}:{part}' in report.parts for part in (
            'stability', 'strength', 'visibility', 'lashing', 'constraints'))
    for name in ('profile_full.py', 'full.py'):
        assert len(data['source_sha256'][name]) == 64


def test_loaded_attribution_is_calculated_from_recorded_profile():
    from .benchmark import HERE, read_fixture
    data = read_fixture(HERE / 'fixtures/profile190_loaded.json')
    summary = read_fixture(HERE / 'fixtures/profile190_attribution.json')
    measurement = data['measurement']
    by_name = {row['function']: row for row in measurement['profile']['checker_part_functions']}
    for row in summary['part_costs']:
        raw = by_name[row['function']]
        assert row['cumulative_s'] == raw['cumulative_s']
        assert row['fraction_of_profiled_wall'] == raw['cumulative_s'] / measurement['wall_s']
    assert summary['wall_s'] == measurement['wall_s']
    assert summary['mode'] == 'recorded'
    assert 'not uninstrumented' in summary['label']


def test_low_load_admission_requires_fresh_capacity_and_declared_threshold():
    good = {'mask': 255, 'load1': 20., 'age_s': 0.}
    assert admitted(good, 20.)
    assert not admitted({**good, 'load1': 20.01}, 20.)
    assert not admitted({**good, 'mask': 127}, 20.)
    assert not admitted({**good, 'age_s': 120.}, 20.)
    assert not admitted({**good, 'age_s': -1.}, 20.)
    assert not admitted({'error': 'missing telemetry'}, 20.)


def test_report_comparison_preserves_all_verdict_evidence():
    class Report:
        def model_dump(self, *, mode):
            assert mode == 'json'
            return {'provenance': {'created_utc': 'then'}, 'passed': False,
                    'parts': {'rules': {'ran': True}}, 'count': {'total_lifts_now': 5},
                    'violations': [{'rule_id': 'example', 'measured': 2., 'limit': 1.}]}

    assert _comparable(Report()) == {
        'passed': False, 'parts': {'rules': {'ran': True}}, 'count': {'total_lifts_now': 5},
        'violations': [{'rule_id': 'example', 'measured': 2., 'limit': 1.}]}
