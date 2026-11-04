"""Grounding and accounting regressions; no HTTP or planner calls."""
from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.reader.refusal_explainer import (
    ExplanationOutputError, ExplanationProviderError, PURPOSE, SCHEMA_VERSION,
    SparkRefusalExplainer, build_prompt, proof_sha256, render_explanation, validate_explanation,
)


@pytest.fixture
def proof():
    return dict(schema_version='stowlm.refusal-evidence/1', status='static_impossible',
                facts=[dict(id='F1', text='Closing bay 01 leaves 8 eligible cells for 13 containers.'),
                       dict(id='F2', text='At least 13 eligible cells are required, so this instruction is impossible.')],
                required_fact_ids=['F1', 'F2'], alternatives=[], clarifications=[])


def _output(proof, **changes):
    value = dict(schema_version=SCHEMA_VERSION, status=proof['status'],
                 statements=[dict(fact_id=f['id'], text=f['text']) for f in proof['facts']],
                 alternative_id=None, clarification_id=None)
    value.update(changes)
    return value


def test_exact_causal_proof_and_ordering(proof):
    output = _output(proof)
    output['statements'].reverse()
    result = validate_explanation(json.dumps(output), proof)
    assert render_explanation(result, proof) == ' '.join(f['text'] for f in reversed(proof['facts']))
    assert proof_sha256(proof) == proof_sha256(deepcopy(proof))
    assert 'software' not in render_explanation(result, proof)


@pytest.mark.parametrize('mutation', ['numeric', 'invented', 'missing_reason', 'extra_field', 'duplicate_fact'])
def test_unsupported_assertions_and_missing_proof_are_rejected(proof, mutation):
    output = _output(proof)
    if mutation == 'numeric':
        output['statements'][0]['text'] = output['statements'][0]['text'].replace('8', '80')
    elif mutation == 'invented':
        output['statements'].append(dict(fact_id='F99', text='The ship will be safe tomorrow.'))
    elif mutation == 'missing_reason':
        output['statements'].pop()
    elif mutation == 'extra_field':
        output['explanation'] = 'The alternative is guaranteed feasible.'
    else:
        output['statements'].append(output['statements'][0])
    with pytest.raises(ExplanationOutputError):
        validate_explanation(json.dumps(output), proof)


def test_duplicate_output_keys_are_rejected(proof):
    raw = json.dumps(_output(proof))
    raw = raw.replace('"alternative_id": null', '"alternative_id": null, "alternative_id": "A1"')
    with pytest.raises(ExplanationOutputError):
        validate_explanation(raw, proof)


def test_ambiguous_request_must_ask_supplied_question(proof):
    proof.update(status='ambiguous', clarifications=[dict(id='Q1', text='Which bay should be closed?')])
    output = _output(proof)
    with pytest.raises(ExplanationOutputError):
        validate_explanation(json.dumps(output), proof)
    output['clarification_id'] = 'Q1'
    assert render_explanation(output, proof).endswith('Which bay should be closed?')
    output['clarification_id'] = 'Please loosen the safety rule'
    with pytest.raises(ExplanationOutputError):
        validate_explanation(json.dumps(output), proof)


def test_loosening_names_actual_rule_from_source(proof):
    proof.update(status='loosening', facts=[dict(id='R1', text='The requested limit would loosen the crane-height rule from 4 tiers to 5 tiers.')],
                 required_fact_ids=['R1'])
    assert render_explanation(_output(proof), proof) == proof['facts'][0]['text']


def _alternative(**changes):
    value = dict(id='A1', text='A stored checked plan satisfies closing bay 03 instead; choose this change before planning.',
                 certificate='stored_checked_witness', plan_sha256='ab' * 32,
                 fresh_audit=dict(passed=True, plan_sha256='ab' * 32),
                 would_work=False, requires_user_choice=True)
    value.update(changes)
    return value


def test_nearest_certified_alternative_required_and_exact(proof):
    proof.update(alternatives=[_alternative(), _alternative(id='A2')], nearest_alternative_id='A1')
    output = _output(proof, alternative_id='A1')
    assert render_explanation(output, proof).endswith(proof['alternatives'][0]['text'])
    for unsupported in (None, 'A2', 'A99'):
        with pytest.raises(ExplanationOutputError):
            validate_explanation(json.dumps(_output(proof, alternative_id=unsupported)), proof)


