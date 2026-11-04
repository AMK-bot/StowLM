"""Job allocation counts only job-owned spend plus monotone foreign increases."""
from decimal import Decimal

import pytest

from llm.spark.planner_budget import allocation_budget, guard_run
from llm.spark.spend import SpendCapExceeded, SpendError, SpendLedger

FIVE = Decimal('5')


def ledger(tmp_path):
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    return SpendLedger(tmp_path / 'calls.jsonl', compute_path=compute)


def other(book):
    return SpendLedger(book.path, compute_path=book.compute_path)


def test_release_of_pre_job_hold_adds_no_headroom(tmp_path):
    # Reviewer reproduction (w2-rev-spark): previously the job could spend $15.
    book = ledger(tmp_path)
    book.append({'phase': 'reservation', 'reservation_id': 'old', 'cost_usd': '10'})
    with allocation_budget(book, allocation_usd=FIVE):
        assert guard_run([], book, allocation_usd=FIVE) == FIVE
        book.append({'cost_usd': '5'})
    other(book).append({'phase': 'settlement', 'reservation_id': 'old',
                        'cost_usd': '0', 'cost_kind': 'measured'})
    with allocation_budget(book, allocation_usd=FIVE):
        assert guard_run([], book, allocation_usd=FIVE) == Decimal(0)
        with pytest.raises(SpendCapExceeded):
            book.append({'cost_usd': '10'})
        with pytest.raises(SpendCapExceeded):
            book.check_capacity(Decimal('0.01'))
    assert book.total_usd() == FIVE and book.cap_usd == Decimal('150')


def test_job_settling_a_pre_job_hold_adds_no_headroom(tmp_path):
    book = ledger(tmp_path)
    book.append({'phase': 'reservation', 'reservation_id': 'old', 'cost_usd': '4'})
    with allocation_budget(book, allocation_usd=FIVE):
        book.append({'cost_usd': '3'})
        book.append({'phase': 'settlement', 'reservation_id': 'old', 'cost_usd': '0'})
        assert guard_run([], book) == Decimal('2')


def test_own_unsettled_reservation_stays_charged(tmp_path):
    book = ledger(tmp_path)
    with allocation_budget(book, allocation_usd=FIVE):
        book.append({'phase': 'reservation', 'reservation_id': 'mine', 'cost_usd': '4'})
    with allocation_budget(book, allocation_usd=FIVE):
        assert guard_run([], book) == Decimal('1')
        with pytest.raises(SpendCapExceeded):
            book.check_capacity(Decimal('1.01'))


def test_own_settlement_replaces_own_reservation(tmp_path):
    book = ledger(tmp_path)
    with allocation_budget(book, allocation_usd=FIVE):
        book.append({'phase': 'reservation', 'reservation_id': 'mine', 'cost_usd': '4'})
        book.append({'phase': 'settlement', 'reservation_id': 'mine', 'cost_usd': '1.5',
                     'cost_kind': 'measured'})
        assert guard_run([], book) == Decimal('3.5')
        book.check_capacity(Decimal('3.5'))
        with pytest.raises(SpendCapExceeded):
            book.check_capacity(Decimal('3.51'))


def test_foreign_settlement_of_own_reservation_keeps_hold(tmp_path):
    book = ledger(tmp_path)
    with allocation_budget(book, allocation_usd=FIVE):
        book.append({'phase': 'reservation', 'reservation_id': 'mine', 'cost_usd': '4'})
    other(book).append({'phase': 'settlement', 'reservation_id': 'mine', 'cost_usd': '0'})
    with allocation_budget(book, allocation_usd=FIVE):
        assert guard_run([], book) == Decimal('1')


# The context holds the project lock, so other ledgers write between contexts.
def test_foreign_spend_during_job_never_increases_allowance(tmp_path):
    book = ledger(tmp_path)
    foreign = other(book)
    with allocation_budget(book, allocation_usd=FIVE):
        book.append({'cost_usd': '1'})
    foreign.append({'phase': 'reservation', 'reservation_id': 'f', 'cost_usd': '2'})
    with allocation_budget(book, allocation_usd=FIVE):
        assert guard_run([], book) == Decimal('2')
    foreign.append({'phase': 'settlement', 'reservation_id': 'f', 'cost_usd': '0',
                    'cost_kind': 'measured'})
    with allocation_budget(book, allocation_usd=FIVE):
        # A foreign post-start call counts at its measured cost once settled.
        assert guard_run([], book) == Decimal('4')
    foreign.append({'cost_usd': '0.5'})
    with allocation_budget(book, allocation_usd=FIVE):
        assert guard_run([], book) == Decimal('3.5')
        with pytest.raises(SpendCapExceeded):
            book.append({'cost_usd': '3.6'})


