"""Review wording, timing and authentication additions need no solver search."""
from types import SimpleNamespace as NS

import pytest

from .. import adapter, confirm_brief
from ..test_adapter import _receipt
from ..test_hooks import fixture_instance
from experiments.mvp.reader.schema import OnePortPerHatch
from llm.instructions.schema import CloseBay
from . import api


@pytest.mark.parametrize('reason,kind', [
    ('the load list needs 10 TEU of cells but only 0 remain open after all instructions', 'proved_full_load'),
    ('no instruction-allowed physical footprint: B1', 'proved_full_load'),
    ('strict physical-hatch aggregate allocation unavailable: UNKNOWN; no infeasibility proof', 'solver_timeout'),
    ('pinned engine/native source manifest does not match', 'engine_identity'),
    ('active checker stubs are not a full native checker', 'engine_identity'),
])
def test_only_full_load_proofs_request_correction(reason, kind):
    evidence = dict(status='constraint_unavailable', reason=reason)
    api.annotate(evidence)
    assert evidence['refusal_kind'] == kind
    text = evidence['planner_message']
    assert ('Correct the instruction' in text) == (kind == 'proved_full_load')
    assert ("A partial plan wasn't tried." in text) == (kind == 'proved_full_load')
    if kind == 'proved_full_load':
        assert text.startswith("With every box aboard this limit can't be met: ")


def test_first_checked_time_is_public_even_without_subscriber(monkeypatch):
    def base(*args, on_incumbent, **kwargs):
        on_incumbent(dict(passed=True, wall_s=4.2))
        on_incumbent(dict(passed=True, wall_s=9.1))
        return NS(plan='checked'), dict(status='checked_plan', passed=True, wall_s=15.)
    monkeypatch.setattr(adapter, 'plan_with_rules', base)
    _, evidence = api.plan_with_rules(fixture_instance(), [CloseBay(type='close_bay', bay=1)])
    assert evidence['first_checked_wall_s'] == 4.2
    assert evidence['wall_s'] == 15.


def test_no_relaxed_search_is_not_claimed_as_searched():
    evidence = dict(status='relaxed_plan', level=4, stages={'peel_seam': {'trials': [
        {'level': 0}, *[dict(level=n, search={'proposal': {'trials': []}}) for n in range(1, 5)]]}})
    api.annotate(evidence)
    assert evidence['ladder_work'] == 'none'
    assert evidence['ladder_searched_levels'] == []
    assert 'L1–L3 were not searched' in evidence['ladder_note']


def test_actual_inner_proposal_trial_is_the_search_evidence():
    evidence = dict(stages={'peel_seam': {'trials': [
        dict(level=1, search={'proposal': {'trials': []}}),
        dict(level=3, search={'partial_search': {'proposal': {'trials': [{'attempt': 0}]}}})]}})
    api.annotate(evidence)
    assert evidence['ladder_work'] == 'searched'
    assert evidence['ladder_searched_levels'] == [3]


def test_hatch_confirmation_names_each_panel_and_preserves_authentication(monkeypatch):
    instance = fixture_instance()
    reading = _receipt(instance, OnePortPerHatch(type='one_port_per_hatch'))
    text = api.confirmation_text(reading)
    assert 'Each hatch cover panel' in text[0]['text']
    assert 'may serve different ports' in text[0]['text']
    monkeypatch.setattr(api, 'plan_with_rules', lambda *a, **k: (None, {'rules': []}))
    with pytest.raises(ValueError, match='Unconfirmed brief'):
        api.execute_brief(instance, reading)
    _, evidence = api.execute_brief(instance, confirm_brief(reading, 'review3:test-host'))
    assert evidence['confirmation_source'] == 'review3:test-host'
    assert evidence['confirmation_text'] == text


def test_executor_uses_public_wrapper_without_mutating_original(monkeypatch):
    instance = fixture_instance()
    reading = confirm_brief(_receipt(instance, CloseBay(type='close_bay', bay=1)), 'review3:host')
    original = adapter.plan_with_rules
    calls = []
    monkeypatch.setattr(api, 'plan_with_rules', lambda *a, **kw: (calls.append(a[1]), {'rules': []}))
    api.execute_brief(instance, reading)
    assert calls[0][0].type == 'close_bay'
    assert adapter.plan_with_rules is original