@pytest.mark.parametrize('changes', [dict(fresh_audit=None), dict(fresh_audit=dict(passed=False, plan_sha256='ab'*32)),
    dict(plan_sha256='cd'*32), dict(would_work=True)])
def test_unwitnessed_or_unaudited_alternative_never_claimed(proof, changes):
    proof['alternatives'] = [_alternative(**changes)]
    with pytest.raises(ExplanationOutputError):
        validate_explanation(json.dumps(_output(proof, alternative_id='A1')), proof)
    validate_explanation(json.dumps(_output(proof)), proof)


def test_screen_only_alternative_needs_both_exact_caveats(proof):
    alt = _alternative(certificate='static_screen_only', screen_passed=True, fresh_audit=None,
                       text='Closing bay 03 is not proved impossible; you must plan and check it after choosing the change.')
    proof['alternatives'] = [alt]
    output = _output(proof, alternative_id='A1')
    assert render_explanation(output, proof).endswith(alt['text'])
    alt['text'] = 'Closing bay 03 would work.'
    with pytest.raises(ExplanationOutputError):
        validate_explanation(json.dumps(output), proof)


@pytest.fixture
def fake_spark(monkeypatch, tmp_path, proof):
    from llm.spark.spend import SpendLedger, Usage
    compute = tmp_path/'compute.md'
    compute.write_text('')
    state = NS(text=json.dumps(_output(proof)), calls=[], cost=Decimal('.4'), error=None)
    class Client:
        def __init__(self, **kwargs):
            self.ledger = SpendLedger(path=tmp_path/'calls.jsonl', compute_path=compute)
        def complete(self, prompt, **kwargs):
            if state.error:
                raise state.error
            self.ledger.append(dict(purpose=kwargs['purpose'], cost_usd=str(state.cost)))
            state.calls.append(kwargs)
            return NS(text=state.text, cost_usd=state.cost, request_id='fake-id', usage=Usage(100, 50))
    monkeypatch.setattr('experiments.mvp.reader.baselines.load_credentials', lambda: None)
    monkeypatch.setattr('llm.spark.client.SparkClient', Client)
    return state


def test_spark_provenance_and_shared_restart_budget(fake_spark, proof):
    explainer = SparkRefusalExplainer(allocation_usd='1')
    # Simulate the reader consuming the same purpose allocation before first use.
    explainer.client.ledger.append(dict(purpose=PURPOSE, cost_usd='.5'))
    text, evidence = explainer.explain(proof)
    assert text == ' '.join(f['text'] for f in proof['facts'])
    assert evidence['proof_sha256'] == proof_sha256(proof)
    assert evidence['request_id'] == 'fake-id'
    assert evidence['validated'] is True
    assert evidence['contribution'] == 'selects_and_orders_exact_software_owned_fact_sentences'
    resumed = SparkRefusalExplainer(allocation_usd='1')
    with pytest.raises(ExplanationProviderError) as caught:
        resumed.explain(proof)
    assert caught.value.evidence['error_type'] == 'SpendCapExceeded'
    assert len(fake_spark.calls) == 1


def test_invalid_paid_output_is_never_replaced_with_template(fake_spark, proof):
    fake_spark.text = 'The model invented a reassuring statement.'
    with pytest.raises(ExplanationOutputError) as caught:
        SparkRefusalExplainer().explain(proof)
    assert caught.value.evidence['validated'] is False
    assert caught.value.evidence['cost_usd'] == '.4' or Decimal(caught.value.evidence['cost_usd']) == Decimal('.4')
    assert fake_spark.text not in str(caught.value)
    assert 'raw' not in caught.value.evidence


def test_provider_error_body_is_not_exposed(fake_spark, proof):
    fake_spark.error = RuntimeError('sensitive provider response body')
    with pytest.raises(ExplanationProviderError) as caught:
        SparkRefusalExplainer().explain(proof)
    assert 'sensitive' not in str(caught.value)
    assert 'sensitive' not in json.dumps(caught.value.evidence)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize('allocation', ['NaN', 'Infinity', '-1', '2.01'])
def test_allocation_guard_precedes_credential_load(allocation):
    with pytest.raises(ValueError, match='between zero and two'):
        SparkRefusalExplainer(allocation_usd=allocation)


def test_no_reason_facts_cannot_reach_provider(proof):
    proof['required_fact_ids'] = []
    with pytest.raises(ValueError, match='reason facts'):
        build_prompt(proof)