def test_settled_foreign_calls_do_not_pin_their_holds(tmp_path):
    # Regression (29 Oct): seven cheap foreign calls, each reserving a large
    # context-sized hold, must not exhaust the job's allocation once settled.
    book = ledger(tmp_path)
    foreign = other(book)
    with allocation_budget(book, allocation_usd=Decimal('15')):
        pass
    for index in range(7):
        foreign.append({'phase': 'reservation', 'reservation_id': f'f{index}', 'cost_usd': '1.5'})
        foreign.append({'phase': 'settlement', 'reservation_id': f'f{index}', 'cost_usd': '0.01',
                        'cost_kind': 'measured'})
    with allocation_budget(book, allocation_usd=Decimal('15')):
        assert guard_run([], book) == Decimal('14.93')


def test_foreign_release_of_a_pre_start_hold_still_adds_nothing(tmp_path):
    book = ledger(tmp_path)
    foreign = other(book)
    foreign.append({'phase': 'reservation', 'reservation_id': 'old', 'cost_usd': '3'})
    with allocation_budget(book, allocation_usd=FIVE):
        book.append({'cost_usd': '4'})
    foreign.append({'phase': 'settlement', 'reservation_id': 'old', 'cost_usd': '0', 'cost_kind': 'measured'})
    with allocation_budget(book, allocation_usd=FIVE):
        assert guard_run([], book) == Decimal('1')


def test_interleaved_jobs_each_count_the_other(tmp_path):
    first = ledger(tmp_path)
    second = other(first)
    with allocation_budget(first, allocation_usd=FIVE):
        pass
    with allocation_budget(second, allocation_usd=FIVE):
        pass
    with allocation_budget(first, allocation_usd=FIVE):
        first.append({'cost_usd': '3'})
    with allocation_budget(second, allocation_usd=FIVE):
        with pytest.raises(SpendCapExceeded):
            second.append({'cost_usd': '2.01'})
        second.append({'cost_usd': '2'})
        assert guard_run([], second) == Decimal(0)
    with allocation_budget(first, allocation_usd=FIVE):
        assert guard_run([], first) == Decimal(0)


def test_resume_seed_and_legacy_attribute(tmp_path):
    book = ledger(tmp_path)
    book.append({'cost_usd': '7'})  # Earlier project spend, not this job's.
    resumed = other(book)
    with allocation_budget(resumed, allocation_usd=FIVE, prior_job_usd=Decimal('4')):
        assert guard_run([], resumed) == Decimal('1')
        # A lower reseed in a later context cannot add headroom.
        with allocation_budget(resumed, allocation_usd=FIVE, prior_job_usd=Decimal('0')):
            assert guard_run([], resumed) == Decimal('1')
    legacy = other(book)
    legacy._spark_allocation_start_usd = Decimal('0')
    with pytest.raises(SpendError, match='prior_job_usd'):
        with allocation_budget(legacy, allocation_usd=FIVE):
            pass
    with pytest.raises(ValueError):
        with allocation_budget(other(book), allocation_usd=FIVE, prior_job_usd=Decimal('-1')):
            pass


def test_nested_guards_record_each_own_line_once(tmp_path):
    book = ledger(tmp_path)
    with allocation_budget(book, allocation_usd=FIVE):
        with allocation_budget(book, allocation_usd=Decimal('60')):
            book.append({'phase': 'reservation', 'reservation_id': 'n', 'cost_usd': '2'})
            book.append({'phase': 'settlement', 'reservation_id': 'n', 'cost_usd': '1'})
        assert guard_run([], book) == Decimal('4')
    assert book._spark_job.owned == {0, 1}
    assert 'append' not in book.__dict__
