"""Budget preflight is local and refuses over-allocation before HTTP."""
from decimal import Decimal

import pytest

from llm.spark.planner_budget import TokenEstimate, allocation_budget, guard_run
from llm.spark.spend import SpendCapExceeded, SpendLedger


def ledger(tmp_path):
    compute = tmp_path / 'compute.md'
    compute.write_text('## Spend ledger\n| Date | Source | Amount | What | Remaining |\n| now | Meta Model API | $10 | prior | $140 |\n')
    return SpendLedger(tmp_path / 'calls.jsonl', compute_path=compute)


def test_run_guard_sums_whole_panel_and_prior_spend(tmp_path):
    book = ledger(tmp_path)
    estimate = TokenEstimate(1, 16, Decimal('26'))
    assert guard_run([estimate], book) == Decimal('50')
    with pytest.raises(SpendCapExceeded):
        guard_run([estimate, estimate], book)
    assert not book.path.exists()


def test_unknown_billing_holds_reduce_allocation(tmp_path):
    book = ledger(tmp_path)
    book.append({'phase': 'reservation', 'reservation_id': 'pending', 'cost_usd': '49'})
    with pytest.raises(SpendCapExceeded):
        guard_run([TokenEstimate(1, 16, Decimal('2'))], book)


@pytest.mark.parametrize('allocation', ['NaN', '-1', 'Infinity'])
def test_bad_allocation_is_rejected(tmp_path, allocation):
    with pytest.raises(ValueError):
        guard_run([], ledger(tmp_path), allocation_usd=Decimal(allocation))


def test_job_allocation_is_incremental_retained_and_never_changes_project_cap(tmp_path):
    book = ledger(tmp_path)
    for number in range(2):
        with allocation_budget(book, allocation_usd=Decimal('5')):
            assert guard_run([], book, allocation_usd=Decimal('5')) == Decimal(5 - number * 2)
            book.append({'cost_usd': '2'})
            assert book.cap_usd == Decimal('150')
    with allocation_budget(book, allocation_usd=Decimal('5')):
        with pytest.raises(SpendCapExceeded):
            book.check_capacity(Decimal('1.01'))
        with pytest.raises(SpendCapExceeded):
            book.append({'cost_usd': '1.01'})
    assert book.total_usd() == Decimal('14')
    assert 'cap-usd=150;' in book.compute_path.read_text()
    restarted = SpendLedger(book.path, compute_path=book.compute_path)
    restarted.check_capacity(Decimal('100'))
    assert restarted.cap_usd == Decimal('150')


def test_nested_context_uses_tighter_allocation_and_restores_guards(tmp_path):
    book = ledger(tmp_path)
    with allocation_budget(book, allocation_usd=Decimal('5')):
        with allocation_budget(book, allocation_usd=Decimal('60')):
            with pytest.raises(SpendCapExceeded):
                book.check_capacity(Decimal('6'))
        with allocation_budget(book, allocation_usd=Decimal('1')):
            with pytest.raises(SpendCapExceeded):
                book.check_capacity(Decimal('2'))
        book.check_capacity(Decimal('5'))
    assert 'check_capacity' not in book.__dict__
    assert 'append' not in book.__dict__
    book.check_capacity(Decimal('100'))


def test_unknown_hold_reduces_job_allocation_and_exceptions_restore_guards(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(RuntimeError), allocation_budget(book, allocation_usd=Decimal('5')):
        book.append({'phase': 'reservation', 'reservation_id': 'r', 'cost_usd': '4'})
        book.append({'phase': 'settlement', 'reservation_id': 'r', 'cost_usd': '4',
                     'cost_kind': 'conservative_hold'})
        raise RuntimeError('aborted job')
    assert 'check_capacity' not in book.__dict__
    with allocation_budget(book, allocation_usd=Decimal('5')):
        assert guard_run([], book) == Decimal('1')
        with pytest.raises(SpendCapExceeded):
            book.check_capacity(Decimal('2'))


def test_job_guard_still_enforces_durable_project_remaining(tmp_path):
    book = ledger(tmp_path)
    book.append({'cost_usd': '139'})
    with allocation_budget(book, allocation_usd=Decimal('5')):
        assert guard_run([], book) == Decimal('1')
        with pytest.raises(SpendCapExceeded):
            book.check_capacity(Decimal('2'))


def test_external_spend_after_job_start_counts_conservatively(tmp_path):
    book = ledger(tmp_path)
    with allocation_budget(book, allocation_usd=Decimal('5')):
        book.append({'cost_usd': '1'})
    other = SpendLedger(book.path, compute_path=book.compute_path)
    other.append({'cost_usd': '3'})
    with allocation_budget(book, allocation_usd=Decimal('5')):
        assert guard_run([], book) == Decimal('1')
        with pytest.raises(SpendCapExceeded):
            book.check_capacity(Decimal('2'))
