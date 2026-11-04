from copy import deepcopy
import json

import pytest

from experiments.mvp.hybrid import admission


@pytest.mark.parametrize('load,mask,age,expected', [
    (45, 975, 0, True), (45, 255, 119.999, True), (0, 262143, 1, True),
    (45.001, 4095, 1, False), (-.01, 4095, 1, False), (True, 4095, 1, False),
    (float('nan'), 4095, 1, False), (float('inf'), 4095, 1, False),
    (20, 1 << 30, 1, False), (20, 127, 1, False), (20, -1, 1, False),
    (20, 255., 1, False), (20, True, 1, False), (20, '255', 1, False),
    (20, 4095, 120, False), (20, 4095, 120.001, False), (20, 4095, -.001, False),
])
def test_threshold_popcount_and_freshness(tmp_path, load, mask, age, expected):
    state = dict(mask=mask, load1=load, t=1000-age, leader_note='exact extra evidence')
    path = tmp_path / 'core-state.json'
    path.write_text(json.dumps(state))
    row = admission.reading(path=path, now=1000)
    assert row['allowed'] is expected
    assert admission.RULE['workers'] == 4
    if row['raw'] is None:
        assert not expected and row.get('error')
        return
    assert row['raw']['leader_note'] == state['leader_note']
    assert row['t'] == state['t']
    assert abs(row['age_s']-age) < 1e-10
    assert row['source_path'] == str(path.resolve())
    if expected:
        assert row['core_count'] == mask.bit_count()
        assert admission.evidence_errors(row, expected_source_path=path) == []
    else:
        assert row.get('error')


@pytest.mark.parametrize('text', ['not JSON', 'null', '[]', '{}',
    '{"mask":255,"load1":20,"t":true}', '{"mask":255,"load1":20,"t":"epoch"}',
    '{"mask":255,"load1":20,"t":NaN}', '{"mask":255,"load1":20,"t":Infinity}',
    '{"mask":255,"load1":20,"t":1000,"extra":NaN}',
    '{"mask":1,"mask":255,"load1":20,"t":1000}'])
def test_malformed_missing_and_duplicate_readings_fail_closed(tmp_path, text):
    path = tmp_path / 'core-state.json'
    path.write_text(text)
    row = admission.reading(path=path, now=1000)
    assert row['allowed'] is False and row.get('error')


def test_missing_file_fails_closed_without_kernel_or_local_load_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(admission.os, 'getloadavg', lambda: pytest.fail('local load probe used'))
    row = admission.reading(path=tmp_path / 'missing.json', now=1000)
    assert row['allowed'] is False and row['core_mask'] is None and row['core_count'] is None
    assert 'FileNotFoundError' in row['error']


def test_derived_evidence_is_checked_against_exact_source_state_and_historical_time():
    row = admission.state_evidence(dict(mask=975, load1=45, t=1000), observed_at=1001)
    assert admission.evidence_errors(row) == []  # Reporting many years later is allowed.
    for key, value in (('core_mask', 975.), ('core_count', 9), ('load_1m', 0), ('t', 999),
                       ('age_s', 120), ('source_path', '/foreign/core-state.json'),
                       ('utc', 'malformed'), ('observed_at', 1002), ('allowed', False)):
        broken = deepcopy(row)
        broken[key] = value
        assert admission.evidence_errors(broken), key


def test_wait_preserves_rule_and_honors_stop():
    readings = iter([dict(allowed=False),dict(allowed=True)])
    log = []
    result = admission.wait_for_admission(stopped=lambda:False, log=lambda *a, **k:log.append(k),
                                          poll_s=0, sample=lambda:next(readings))
    assert result['allowed'] and len(log) == 2
    assert admission.wait_for_admission(stopped=lambda:True, log=lambda *a, **k:None) is None


def test_stop_during_probe_prevents_admission():
    state = dict(stop=False)
    def sample():
        state['stop'] = True
        return dict(allowed=True)
    assert admission.wait_for_admission(stopped=lambda:state['stop'],log=lambda *a,**k:None,
                                          poll_s=0,sample=sample) is None
