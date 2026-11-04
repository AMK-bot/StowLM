"""Adoption fixtures use synthetic metrics only; no sealed input is opened."""
import copy

import pytest

from experiments.mvp.reader.v3.adoption import adoption_report
from experiments.mvp.reader.v3.gates import gate_report


def metrics(correct=124, accepted=0, decoder='current'):
    return {'count': 128, 'exact_match_count': correct,
            'exact_match_pct': 100*correct/128, 'loosening_count': 48,
            'false_acceptance_count': accepted, 'missing_count': 0,
            'data_sha256': 'synthetic-cases', 'schema_constrained': True,
            'answer_language': 'reader-schema-v3', 'decoder_sha256': decoder,
            'domain_words_sha256': 'synthetic-vocabulary',
            'details': [{'id': f'fixture-{i}'} for i in range(128)]}


def test_spark_is_fixed_reference_and_current_local_mask_must_match():
    report = gate_report(metrics(), metrics(80), metrics(126, decoder='legacy'))
    assert report['all_pass']
    with pytest.raises(ValueError, match='identical decoder'):
        gate_report(metrics(), metrics(80, decoder='different'), metrics(126, decoder='legacy'))


def test_running_does_not_wait_for_v2_but_adoption_does():
    report = adoption_report(metrics(), metrics(80), metrics(126, decoder='legacy'))
    assert report['run_v3'] is True and report['adopt_v3'] is False
    assert report['status'] == 'pending_completed_v2_reference'


@pytest.mark.parametrize('correct,accepted,v2_correct,expected', [
    (124, 0, 123, True), (124, 0, 124, True), (124, 0, 125, False),
    (122, 0, 120, False), (124, 1, 123, False), (115, 0, 110, False),
])
def test_adoption_needs_all_gates_and_no_comparator_regression(correct, accepted, v2_correct, expected):
    result = adoption_report(metrics(correct, accepted), metrics(80),
                             metrics(126, decoder='legacy'), metrics(v2_correct))
    assert result['adopt_v3'] is expected


@pytest.mark.parametrize('change', ['hash', 'ids', 'mask', 'vocabulary', 'missing'])
def test_adoption_rejects_incomparable_v2_reference(change):
    reference = copy.deepcopy(metrics())
    if change == 'hash':
        reference['data_sha256'] = 'other-seal'
    elif change == 'ids':
        reference['details'][0]['id'] = 'other-case'
    elif change == 'mask':
        reference['decoder_sha256'] = 'old'
    elif change == 'vocabulary':
        reference['domain_words_sha256'] = 'old'
    else:
        reference['missing_count'] = 1
    with pytest.raises(ValueError):
        adoption_report(metrics(), metrics(80), metrics(126, decoder='legacy'), reference)
