"""Chief officer's complete statement menu, without network or numeric reruns."""
import json
from pathlib import Path
import socket

import pytest

from llm.spark.client import SparkClient
from stowage.model import CheckReport, Instance, MovePlan

from experiments.mvp.pipeline.package.note import DISCLAIMER, LOADING_WARNING, build_note_prompt, render_note


@pytest.fixture(scope='module')
def note_inputs():
    outcome = json.loads((Path(__file__).parent / 'fixtures' / 'outcome.json').read_text())
    return (Instance.model_validate(outcome['instance']), MovePlan.model_validate(outcome['plan']),
            CheckReport.model_validate(outcome['checker']), outcome)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline note attempted a paid call or network access')
    monkeypatch.setattr(SparkClient, 'complete', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def selection():
    return dict(headline=['status', 'scope'], points=[['departure_gm', 'departure_draught'],
                                                    ['departure_trim', 'departure_heel'], ['not_checked']])


def test_default_is_deterministic_template_without_network(note_inputs):
    instance, plan, report, _ = note_inputs
    first = render_note(instance, plan, report)
    assert first == render_note(instance, plan, report)
    assert first['mode'] == 'generated'
    assert first['model'] is None and first['selection'] is None and first['cost_usd'] == 0
    assert '[Deterministic template fallback]' in first['text']
    assert 'Checker: PASS' in first['text']


def test_recorded_spark_can_only_reorder_the_complete_checked_allow_list(note_inputs):
    instance, plan, report, _ = note_inputs
    note = render_note(instance, plan, report, recorded_selection=selection())
    assert note['mode'] == 'recorded' and note['model'] == 'muse-spark-1.3'
    assert note['selection'] == selection()
    known = {s['id']: s['text'] for s in note['statements']}
    chosen = [*note['selection']['headline'], *(sid for group in note['selection']['points'] for sid in group)]
    assert len(chosen) == len(set(chosen)) and set(chosen) <= known.keys()
    assert all(sentence in note['text'] for sentence in known.values())
    assert 'Recorded Muse Spark' in note['text']
    assert DISCLAIMER in note['text'] and LOADING_WARNING in note['text']


@pytest.mark.parametrize('raw', [
    {'headline': ['invented checker fact'], 'points': [['departure_gm'], ['scope']]},
    {'headline': ['status'], 'points': [['scope'], ['status']]},
    {'headline': ['status'], 'points': [['scope'], ['departure_gm']], 'prose': 'invented'},
    'invalid JSON',
])
def test_invalid_selection_is_rejected_and_template_is_used(note_inputs, raw):
    instance, plan, report, _ = note_inputs
    note = render_note(instance, plan, report, recorded_selection=raw)
    assert note['mode'] == 'generated' and note['model'] is None and note['selection'] is None
    assert note['rejection'] and '[Deterministic template fallback]' in note['text']
    assert 'invented checker fact' not in note['text']


def test_old_recording_is_not_reused_after_menu_changes(note_inputs):
    instance, plan, report, outcome = note_inputs
    old = outcome.get('note_selection')
    if old and 'total' in json.dumps(old):
        note = render_note(instance, plan, report, recorded_selection=old)
        assert note['mode'] == 'generated' and note['rejection']


def test_relaxed_warning_is_first_and_mandatory_even_if_spark_omits_it(note_inputs):
    instance, plan, report, _ = note_inputs
    note = render_note(instance, plan, report, relaxed=True, broken_rules=['strength.test', 'lashing.test'],
                       recorded_selection=selection())
    first = note['text'].splitlines()[0]
    assert first.startswith('RELAXED PLAN') and 'strength.test' in first and 'lashing.test' in first
    assert 'Half-ship section:' in note['text'].splitlines()[1]


def test_departure_scalars_have_limits_and_direction(note_inputs):
    instance, plan, report, _ = note_inputs
    note = render_note(instance, plan, report)
    text = note['text']
    assert 'GM 2.54 m; minimum 0.15 m' in text
    assert 'draught aft 7.47 m, forward 7.63 m' in text
    assert '0.16 m by the head' in text
    assert 'heel 0.006 degrees' in text and 'limit 2.00 degrees' in text
    assert 'displacement 13,564.284 t' in text
    assert 'berth/channel limit unknown' in text
    assert 'stability-summary.md' in text and 'tank-plan.csv' in text


def test_scope_cargo_counts_and_slots_without_yard_ids_or_project_labels(note_inputs):
    instance, plan, report, _ = note_inputs
    note = render_note(instance, plan, report)
    text = note['text']
    assert text.startswith('Half-ship section: 190 boxes planned in 8 of 14 bays.')
    assert '268 fixed synthetic background boxes (3,856 t)' in text
    assert '5 DG boxes' in text and '6 reefer boxes' in text and '2 OOG boxes' in text
    assert 'slots:' in text and '0260502' in text
    assert not any(box.id in text for box in instance.containers)
    assert all(word not in text for word in ('D11', 'TRAIN', 'expert Q14', 'every check ran', 'crane lifts'))
    assert DISCLAIMER in text and LOADING_WARNING in text


def test_every_margin_examined_and_real_height_limit_is_first(note_inputs):
    instance, plan, report, _ = note_inputs
    facts = render_note(instance, plan, report)['facts']['tightest_limits']
    assert facts['examined'] == sum(len(part.margins) for part in report.parts.values())
    assert facts['top_three'][0]['metric'] == 'stack_height_m'
    assert facts['top_three'][0]['margin'] == pytest.approx(.028)
    assert len(facts['top_three']) == 3
    assert all(row['metric'] != 'weather_table_applicability' for row in facts['top_three'])
    assert any('applicability' in row['source'] for row in facts['excluded'])
    assert all('applicability' in row['source'] for row in facts['excluded'])
    assert [r['spare_fraction'] for r in facts['top_three']] == sorted(r['spare_fraction'] for r in facts['top_three'])


def test_confirmed_instructions_and_confirming_source_are_visible(note_inputs):
    instance, plan, report, _ = note_inputs
    rules = [dict(sentence='Keep dangerous goods on deck.', confirmed=True),
             dict(sentence='Never accepted.', confirmed=False)]
    note = render_note(instance, plan, report, rules=rules,
                       confirmation=dict(schema_version='stowlm.binding-confirmation/1', source='planner_ui:review-42'))
    assert "Confirmed instruction: Keep dangerous goods on deck. Confirmed by: source recorded as 'planner_ui:review-42'." in note['text']
    assert 'Never accepted.' not in note['text']
    assert note['statements'][next(i for i,r in enumerate(note['statements']) if r['id']=='instruction:0')]['evidence']['confirmation']['source'] == 'planner_ui:review-42'


def test_no_fabricated_confirmer_when_identity_missing(note_inputs):
    instance, plan, report, _ = note_inputs
    text = render_note(instance, plan, report, rules=[dict(sentence='Keep a bay clear.', confirmed=True)])['text']
    assert "Confirmation recorded; who confirmed isn't recorded." in text
    text = render_note(instance, plan, report)['text']
    assert 'No confirmed voyage instruction is recorded' in text


def test_cargo_work_hold_is_explicit_and_never_claims_ballast_adjustment(note_inputs):
    instance, plan, report, _ = note_inputs
    text = render_note(instance, plan, report, cargo_work=dict(heel_ballast_hold=True,
                       max_abs_list_deg_est=2.7))['text']
    assert 'HEEL-BALLAST-01' in text and 'stop before any lift flagged over 1 degree' in text
    assert 'no ballast adjustment is claimed' in text
    assert LOADING_WARNING in text


def test_recording_prompt_matches_the_rendered_menu(note_inputs):
    import hashlib
    instance, plan, report, _ = note_inputs
    prompt = build_note_prompt(instance, plan, report)
    note = render_note(instance, plan, report)
    assert hashlib.sha256(prompt.encode()).hexdigest() == note['prompt_sha256']
    assert json.loads(prompt.split('STATEMENTS: ', 1)[1]) == note['statements']
