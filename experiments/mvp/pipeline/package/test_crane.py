"""Cargo-work estimates stay separate from native move and departure verdicts."""
import csv
import io
import json
import math
from pathlib import Path

import pytest

from stowage.checker.stability_loading import loading_condition
from stowage.model import CheckReport, Instance, MovePlan
from stowage.moves import apply_move, initial_state

from experiments.mvp.pipeline.package.crane import cargo_work


@pytest.fixture(scope='module')
def checked_inputs():
    directory = Path(__file__).parent / 'fixtures' / 'complete'
    def load(name):
        return json.loads((directory / name).read_text())
    return (Instance.model_validate(load('instance.json')), MovePlan.model_validate(load('plan.json')),
            CheckReport.model_validate(load('checker-report.json')))


def rows(result, name):
    return list(csv.DictReader(io.StringIO(result['files'][name].split('\n', 1)[1])))


def test_quay_and_yard_preserve_every_native_move_once(checked_inputs):
    instance, plan, report = checked_inputs
    before = plan.model_dump(mode='json')
    result = cargo_work(instance, plan, report, banner='Half-ship section: synthetic fixture')
    quay = rows(result, 'crane-sequence.csv')
    yard = rows(result, 'yard-sequence.csv')
    originals = [r for r in quay + yard if r['source_move_index']]
    assert sorted(int(r['source_move_index']) for r in originals) == list(range(1, len(plan.moves) + 1))
    assert len(yard) == 92
    assert sum(bool(r['source_move_index']) for r in quay) == 206
    assert all(r['kind'] == 'relocate' for r in yard)
    assert {r['crane'] for r in quay if r['source_move_index']} == {'QC-1', 'QC-2'}
    assert all(r['mass_t'] and (r['pod'] or r['kind'].startswith('hatch_cover')) for r in originals)
    assert plan.model_dump(mode='json') == before
    for text in result['files'].values():
        assert text.splitlines()[0].startswith('# Half-ship section: synthetic fixture')
    assert 'ESTIMATE ONLY: list_deg_est' in result['files']['crane-sequence.csv'].splitlines()[0]


def test_list_estimate_names_unresolved_heel_ballast_hold(checked_inputs):
    instance, plan, report = checked_inputs
    result = cargo_work(instance, plan, report, banner='synthetic')
    quay = rows(result, 'crane-sequence.csv')
    flagged = [r for r in quay if r['list_over_1_deg'] == 'YES']
    evidence = result['evidence']
    assert flagged
    assert evidence['max_abs_list_deg_est'] > 2
    assert evidence['flagged_source_move_indices'] == [int(r['source_move_index']) for r in flagged]
    assert all(abs(float(r['list_deg_est'])) > 1 and 'HEEL-BALLAST-01' in r['instruction'] for r in flagged)
    hold = next(i for i, r in enumerate(quay) if r['kind'] == 'heel_ballast_hold')
    first_flag = next(i for i, r in enumerate(quay) if r['list_over_1_deg'] == 'YES')
    assert hold < first_flag
    assert 'not an executed ballast transfer' in quay[hold]['instruction']
    assert evidence['estimate_is_safety_verdict'] is False
    assert evidence['cargo_work_stability_checked'] is False
    assert 'departure_gm_m' in evidence['estimator']


def test_estimator_includes_native_fixed_and_tank_moments(checked_inputs):
    instance, plan, report = checked_inputs
    result = cargo_work(instance, plan, report, banner='synthetic')
    first = next(r for r in rows(result, 'crane-sequence.csv') if r['source_move_index'])
    index = int(first['source_move_index'])
    state = initial_state(instance)
    for move in plan.moves[:index]:
        state = apply_move(instance, state, move)
    condition = loading_condition(instance, state.ship)
    expected = math.degrees(math.atan(condition.tcg_m / report.parts['stability'].metrics['gm_m']))
    assert float(first['list_deg_est']) == pytest.approx(expected, abs=1e-8)
    assert instance.vessel.fixed_loads
    assert state.ship.tank_masses_kg


def test_ballast_unchanged_is_explicit_and_totals_close(checked_inputs):
    instance, plan, report = checked_inputs
    result = cargo_work(instance, plan, report, banner='synthetic')
    tanks = rows(result, 'ballast-plan.csv')
    actual = [r for r in tanks if r['record_type'] == 'tank' and r['kind'] == 'ballast']
    summary = next(r for r in tanks if r['record_type'] == 'ballast_summary')
    assert sum(float(r['arrival_t']) for r in actual) == pytest.approx(float(summary['arrival_t']))
    expected = sum(instance.onboard.tank_masses_kg.get(t.id, 0) for t in instance.vessel.tanks
                   if t.kind == 'ballast') / 1000
    assert float(summary['departure_t']) == pytest.approx(expected)
    assert result['evidence']['ballast_unchanged'] is True
    assert 'unchanged from arrival' in result['evidence']['ballast_line']
    assert '1,552 t' in result['evidence']['ballast_line']
    assert rows(result, 'crane-sequence.csv')[-1]['kind'] == 'ballast_condition'


def test_illegal_source_plan_cannot_gain_cargo_work_output(checked_inputs):
    instance, plan, report = checked_inputs
    first_load = next(i for i, m in enumerate(plan.moves) if m.kind == 'load')
    bad_moves = list(plan.moves)
    bad_moves[first_load] = bad_moves[first_load].model_copy(update={'src': 'NOT-A-YARD-POSITION'})
    bad = plan.model_copy(update={'moves': bad_moves})
    with pytest.raises(ValueError, match='cargo-work move replay failed'):
        cargo_work(instance, bad, report, banner='synthetic')


def test_unknown_gm_requires_hold_and_never_becomes_zero_list(checked_inputs):
    instance, plan, report = checked_inputs
    report = report.model_copy(deep=True)
    report.parts['stability'].metrics.pop('gm_m')
    result = cargo_work(instance, plan, report, banner='synthetic')
    quay = rows(result, 'crane-sequence.csv')
    assert result['evidence']['max_abs_list_deg_est'] is None
    assert result['evidence']['unavailable_source_move_indices']
    assert quay[0]['kind'] == 'heel_ballast_hold'
    assert all(not r['list_deg_est'] for r in quay)
