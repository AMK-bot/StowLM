"""An authenticated reading alone must never authorize execution."""
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.binding import ConfirmedBrief, adapter, confirm_brief
from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
from experiments.mvp.pipeline.binding.test_adapter import RULES, _receipt
from experiments.mvp.pipeline.binding.test_hooks import fixture_instance
from experiments.mvp.reader import public_brief


def test_authentic_unconfirmed_reading_cannot_reach_planner(monkeypatch):
    """Regression #17: this fails against the former reading-only gate."""
    instance = fixture_instance()
    receipt = _receipt(instance, RULES[0])
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('unconfirmed reading reached planner'))
    with pytest.raises(ValueError, match=r'Unconfirmed brief.*confirm_brief'):
        adapter.execute_brief(instance, receipt)


def test_confirmation_records_exact_host_source_and_preserves_reading_identity(monkeypatch):
    instance = fixture_instance()
    receipt = _receipt(instance, RULES[0], RULES[0])
    confirmed = confirm_brief(receipt, 'planner_ui:review-42')
    seen = []
    def plan(original, answers, **kwargs):
        assert original is instance
        seen.extend(answers)
        return None, {'rules': [{}, {}]}
    monkeypatch.setattr(adapter, 'plan_with_rules', plan)
    result, evidence = adapter.execute_brief(instance, confirmed)
    assert result is None and seen == [RULES[0], RULES[0]]
    assert isinstance(confirmed, ConfirmedBrief) and isinstance(confirmed, public_brief.ReadBrief)
    assert confirmed.payload == receipt.payload and confirmed.authentication == receipt.authentication
    assert confirmed.evidence() == receipt.evidence()
    assert evidence['confirmation_source'] == 'planner_ui:review-42'
    assert evidence['confirmation']['source'] == confirmed.source
    assert evidence['confirmation']['schema_version'] == 'stowlm.binding-confirmation/1'
    assert evidence['receipt_sha256'] == hashlib.sha256(receipt.payload.encode()).hexdigest()
    assert evidence['confirmation_sha256'] == evidence['confirmation']['confirmation_sha256']


@pytest.mark.parametrize('source', [None, True, False, '', ' \n ', {}, ['host']])
def test_confirmation_requires_explicit_nonempty_string_source(source):
    with pytest.raises(ValueError, match='Confirmation source'):
        confirm_brief(_receipt(fixture_instance(), RULES[0]), source)


@pytest.mark.parametrize('field', ['source', 'payload', 'authentication', 'confirmation_authentication'])
def test_tampered_confirmation_never_reaches_planner(monkeypatch, field):
    instance = fixture_instance()
    confirmed = confirm_brief(_receipt(instance, RULES[0]), 'host approval')
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('tampered confirmation reached planner'))
    forged = replace(confirmed, **{field: 'tampered'})
    with pytest.raises(ValueError, match='authenticated brief confirmation'):
        adapter.execute_brief(instance, forged)


def test_reader_authentication_cannot_substitute_for_confirmation(monkeypatch):
    instance = fixture_instance()
    receipt = _receipt(instance, RULES[0])
    forged = ConfirmedBrief(receipt.payload, receipt.authentication, 'claimed approval', receipt.authentication)
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('reader MAC authorized confirmation'))
    with pytest.raises(ValueError, match='authenticated brief confirmation'):
        adapter.execute_brief(instance, forged)


def test_confirmation_cannot_be_swapped_to_another_authentic_reading(monkeypatch):
    instance = fixture_instance()
    first = confirm_brief(_receipt(instance, RULES[0]), 'host approval')
    other = _receipt(instance, RULES[1])
    forged = replace(first, payload=other.payload, authentication=other.authentication)
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('swapped reading reached planner'))
    with pytest.raises(ValueError, match='authenticated brief confirmation'):
        adapter.execute_brief(instance, forged)


