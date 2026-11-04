"""Offline v4 handover disclosure and printed instruction regressions."""
from copy import deepcopy
from html import escape
import inspect
import json
from pathlib import Path

import pytest

from stowage.model import CheckReport, Instance, MovePlan

import experiments.mvp.pipeline.package as package
from experiments.mvp.pipeline.package import receipt as receipt_module
from experiments.mvp.pipeline.package.confirmation import BATCH_CONFIRMATION_SOURCE, canonical_rule_label
from experiments.mvp.pipeline.package.note import render_note
from experiments.mvp.pipeline.package.provenance import confirmed_instruction_rows
from experiments.mvp.pipeline.package.regenerate import regenerate
from experiments.mvp.pipeline.package.render import render_bay_plan


HERE = Path(__file__).parent


@pytest.fixture(scope='module')
def hero():
    outcome = json.loads((HERE / 'fixtures/hero-run/outcome.json').read_text())
    rules = confirmed_instruction_rows(outcome['rules'], outcome['confirmation'], outcome['receipt']['steps'],
                                      reading=outcome.get('reading'), runner_receipt=outcome['receipt'],
                                      packet=outcome['packet'])
    assert len(rules) == 1 and rules[0]['confirmed'] is True
    return (outcome, Instance.model_validate(outcome['instance']),
            MovePlan.model_validate(outcome['plan']), CheckReport.model_validate(outcome['checker']), rules)


def test_adopted_hero_batch_is_honestly_labelled_without_changing_evidence(hero):
    outcome, instance, plan, report, rules = hero
    original = deepcopy(outcome['confirmation'])
    note = render_note(instance, plan, report, rules=rules, confirmation=original)
    label = 'Confirmed by: ' + BATCH_CONFIRMATION_SOURCE
    assert label in note['text']
    assert 'Confirmed by: user_authorized_batch:' not in note['text']
    instruction = next(row for row in note['statements'] if row['id'] == 'instruction:0')
    assert instruction['evidence']['confirmation'] == original == outcome['confirmation']
    assert inspect.signature(regenerate).parameters['confirmation_source'].default == BATCH_CONFIRMATION_SOURCE


def test_every_bay_prints_verbatim_instruction_canonical_rule_and_batch_label_near_reader(hero):
    outcome, instance, plan, report, rules = hero
    banner = 'Recorded reading: StowLM Reader swa-last-5. Pipeline: frozen.'
    html = render_bay_plan(instance, plan, report, {}, banner=banner,
                           rules=rules, confirmation=outcome['confirmation'])
    pages = html.split('<section class="bay-page"')[1:]
    assert pages
    for page in pages:
        assert escape(rules[0]['sentence']) in page
        assert 'Canonical rule:</b> close bay 09</p>' in page
        canonical = escape(json.dumps(rules[0]['reading'], sort_keys=True, separators=(',', ':')))
        assert f'<p class="canonical-reading">{canonical}</p>' in page
        assert 'Confirmed by: ' + BATCH_CONFIRMATION_SOURCE in page
        assert page.index('StowLM Reader swa-last-5') < page.index('Confirmed instruction:') < page.index('<header>')


def test_real_planner_identity_and_recorded_time_survive_in_note_and_bay_plan(hero):
    _, instance, plan, report, original_rules = hero
    rules = [dict(original_rules[0], confirmation_source='planner_ui:chief-officer-42',
                  confirmed_utc='2026-11-02T20:30:00Z')]
    confirmation = {'source': BATCH_CONFIRMATION_SOURCE}
    label = "Confirmed by: source recorded as 'planner_ui:chief-officer-42' at 2026-11-02T20:30:00Z"
    note = render_note(instance, plan, report, rules=rules, confirmation=confirmation)
    html = render_bay_plan(instance, plan, report, {}, banner='Reader review',
                           rules=rules, confirmation=confirmation)
    assert label in note['text'] and escape(label) in html
    assert 'no planner present' not in note['text'] and 'no planner present' not in html


