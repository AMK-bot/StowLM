"""The follow-up's pending work has a finite, explicit admission contract."""
from .low_full_followup import admission_status


def test_admission_wait_expires_and_cancellation_takes_precedence():
    row = {'load1': 19., 'mask': 255, 'age_s': 1.}
    assert admission_status(row, now=10., deadline=11.) == 'ready'
    assert admission_status(row, now=11., deadline=11.) == 'wait_expired'
    assert admission_status(row, now=10., deadline=11., cancelled=True) == 'cancelled'
    assert admission_status(row, now=11., deadline=11., cancelled=True) == 'cancelled'


def test_physics_has_priority_and_busy_or_stale_readings_cannot_start():
    row = {'load1': 20., 'mask': 255, 'age_s': 0.}
    assert admission_status(row, now=10., deadline=11., released=False) == 'waiting'
    for change in ({'load1': 20.001}, {'mask': 127}, {'age_s': 120.}, {'age_s': -1.}):
        assert admission_status({**row, **change}, now=10., deadline=11.) == 'waiting'


def test_recorded_full_calls_preserve_coverage_binding_and_actual_load_labels():
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport, Instance, MovePlan
    from .benchmark import HERE, read_fixture
    from .moves import prepare_move
    from .profile_full import admitted
    data = read_fixture(HERE / 'fixtures/profile190_lowload_followup.json')
    source = read_fixture(HERE / 'fixtures/envelope190.json')
    instance, base = Instance.model_validate(source['instance']), MovePlan.model_validate(source['plan'])
    assert data['complete'] and data['status'] == 'complete'
    assert data['workers'] == 1 and data['max_admission_load1'] == 20.
    assert set(data['native_thread_caps'].values()) == {'1'}
    assert [row['kind'] for row in data['runs']] == ['base', 'swap', 'empty']
    for row in data['runs']:
        candidate = (base if row['kind'] == 'base' else
                     prepare_move(instance, base, row['box_id'], row['target_slot']).plan)
        report = CheckReport.model_validate(row['report'])
        assert report.passed and row['passed'] and row['input_binding_matches']
        assert report_matches_inputs(report, instance, candidate)
        assert report.count is not None
        assert row['all_parts_ran'] and all(part.ran and not part.error for part in report.parts.values())
        assert {'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing',
                'constraints', 'voyage'} <= set(report.parts)
        assert len({key.rsplit(':', 1)[0] for key in report.parts if key.startswith('condition:')}) == 3
        assert admitted(row['load_samples'][0], 20.)
        assert row['sustained_low_load'] == all(admitted(sample, 20.) for sample in row['load_samples'])
    assert [row['sustained_low_load'] for row in data['runs']] == [True, True, False]