def test_valid_confirmation_still_rejects_a_different_instance(monkeypatch):
    instance = fixture_instance()
    confirmed = confirm_brief(_receipt(instance, RULES[0]), 'host approval')
    changed = instance.model_copy(update={'id': 'different-input'})
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('wrong input reached planner'))
    with pytest.raises(ValueError, match='does not match this input/context'):
        adapter.execute_brief(changed, confirmed)


def test_reading_checkpoint_does_not_silently_preserve_or_create_confirmation(monkeypatch):
    instance = fixture_instance()
    confirmed = confirm_brief(_receipt(instance, RULES[0]), 'host approval')
    key = b'dedicated-test-checkpoint-key-32bytes'
    checkpoint = public_brief.export_checkpoint(confirmed, key=key)
    reloaded = public_brief.load_checkpoint(instance, checkpoint, key=key)
    assert not isinstance(reloaded, ConfirmedBrief)
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('reading checkpoint auto-confirmed'))
    with pytest.raises(ValueError, match='Unconfirmed brief'):
        adapter.execute_brief(instance, reloaded)
    assert verify_confirmation(confirm_brief(reloaded, 'explicit receiving host approval'))['source'] == 'explicit receiving host approval'


def test_confirmation_cannot_seal_a_tampered_reading():
    receipt = _receipt(fixture_instance(), RULES[0])
    payload = json.loads(receipt.payload)
    payload['sentences'][1]['parsed'] = {'changed': True}
    with pytest.raises(ValueError, match='authenticated reader-produced'):
        confirm_brief(replace(receipt, payload=json.dumps(payload)), 'host approval')


def test_old_positional_and_boolean_confirmation_shortcuts_fail_loudly():
    instance = fixture_instance()
    receipt = _receipt(instance, RULES[0])
    with pytest.raises(TypeError):
        confirm_brief(receipt)
    with pytest.raises(TypeError):
        adapter.execute_brief(instance, receipt, True)
    with pytest.raises(TypeError):
        adapter.execute_brief(instance, receipt, confirmed=True)


def test_confirmation_import_alias_shares_class_and_signing_key(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    alias = importlib.import_module('binding.confirmation')
    assert alias.ConfirmedBrief is ConfirmedBrief
    confirmed = alias.confirm_brief(_receipt(fixture_instance(), RULES[0]), 'alias host')
    assert verify_confirmation(confirmed)['source'] == 'alias host'


def test_checked_incumbent_also_records_authenticated_confirmation_source(monkeypatch):
    instance = fixture_instance()
    confirmed = confirm_brief(_receipt(instance, RULES[0]), 'planner_ui:stream-42')
    original_event = {'passed': True, 'confirmation_source': 'untrusted placeholder'}
    events = []
    def plan(original, answers, **kwargs):
        kwargs['on_incumbent'](original_event)
        return None, {'rules': [{}]}
    monkeypatch.setattr(adapter, 'plan_with_rules', plan)
    _, evidence = adapter.execute_brief(instance, confirmed, on_incumbent=events.append)
    assert events[0]['confirmation_source'] == 'planner_ui:stream-42'
    assert events[0]['confirmation'] == evidence['confirmation']
    assert events[0]['receipt_sha256'] == evidence['receipt_sha256']
    assert original_event['confirmation_source'] == 'untrusted placeholder'


def test_incumbent_consumer_cannot_rewrite_final_confirmation_evidence(monkeypatch):
    instance = fixture_instance()
    confirmed = confirm_brief(_receipt(instance, RULES[0]), 'approved host event')
    def consumer(event):
        event['confirmation']['source'] = 'consumer rewrite'
    def plan(original, answers, **kwargs):
        kwargs['on_incumbent']({'passed': True})
        return None, {'rules': [{}]}
    monkeypatch.setattr(adapter, 'plan_with_rules', plan)
    _, evidence = adapter.execute_brief(instance, confirmed, on_incumbent=consumer)
    assert evidence['confirmation_source'] == 'approved host event'
    assert evidence['confirmation']['source'] == 'approved host event'
