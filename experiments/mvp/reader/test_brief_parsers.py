"""Reader provenance and spend guards, with no paid calls or inference."""
from decimal import Decimal
import json
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.reader.pipeline import (
    GlimmerParser, ParserOutputError, ParserUnavailableError, PURPOSE, SparkParser,
)
from experiments.mvp.reader.schema import answer_envelope
from llm.instructions.schema import Refusal
from llm.spark.client import MODEL
from llm.spark.spend import SpendCapExceeded, SpendLedger, Usage


CONTEXT = {'bays': [1, 3], 'max_tiers': {'hold': 4, 'deck': 2},
           'hatch_covers': ['HC'], 'has_accommodation': False}
VALID = '{"schema_version":"stowlm.reader/2","constraint":{"type":"close_bay","bay":3}}'
DIGEST = 'ab' * 32


@pytest.fixture
def fake_spark(monkeypatch, tmp_path):
    """Real allocation ledger in a temporary directory, synthetic paid client."""
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    state = NS(text=VALID, cost=Decimal('.4'), calls=[])

    class Client:
        def __init__(self, **kwargs):
            self.ledger = SpendLedger(path=tmp_path / 'calls.jsonl', compute_path=compute)

        def complete(self, prompt, **kwargs):
            # Direct entries exercise allocation accounting without HTTP or the
            # production shared ledger. A denied append does not make a call.
            self.ledger.append({'purpose': kwargs['purpose'], 'cost_usd': str(state.cost)})
            state.calls.append(kwargs)
            return NS(text=state.text, request_id='fake-request', cost_usd=state.cost,
                      usage=Usage(100, 50))

    monkeypatch.setattr('experiments.mvp.reader.baselines.load_credentials', lambda: None)
    monkeypatch.setattr('llm.spark.client.SparkClient', Client)
    return state


def test_spark_default_identity_and_original_allocation(fake_spark):
    parser = SparkParser()
    answer, evidence = parser.parse('Close bay 3.', CONTEXT)
    assert answer.bay == 3
    assert parser.allocation_usd == Decimal('1')
    assert fake_spark.calls[0]['purpose'] == PURPOSE
    assert evidence['parser'] == 'SparkParser'
    assert evidence['model'] == MODEL
    assert evidence['adapter_sha256'] is None
    assert evidence['raw'] == VALID
    assert evidence['cost_usd'] == '.4' or Decimal(evidence['cost_usd']) == Decimal('.4')


def test_spark_repeated_calls_charge_once_and_resume_before_first_call(fake_spark):
    parser = SparkParser(purpose='reader-d220-brief', allocation_usd='2')
    # Another process's settled spend can arrive after this parser is created.
    parser.client.ledger.append({'purpose': parser.purpose, 'cost_usd': '1'})
    parser.parse('Close bay 3.', CONTEXT)
    parser.parse('Close bay 3.', CONTEXT)
    with pytest.raises(SpendCapExceeded):
        parser.parse('Close bay 3.', CONTEXT)
    assert len(fake_spark.calls) == 2
    assert parser.prior == Decimal('1')
    # New parser/ledger instances cannot reset the allocation in this process.
    resumed = SparkParser(purpose=parser.purpose, allocation_usd='2')
    with pytest.raises(SpendCapExceeded):
        resumed.parse('Close bay 3.', CONTEXT)
    assert resumed.prior == Decimal('1.8')


def test_purpose_allocation_is_separate_from_prior_other_jobs(fake_spark):
    parser = SparkParser(purpose='reader-d220-brief', allocation_usd='.5')
    parser.client.ledger.append({'purpose': PURPOSE, 'cost_usd': '2'})
    parser.parse('Close bay 3.', CONTEXT)
    assert parser.prior == 0


@pytest.mark.parametrize('text', ['not JSON', '```json\n' + VALID + '\n```',
    VALID.replace('"bay":3', '"bay":99'),
    VALID.replace('"bay":3', '"bay":3,"bay":1')])
def test_spark_malformed_keeps_provider_evidence_without_schema_error_text(fake_spark, text):
    fake_spark.text = text
    with pytest.raises(ParserOutputError) as caught:
        SparkParser().parse('Close bay 3.', CONTEXT)
    assert caught.value.evidence['raw'] == text
    assert caught.value.evidence['request_id'] == 'fake-request'
    assert caught.value.evidence['parser'] == 'SparkParser'
    assert text not in str(caught.value)
    assert caught.value.__cause__ is None


def test_glimmer_stub_never_falls_back():
    with pytest.raises(ParserUnavailableError):
        GlimmerParser().parse('Close bay 3.', CONTEXT)


def test_glimmer_callback_uses_same_strict_schema_and_adapter_provenance():
    seen = []
    def generate(sentence, context):
        seen.append((sentence, context))
        return VALID
    parser = GlimmerParser(generate, adapter_sha256=DIGEST.upper(), model='local-glimmer')
    answer, evidence = parser.parse('Close bay 3.', CONTEXT)
    assert answer_envelope(answer) == json.loads(VALID)
    assert seen == [('Close bay 3.', CONTEXT)]
    assert evidence == {'parser': 'GlimmerParser', 'model': 'local-glimmer',
                        'adapter_sha256': DIGEST, 'raw': VALID}


def test_glimmer_malformed_is_not_repaired_or_replaced_with_refusal():
    malformed = VALID.replace('"bay":3', '"bay":99')
    parser = GlimmerParser(lambda *_: malformed, adapter_sha256=DIGEST)
    with pytest.raises(ParserOutputError) as caught:
        parser.parse('Close bay 3.', CONTEXT)
    assert caught.value.evidence['raw'] == malformed
    assert caught.value.evidence['adapter_sha256'] == DIGEST


def test_glimmer_refusal_stays_refusal():
    refusal = json.dumps({'schema_version': 'stowlm.reader/2', 'refusal':
        {'refused': True, 'code': 'loosening', 'reason': 'Would relax a restriction.'}})
    answer, _ = GlimmerParser(lambda *_: refusal, adapter_sha256=DIGEST).parse('Open bay 3.', CONTEXT)
    assert isinstance(answer, Refusal)


def test_glimmer_callback_requires_adapter_identity():
    with pytest.raises(ValueError, match='requires adapter_sha256'):
        GlimmerParser(lambda *_: VALID)
    with pytest.raises(ValueError, match='hexadecimal'):
        GlimmerParser(lambda *_: VALID, adapter_sha256='unrecorded')


@pytest.mark.parametrize('allocation', ['NaN', '-1', 'Infinity'])
def test_spark_rejects_invalid_allocation_before_loading_credentials(allocation):
    with pytest.raises(ValueError, match='finite and nonnegative'):
        SparkParser(allocation_usd=allocation)
