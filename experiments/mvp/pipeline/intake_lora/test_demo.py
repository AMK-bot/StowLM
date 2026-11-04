from .demo import replay_fixture


def test_recorded_mock_is_labelled_and_supplemented_from_base():
    result = replay_fixture()
    assert result.status == 'complete'
    assert len(result.rows) == 1
    assert all(r.mode.value == 'recorded' for r in result.records)
    assert all('mock' in r.model for r in result.records)
    assert 'tare_kg' in result.rows[0]
    assert next(s for s in result.field_sources if s['field'] == 'tare_kg')['document'] == 'base-container'
