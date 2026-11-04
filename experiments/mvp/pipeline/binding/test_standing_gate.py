"""Code5 P2: the direct API cannot silently discard hard standing text."""
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.binding import adapter, confirm_brief
from experiments.mvp.pipeline.binding.test_hooks import fixture_instance
from experiments.mvp.pipeline.binding.test_adapter import RULES
from experiments.mvp.reader import public_brief
from experiments.mvp.reader.schema import OnePortPerHatch
from llm.instructions.schema import Refusal


def receipt(instance, sentence, answer):
    parser = NS(parse=lambda *a: (answer, {'parser': 'standing-gate-test', 'adapter_sha256': None}))
    return confirm_brief(public_brief.read_brief(instance,
        public_brief.VoyageBrief(standing=sentence), parser=parser), 'host exact reading approval')


@pytest.mark.parametrize('answer', [RULES[0], RULES[1], RULES[2], Refusal(code='ambiguous', reason='Which bay?')])
def test_nonempty_unsupported_standing_reading_stops_before_planning(monkeypatch, answer):
    instance = fixture_instance()
    confirmed = receipt(instance, 'Keep bay 1 clear.', answer)
    monkeypatch.setattr(adapter, 'plan_with_rules', lambda *a, **kw: pytest.fail('standing text silently dropped'))
    result, evidence = adapter.execute_brief(instance, confirmed)
    assert result is None and evidence['status'] == 'needs_confirmation'
    assert evidence['unresolved_standing_sentence_indices'] == [0]
    assert evidence['unresolved_sentence_indices'] == [0]
    assert not evidence['passed'] and not evidence['plan_released']
    assert evidence['confirmation_source'] == 'host exact reading approval'
    assert 'move it to voyage' in evidence['reason']


@pytest.mark.parametrize('sentence,answer', [('', Refusal(code='not_an_instruction', reason='Empty')),
    ('One port per hatch.', OnePortPerHatch(type='one_port_per_hatch'))])
def test_empty_standing_or_supported_preference_can_reach_planning(monkeypatch, sentence, answer):
    instance = fixture_instance()
    confirmed = receipt(instance, sentence, answer)
    seen = []
    def plan(original, answers, **kwargs):
        seen.extend(answers)
        return None, {'rules': []}
    monkeypatch.setattr(adapter, 'plan_with_rules', plan)
    _, evidence = adapter.execute_brief(instance, confirmed)
    assert seen == [] and evidence['standing_is_preference']