def test_bay_prints_all_confirmed_rows_and_escapes_their_text(hero):
    _, instance, plan, report, original_rules = hero
    rules = [dict(original_rules[0], sentence='Keep <bay nine> clear & closed.'),
             dict(original_rules[0], sentence='One port per hatch.',
                  reading={'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'one_port_per_hatch'}}),
             dict(original_rules[0], sentence='UNCONFIRMED INPUT', confirmed=False)]
    html = render_bay_plan(instance, plan, report, {}, banner='Reader review', rules=rules)
    for page in html.split('<section class="bay-page"')[1:]:
        assert 'Keep &lt;bay nine&gt; clear &amp; closed.' in page
        assert 'One port per hatch.' in page
        assert 'one_port_per_hatch' in page
        assert 'UNCONFIRMED INPUT' not in page


@pytest.mark.parametrize('source,label', [
    ('sealed_expected_match', 'Confirmed by: exact match to the expected rule (test harness, no planner)'),
    ('unrecognised_event', "Confirmed by: source recorded as 'unrecognised_event'"),
    (None, "Confirmation recorded; who confirmed isn't recorded"),
])
def test_confirmation_labels_describe_recorded_source_without_asserting_identity(hero, source, label):
    _, instance, plan, report, original_rules = hero
    rules = [dict(original_rules[0], confirmation_source=source)]
    note = render_note(instance, plan, report, rules=rules)
    html = render_bay_plan(instance, plan, report, {}, banner='Reader review', rules=rules)
    assert label in note['text'] and escape(label) in html
    assert 'Confirmed by: sealed_expected_match' not in note['text']


@pytest.mark.parametrize('constraint,label', [
    ({'type': 'close_bay', 'bay': 9}, 'close bay 09'),
    ({'type': 'max_tiers', 'zone': 'deck', 'bay': 9, 'max_tiers': 2}, 'at most 2 tiers in the deck, bay 09'),
    ({'type': 'max_tiers', 'zone': 'hold', 'bay': None, 'max_tiers': 3}, 'at most 3 tiers in the hold, every bay'),
    ({'type': 'one_port_per_hatch'}, 'one discharge port per hatch'),
    ({'type': 'dg_keep_away', 'imdg_class': '3', 'min_bays': 2, 'away_from': {'kind': 'bay', 'bay': 9}},
     'DG class 3 at least 2 bays from bay 09'),
    ({'type': 'dg_keep_away', 'imdg_class': '3', 'min_bays': 2, 'away_from': {'kind': 'accommodation'}},
     'DG class 3 at least 2 bays from accommodation'),
])
def test_canonical_rule_label_is_short_plain_words(constraint, label):
    assert canonical_rule_label({'reading': {'schema_version': 'stowlm.reader/2', 'constraint': constraint}}) == label


def test_writer_prints_documented_canonical_envelope_alias(hero, tmp_path, monkeypatch):
    original, _, _, report, rules = hero
    outcome = {key: deepcopy(original[key]) for key in ('status', 'instance', 'plan', 'packet')}
    outcome.update({key: original['receipt'][key] for key in ('engine_version', 'engine_manifest_sha256')})
    rule = dict(rules[0])
    canonical = rule.pop('reading')
    rule['canonical_envelope'] = canonical
    outcome['rules'] = [rule]
    monkeypatch.setattr(package, '_checker', lambda *args: report)
    sign = receipt_module.sign_receipt
    monkeypatch.setattr(package, 'sign_receipt', lambda receipt, **kwargs:
                        sign(receipt, key_dir=tmp_path / 'test-key'))
    directory = tmp_path / 'alternate-envelope'
    package.write_package(outcome, directory)
    html = (directory / 'bay-plan.html').read_text()
    assert 'Canonical rule:</b> close bay 09</p>' in html
    assert escape(json.dumps(canonical, sort_keys=True, separators=(',', ':'))) in html
    assert 'canonical reading not recorded' not in html
    receipt = json.loads((directory / 'receipt.json').read_text())
    assert receipt['rules'][0]['reading'] == canonical
    assert package.verify_package(directory, expected_public_key=receipt['public_key'])['passed']
