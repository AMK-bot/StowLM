"""Offline audit of expanded screen-clear development measurements."""
import json
import math
from pathlib import Path
import pytest

from experiments.mvp.pipeline.contract.schema import MoveVerdict, sha256_file, sha256_json
from experiments.mvp.pipeline.sandbox.clear_benchmark import placements_hash, summary
from experiments.mvp.pipeline.sandbox import clear_benchmark
from stowage.conventions import cells_of_slot
from stowage.model import Instance, MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).parent


def test_clear_cohort_runs_distinct_real_drags_to_requested_states():
    data = json.loads((HERE/'fixtures/clear190.json').read_text())
    source = HERE/'fixtures'/data['source_fixture']
    assert data['complete'] and data['discovery_complete'] and data['n_boxes'] == 190
    assert sha256_file(source) == data['source_sha256']
    fixture = json.loads(source.read_text())
    instance = Instance.model_validate(fixture['instance'])
    plan = MovePlan.model_validate(fixture['plan'])
    ship = apply_plan(instance, plan).final_state.ship
    base_hash = placements_hash(ship)
    source_slots = {p.container_id: p.slot for p in ship.placements}
    occupied = {c for p in ship.placements for c in cells_of_slot(p.slot)}
    rows = data['moves']
    assert len(rows) >= 40
    assert len({(r['box_id'], r['target_slot']) for r in rows}) == len(rows)
    assert len({r['requested_state_sha256'] for r in rows}) >= 40
    assert len({r['box_id'] for r in rows}) >= 20
    for source_name in ('moves.py', 'physics.py'):
        assert len({r['code_sha256'][source_name] for r in rows}) == 1
    empties = [r for r in rows if r['kind'] == 'empty']
    assert all(sum(r['box_id'] == box for r in empties) <= 2 for box in {r['box_id'] for r in empties})
    for kind in ('empty', 'swap'):
        assert sum(r['kind'] == kind for r in rows) >= 10
    for row in rows:
        assert source_slots[row['box_id']] != row['target_slot']
        assert row['requested_state_sha256'] == row['reached_state_sha256'] != base_hash
        assert bool(set(cells_of_slot(row['target_slot'])) & occupied) == (row['kind'] == 'swap')
        fast, full = MoveVerdict.model_validate(row['fast']), MoveVerdict.model_validate(row['full'])
        assert fast.legal and fast.screen == 'fast' and full.screen == 'full'
        assert full.gauges['replay_legal'] and not full.gauges['diagnostic_only']
        assert all({'rule_id', 'measured', 'limit', 'boxes'} <= b.keys() for b in full.broken)
        assert math.isfinite(row['fast_s']) and 0 <= row['fast_s'] <= row['full_s']
        assert row['admission']['load1'] <= 45
        assert row['admission']['mask'].bit_count() >= 8
        assert 0 <= row['admission']['age_s'] < 120
        if 'measurement_session' in row:
            session = data['measurement_sessions'][row['measurement_session']]
            assert sha256_json(session) == row['measurement_session']
            assert session['pid'] == row['pid']
            assert set(session['native_thread_caps'].values()) == {'1'}
            assert session['component_versions']['checker']
            assert len(session['source_sha256']['src/stowage/checker/full.py']) == 64
            for name, digest in row['code_sha256'].items():
                assert digest == session['source_sha256'][str((HERE/name).relative_to(HERE.parents[3]))]
        else:
            # Initial rows explicitly retain their missing pre-call source pin.
            assert data['legacy_source_capture_note']
    assert sum('measurement_session' not in row for row in rows) <= 4
    assert data['summary'] == summary(rows)


def test_measurement_admission_waits_for_all_capacity_conditions(tmp_path, monkeypatch):
    monkeypatch.setattr(clear_benchmark, 'ROOT', tmp_path)
    monkeypatch.setattr(clear_benchmark, 'HERE', tmp_path)
    monkeypatch.setattr(clear_benchmark.time, 'time', lambda: 1000.)
    capacity = tmp_path/'experiments/fleet/core-state.json'
    capacity.parent.mkdir(parents=True)
    (tmp_path/'fixtures').mkdir()
    states = [{'mask': 7, 'load1': 30, 't': 999},
              {'mask': 255, 'load1': 46, 't': 999},
              {'mask': 255, 'load1': 30, 't': 800},
              {'mask': 255, 'load1': 30, 't': 1010},
              {'mask': 255, 'load1': 30, 't': 999}]
    capacity.write_text(json.dumps(states.pop(0)))
    def advance(_):
        assert states, 'admission did not accept the fresh, free reading'
        capacity.write_text(json.dumps(states.pop(0)))
    monkeypatch.setattr(clear_benchmark.time, 'sleep', advance)
    assert clear_benchmark._admit_measurement() == {'mask': 255, 'load1': 30, 't': 999, 'age_s': 1.}
    assert not states


def test_measurement_control_cancel_prevents_a_new_call(tmp_path, monkeypatch):
    monkeypatch.setattr(clear_benchmark, 'HERE', tmp_path)
    (tmp_path/'fixtures').mkdir()
    (tmp_path/'fixtures/clear190.cancel').touch()
    with pytest.raises(RuntimeError, match='cancelled'):
        clear_benchmark._admit_measurement()
