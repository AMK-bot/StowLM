"""Pure accounting tests: no ledger files, network, model or spending writes."""
import pytest

from experiments.mvp.pipeline.explain.reconcile_spend import ordered_matches


def settlement(rid, line, purpose='en', cost='.1'):
    row = dict(_line=line, purpose=purpose, cost_usd=cost, cost_kind='measured')
    if rid is not None:
        row['request_id'] = rid
    return row


def incomplete(purpose='en', cost='.1'):
    return dict(status='measured_incomplete', purpose=purpose, cost_usd=cost)


def test_ordered_cost_duplicates_are_disambiguated_by_known_anchor():
    rows = [settlement('a', 1), settlement('anchor', 2, 'sense', '.2'), settlement('b', 3)]
    calls = [incomplete(), dict(incomplete('sense', '.2'), status='measured', request_id='anchor'), incomplete()]
    actual = ordered_matches(calls, rows, seed_line=0, used_ids=['anchor'])
    assert {i: row['request_id'] for i, row in actual.items()} == {0: 'a', 1: 'anchor', 2: 'b'}


def test_settlements_without_request_id_are_never_candidates():
    rows = [settlement(None, 1), settlement('matched', 2)]
    actual = ordered_matches([incomplete()], rows, seed_line=0)
    assert actual[0]['request_id'] == 'matched'


def test_only_idless_matching_settlement_fails_closed():
    with pytest.raises(ValueError, match='absent'):
        ordered_matches([incomplete()], [settlement(None, 1)], seed_line=0)


def test_ambiguous_matching_cost_is_rejected():
    with pytest.raises(ValueError, match='ambiguous'):
        ordered_matches([incomplete()], [settlement('a', 1), settlement('b', 2)], seed_line=0)


def test_seed_boundary_and_already_used_ids_exclude_candidates():
    rows = [settlement('old', 0), settlement('used', 1), settlement('new', 2)]
    actual = ordered_matches([incomplete()], rows, seed_line=0, used_ids=['used'])
    assert actual[0]['request_id'] == 'new'


def test_cost_uses_exact_decimal_equality():
    actual = ordered_matches([incomplete(cost='0.1000')], [settlement('a', 1, cost='.1')], seed_line=0)
    assert actual[0]['request_id'] == 'a'
    with pytest.raises(ValueError, match='absent'):
        ordered_matches([incomplete(cost='.100000001')], [settlement('a', 1)], seed_line=0)


def test_held_call_is_not_repaired_as_a_measured_call():
    with pytest.raises(ValueError, match='finished'):
        ordered_matches([dict(status='held', purpose='en')], [settlement('a', 1)], seed_line=0)


def test_existing_ids_must_match_purpose_and_cost():
    call = dict(incomplete('sense'), status='measured', request_id='a')
    with pytest.raises(ValueError, match='Known request id'):
        ordered_matches([call], [settlement('a', 1)], seed_line=0)


def test_duplicate_known_settlements_fail_closed():
    with pytest.raises(ValueError, match='Repeated'):
        ordered_matches([incomplete()], [settlement('a', 1), settlement('a', 2)], seed_line=0)
