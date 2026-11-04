from time import perf_counter

from stowage.model.fixtures import build_instance

from experiments.mvp.pipeline.runner.envelope import check


def test_fill_is_physical_cells_and_does_not_mutate():
    instance = build_instance()
    before = instance.model_dump(mode='json')
    started = perf_counter()
    result = check(instance)
    assert perf_counter() - started < 1
    assert result['in_envelope']
    assert result['numbers']['physical_cell_demand'] == 5
    assert result['numbers']['physical_cell_capacity'] == 16
    assert result['numbers']['physical_cell_fill'] == 5 / 16
    assert instance.model_dump(mode='json') == before


def test_45ft_uses_two_cells_not_two_and_quarter():
    instance = build_instance()
    instance.containers[0].length_ft = 45
    assert check(instance)['numbers']['physical_cell_demand'] == 5


def test_exact_seventy_percent_is_included():
    instance = build_instance()
    instance.vessel.cells = instance.vessel.cells[:10]
    instance.load_list = [instance.containers[0].id] * 3 + [instance.containers[1].id]
    assert check(instance)['numbers']['physical_cell_fill'] == .7
    assert check(instance)['in_envelope']
    instance.vessel.cells = instance.vessel.cells[:9]
    reason = next(row for row in check(instance)['reasons'] if row['test'] == 'fill')
    assert reason['measured'] == 7 / 9 and reason['limit'] == .7
    assert 'limit 70%' in reason['message']


def test_medium_ship_gate_uses_vessel_identity_not_family():
    instance = build_instance()
    instance.size_class = 'medium'
    instance.vessel.slice_of = 'feeder_800'
    assert check(instance)['in_envelope']
    instance.vessel.slice_of = 'feeder_1700'
    instance.family['vessel'] = 'feeder_800'
    assert not check(instance)['in_envelope']
    assert any(row['test'] == 'ship' for row in check(instance)['reasons'])
    instance.size_class = 'small'
    assert check(instance)['in_envelope']


def test_every_failure_is_reported():
    instance = build_instance(layover=True)
    instance.size_class = 'large'
    instance.load_list = [instance.load_list[0]] * 196
    instance.plan_calls = [1]
    result = check(instance)
    assert not result['in_envelope']
    assert {row['test'] for row in result['reasons']} == {
        'size', 'size_class', 'fill', 'arrival', 'discharge', 'loading_calls', 'stage'}
    assert all(set(('measured', 'limit', 'message')) <= row.keys() for row in result['reasons'])


def test_empty_cell_region_fails_without_non_json_number():
    instance = build_instance()
    instance.vessel.cells = []
    result = check(instance)
    assert not result['in_envelope']
    assert result['numbers']['physical_cell_fill'] is None


def test_empty_base_load_list_cannot_be_a_checked_voyage():
    instance = build_instance()
    instance.load_list = []
    result = check(instance)
    assert not result['in_envelope']
    assert next(row for row in result['reasons'] if row['test'] == 'load_list')['measured'] == 0


def test_full_unsupported_ship_cannot_hide_as_a_small_size_class():
    instance = build_instance()
    instance.vessel.slice_of = None
    instance.vessel.id = 'feeder_1700'
    for size in ('tiny', 'small', 'medium'):
        instance.size_class = size
        result = check(instance)
        assert not result['in_envelope']
        assert any(row['test'] == 'ship' for row in result['reasons'])
        assert result['numbers']['planned_region'] == 'full ship'


def test_genuine_tiny_and_small_sections_of_large_parent_still_pass():
    instance = build_instance()
    instance.vessel.slice_of = 'feeder_1700'
    for size in ('tiny', 'small'):
        instance.size_class = size
        result = check(instance)
        assert result['in_envelope']
        assert result['numbers']['planned_region'] == 'section'
        assert result['numbers']['section_of'] == 'feeder_1700'


def test_medium_region_and_fill_are_labelled_half_ship_section():
    instance = build_instance()
    instance.size_class = 'medium'
    instance.vessel.slice_of = 'feeder_800'
    result = check(instance)
    assert result['in_envelope']
    assert result['numbers']['planned_region'] == 'half-ship section'
    assert result['numbers']['fill_scope'] == 'physical cells in the planned region'
    instance.vessel.cells = instance.vessel.cells[:6]
    message = next(row['message'] for row in check(instance)['reasons'] if row['test'] == 'fill')
    assert 'planned half-ship section' in message
    assert '5/6' in message


def test_medium_full_ship_requires_an_actual_section():
    instance = build_instance()
    instance.size_class = 'medium'
    instance.vessel.id, instance.vessel.slice_of = 'feeder_800', None
    result = check(instance)
    assert not result['in_envelope']
    assert any(row['test'] == 'section' for row in result['reasons'])
