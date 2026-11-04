import json
from decimal import Decimal

import pytest

from .evaluate import prompt_tokens, score_predictions
from .gates import gate_report
from .spark_baseline import CAP, PURPOSE, TaskLedger

CONTEXT = {'pods': ['SGSIN'], 'iso_types': ['22G1'], 'dg_classes': ['3', '8']}
ROW = {'id': 'SYN U0000010'.replace(' ', ''), 'iso_type': '22G1', 'length_ft': 20,
       'gross_mass_kg': 12000, 'pod': 'SGSIN', 'reefer': False,
       'reefer_setpoint_c': None, 'dg': {'un_number': '1263', 'imdg_class': '3'}}


def case(expected=ROW):
    return [{'id': 'test', 'context': CONTEXT, 'expected': expected}]


def prediction(value):
    return [{'id': 'test', 'prediction': json.dumps(value, separators=(',', ':'))}]


def test_exact_and_refusal():
    assert score_predictions(case(), prediction(ROW))['exact_count'] == 1
    refusal = {'refusal': {'code': 'MISSING_FIELD'}}
    result = score_predictions(case(), prediction(refusal))
    assert result['exact_count'] == result['silent_dg_errors'] == 0
    assert score_predictions(case(refusal), prediction(refusal))['exact_count'] == 1


@pytest.mark.parametrize('iso,length', [('22G1', 20), ('42G1', 40), ('L5G1', 45)])
@pytest.mark.parametrize('representation', ['integer', 'decimal', 'exponent'])
def test_length_numeric_equivalents_score_identically(iso, length, representation):
    row = dict(ROW, iso_type=iso, length_ft=length)
    context = dict(CONTEXT, iso_types=[iso])
    raw = json.dumps(row, separators=(',', ':'))
    value = {'integer': str(length), 'decimal': f'{length}.0',
             'exponent': f'{length / 10:g}e1'}[representation]
    raw = raw.replace(f'"length_ft":{length},', f'"length_ft":{value},')
    result = score_predictions([{'id': 'test', 'context': context, 'expected': row}],
                               [{'id': 'test', 'prediction': raw}])
    assert result['exact_count'] == 1
    assert result['malformed_count'] == 0


@pytest.mark.parametrize('length', [True, False, None, '20', float('nan'),
                                   float('inf'), -float('inf'), 20.001])
def test_non_numeric_or_non_equivalent_lengths_are_malformed(length):
    result = score_predictions(case(), prediction(dict(ROW, length_ft=length)))
    assert result['exact_count'] == 0
    assert result['malformed_count'] == 1


def test_inference_prompt_matches_training_completion_prefix():
    from experiments.mvp.reader.evaluate import render_prompt
    from .schema import prompt_messages
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return 'TEMPLATE'
        def encode(self, text, **kwargs):
            return list(text.encode())
    tokenizer = Tokenizer()
    expected = render_prompt(tokenizer, prompt_messages('synthetic', CONTEXT))
    assert prompt_tokens(tokenizer, 'synthetic', CONTEXT) == list(expected.encode())
    assert expected.endswith(' to=user<|message|>')


@pytest.mark.parametrize('dg', [None, {'un_number': '1263', 'imdg_class': '8'},
                               {'un_number': '1993', 'imdg_class': '3'}])
def test_silent_dg_includes_dropped_class_and_wrong_un(dg):
    result = score_predictions(case(), prediction(dict(ROW, dg=dg)))
    assert result['exact_count'] == 0
    assert result['silent_dg_errors'] == 1


def test_missing_duplicate_and_malformed_not_repaired():
    assert score_predictions(case(), [])['missing_count'] == 1
    result = score_predictions(case(), [{'id': 'test', 'prediction': '{"dg":'}])
    assert result['malformed_count'] == 1
    with pytest.raises(ValueError, match='duplicate'):
        score_predictions(case(), prediction(ROW) * 2)


def test_refusal_key_cannot_hide_a_dropped_dg_row():
    malicious = dict(ROW, dg=None, refusal={'code': 'MISSING_FIELD'})
    result = score_predictions(case(), prediction(malicious))
    assert result['silent_dg_errors'] == result['malformed_count'] == 1


def reports(tuned=180, bare=160, spark=190, silent=0):
    common = {'count': 200, 'recorded_prediction_count': 200, 'missing_count': 0,
              'schema_constrained': True, 'dg_count': 30, 'malformed_count': 0,
              'silent_dg_errors': 0, 'model': 'glimmer', 'adapter_sha256': None,
              **{key: 'fixed' for key in ('data_sha256', 'decoder_sha256', 'schema_sha256',
                  'prompt_sha256', 'decoding_policy', 'max_output_tokens', 'evaluator_sha256')}}
    return [dict(common, arm=name, exact_count=n, row_exact=n/200,
                 silent_dg_errors=silent if name == 'tuned' else 0,
                 adapter_sha256='weights' if name == 'tuned' else None)
            for name, n in zip(('tuned', 'untrained', 'spark'), (tuned, bare, spark))]


def test_gate_boundaries_and_silent_error_cut():
    assert gate_report(*reports())['all_pass']
    assert not gate_report(*reports(tuned=179))['I1']
    assert not gate_report(*reports(bare=161))['I3']
    assert not gate_report(*reports(spark=191))['I4']
    assert gate_report(*reports(silent=1))['decision'] == 'cut'


def test_different_mask_or_missing_arm_cannot_pass():
    arms = reports()
    arms[1]['decoder_sha256'] = 'other'
    with pytest.raises(ValueError, match='incomparable'):
        gate_report(*arms)
    arms = reports()
    arms[2]['missing_count'] = 1
    with pytest.raises(ValueError, match='complete'):
        gate_report(*arms)


def test_spark_cap_counts_only_this_task_and_pending_reservations():
    from llm.spark.spend import SpendCapExceeded, SpendLedger
    class Shared:
        _totals = staticmethod(SpendLedger._totals)
        def _records(self):
            return [{'purpose': 'other', 'cost_usd': '10'},
                    {'purpose': PURPOSE, 'phase': 'reservation', 'cost_usd': '1.4',
                     'reservation_id': 'one'}]
        def check_capacity(self, reservation):
            pass
    ledger = TaskLedger(Shared())
    assert ledger.spent() == Decimal('1.4')
    with pytest.raises(SpendCapExceeded, match='cap'):
        ledger.check_capacity(Decimal('0.61'))
    ledger.check_capacity(CAP - ledger.spent())
