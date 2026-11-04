"""Offline durable spend accounting tests using synthetic records only."""

from decimal import Decimal
import fcntl
import json
from pathlib import Path

import pytest

from llm.spark.spend import SpendCapExceeded, SpendError, SpendLedger, Usage, calculate_cost


@pytest.fixture
def ledger(tmp_path):
    compute = tmp_path / "compute.md"
    compute.write_text("## Spend ledger\n| Date | Source | Amount | What for | Remaining |\n")
    return SpendLedger(tmp_path / "calls.jsonl", compute, Decimal("10"))


def reserve(ledger, amount="3", identifier="test"):
    ledger.append({"phase": "reservation", "reservation_id": identifier,
                   "cost_usd": amount, "cost_kind": "conservative_hold"})


def settle(ledger, amount="0.1", identifier="test", kind="measured"):
    ledger.append({"phase": "settlement", "reservation_id": identifier,
                   "cost_usd": amount, "cost_kind": kind})


def test_decimal_price_cached_subset():
    assert calculate_cost(Usage(1_000_000, 1_000_000, 100_000)) == Decimal("5.39")
    with pytest.raises(SpendError):
        Usage(10, 0, 11)
    with pytest.raises(SpendError):
        Usage(True, 0)


def test_pending_reservation_survives_client_restart(ledger):
    reserve(ledger, "9")
    restarted = SpendLedger(ledger.path, ledger.compute_path, Decimal("10"))
    assert restarted.total_usd() == 9
    assert restarted.remaining_usd() == 1
    with pytest.raises(SpendCapExceeded):
        restarted.check_capacity(Decimal("1.01"))
    restarted.check_capacity(Decimal("1"))


def test_settlement_replaces_reservation_instead_of_double_counting(ledger):
    reserve(ledger)
    settle(ledger)
    assert ledger.total_usd() == Decimal("0.1")
    assert ledger.remaining_usd() == Decimal("9.9")


def test_unknown_outcome_remains_conservative_hold(ledger):
    reserve(ledger)
    settle(ledger, "3", kind="conservative_hold")
    assert ledger.total_usd() == 3
    assert "| $0 |" in ledger.summary_row()
    assert "pending/unknown hold $3" in ledger.summary_row()


def test_summary_copy_does_not_double_count_manual_spend(ledger):
    with ledger.compute_path.open("a") as handle:
        handle.write("| today | Meta Model API | $2 | synthetic manual | $8 |\n")
    reserve(ledger)
    settle(ledger)
    row = ledger.summary_row()
    with ledger.compute_path.open("a") as handle:
        handle.write(row + "\n")
    assert ledger.total_usd() == Decimal("2.1")
    with pytest.raises(SpendCapExceeded):
        ledger.check_capacity(Decimal("8"))


@pytest.mark.parametrize("corruption", [
    "not JSON\n", "{}\n", '{"cost_usd":"NaN"}\n',
    '{"cost_usd":"-1"}\n', '{"cost_usd":"0"}\n\n',
    json.dumps({"phase":"settlement", "reservation_id":"missing", "cost_usd":"1"}) + "\n",
])
def test_corrupt_spend_fails_closed(ledger, corruption):
    ledger.path.write_text(corruption)
    with pytest.raises(SpendError):
        ledger.check_capacity(Decimal("0"))


def test_invalid_manual_ledger_fails_closed(ledger):
    ledger.compute_path.write_text("## Spend ledger\n| today | Meta Model API | unknown | test | |\n")
    with pytest.raises(SpendError):
        ledger.total_usd()


def test_whole_call_lock_is_real_and_reentrant(ledger):
    lock_path = ledger.path.with_suffix(".jsonl.lock")
    with ledger.locked():
        with ledger.locked():
            reserve(ledger)
            with lock_path.open("a") as separate_handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(separate_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    with lock_path.open("a") as separate_handle:
        fcntl.flock(separate_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(separate_handle.fileno(), fcntl.LOCK_UN)


def test_duplicate_settlement_rejected_without_changing_file(ledger):
    reserve(ledger)
    settle(ledger)
    previous = ledger.path.read_bytes()
    with pytest.raises(SpendError):
        settle(ledger)
    assert ledger.path.read_bytes() == previous
