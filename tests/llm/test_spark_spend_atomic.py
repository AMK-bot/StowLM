"""Offline regressions for the authoritative Spark budget and compute ledger."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import subprocess
import sys

import pytest

from llm.spark.spend import SpendCapExceeded, SpendError, SpendLedger


def budget(tmp_path, name="calls.jsonl", cap="10"):
    compute = tmp_path / "compute.md"
    if not compute.exists():
        compute.write_text("# Budget\nUnrelated text.\n\n## Spend ledger\n\n"
                           "| Date | Source | Amount | What for | Remaining |\n"
                           "|---|---|---|---|---|\n"
                           "| today | Modal | $3 | Preserve this exactly | $7 |\n"
                           "\n## Later section\nUnrelated tail.\n")
    return SpendLedger(tmp_path / name, compute, Decimal(cap))


def reserve(ledger, amount="6", identifier="r"):
    ledger.append({"phase": "reservation", "reservation_id": identifier,
                   "cost_usd": amount, "cost_kind": "conservative_hold"})


HISTORICAL_CALL = ("e248b23ad3bc436cbd50bf14102c5dd2", Decimal("0.00057025"))  # plan 1.8 test call, 2026-10-27


def repository_ledger(tmp_path, alternate=False, manual_spend=Decimal("0")):
    """Recreate the repository binding without reading changing live spend files."""
    reservation, cost = HISTORICAL_CALL
    compute = tmp_path / "docs/compute.md"
    compute.parent.mkdir(parents=True)
    compute.write_text(
        "# Compute\n\n## Spend ledger\n\n"
        "| Date | Source | Amount | What for | Remaining |\n"
        "|---|---|---|---|---|\n"
        f"| 2026-10-27 | Meta Model API | ${cost} | Historical call "
        "(spark-jsonl; ledger=../experiments/spark-spend/calls.jsonl; "
        "cap-usd=150; historical) | |\n"
        f"| today | Meta Model API | ${manual_spend} | Other Meta API calls | |\n"
        "| today | Modal | $3 | Separate budget | |\n"
    )
    calls = tmp_path / "experiments/spark-spend/calls.jsonl"
    calls.parent.mkdir(parents=True)
    rows = [
        {"phase": "reservation", "reservation_id": reservation, "cost_usd": "1"},
        {"phase": "settlement", "reservation_id": reservation, "cost_usd": str(cost)},
        {"phase": "reservation", "reservation_id": "settled", "cost_usd": "2"},
        {"phase": "settlement", "reservation_id": "settled", "cost_usd": "0.25"},
        {"phase": "reservation", "reservation_id": "pending", "cost_usd": "3"},
    ]
    calls.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return SpendLedger(tmp_path / "alternate.jsonl" if alternate else calls, compute)


def independent_total(calls):
    """Recount the JSONL from scratch: each reservation counts once, at its latest row (a settlement replaces it)."""
    latest = {}
    for line in calls.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row["reservation_id"]] = Decimal(row["cost_usd"])
    return latest, sum(latest.values(), Decimal(0))


def historical_compute_rows(compute):
    return [line for line in compute.read_text().splitlines()
            if "spark-jsonl" in line and "historical" in line]


@pytest.mark.parametrize("manual_spend", [Decimal("0"), Decimal("0.012700")])
def test_actual_repository_call_is_counted_once(tmp_path, manual_spend):
    ledger = repository_ledger(tmp_path, manual_spend=manual_spend)
    latest, total = independent_total(ledger.path)
    # Reservations and settlements count once; the historical compute row is not counted again.
    assert ledger.total_usd() == total + manual_spend
    reservation, cost = HISTORICAL_CALL
    assert latest[reservation] == cost
    rows = historical_compute_rows(ledger.compute_path)
    assert len(rows) == 1 and f"${cost} " in rows[0]
    assert ledger._manual_spend() == manual_spend
    assert ledger.remaining_usd() == ledger.cap_usd - total - manual_spend
    assert ledger.cap_usd == Decimal("150")


def test_each_append_updates_only_spark_compute_rows(tmp_path):
    ledger = budget(tmp_path)
    before = ledger.compute_path.read_text()
    reserve(ledger)
    assert "pending/unknown hold $6" in ledger.compute_path.read_text()
    ledger.append({"phase": "settlement", "reservation_id": "r", "cost_usd": "0.1"})
    after = ledger.compute_path.read_text()
    assert "| Meta Model API | $0.1 |" in after
    assert "pending/unknown hold $0" in after
    assert "| $9.9 |" in after
    assert "".join(line for line in after.splitlines(keepends=True) if "spark-jsonl" not in line) == before


def test_different_requested_paths_share_one_authoritative_budget(tmp_path):
    first = budget(tmp_path, "first.jsonl")
    second = budget(tmp_path, "second.jsonl")
    reserve(first)
    assert second.total_usd() == 6
    assert second.path.resolve() == first.path.resolve()
    with pytest.raises(SpendCapExceeded):
        second.check_capacity(Decimal("5"))
    with pytest.raises(SpendCapExceeded):
        reserve(second, "5", "second")
    assert not (tmp_path / "second.jsonl").exists()


def test_concurrent_clients_with_different_paths_cannot_overreserve(tmp_path):
    ledgers = [budget(tmp_path, f"{i}.jsonl") for i in range(2)]

    def attempt(pair):
        index, ledger = pair
        try:
            with ledger.locked():
                ledger.check_capacity(Decimal("6"))
                reserve(ledger, identifier=str(index))
            return True
        except SpendCapExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, enumerate(ledgers))) == [False, True]
    assert ledgers[0].total_usd() == ledgers[1].total_usd() == 6


def test_preexisting_secondary_ledger_is_not_silently_ignored(tmp_path):
    first = budget(tmp_path, "first.jsonl")
    reserve(first)
    other = tmp_path / "other.jsonl"
    other.write_text('{"cost_usd":"7"}\n')
    second = budget(tmp_path, "other.jsonl")
    with pytest.raises(SpendError):
        second.total_usd()


def test_lower_ceiling_is_shared_and_persists_across_restart(tmp_path):
    first = budget(tmp_path, cap="7")
    reserve(first)
    restarted = budget(tmp_path, cap="150")
    with pytest.raises(SpendCapExceeded):
        reserve(restarted, "2", "later")
    assert restarted.remaining_usd() == 1


def test_first_remaining_call_respects_persisted_lower_ceiling(tmp_path):
    reserve(budget(tmp_path, cap="7"))
    assert budget(tmp_path, cap="150").remaining_usd() == 1


@pytest.mark.parametrize("manual_spend", [Decimal("0"), Decimal("0.012700")])
def test_historical_repository_binding_covers_alternate_path(tmp_path, manual_spend):
    ledger = repository_ledger(tmp_path, alternate=True, manual_spend=manual_spend)
    bound = tmp_path / "experiments/spark-spend/calls.jsonl"
    latest, total = independent_total(bound)
    assert ledger.total_usd() == total + manual_spend
    # The binding is resolved on first use: the empty alternate path adopts the bound JSONL.
    assert ledger.path.resolve() == bound.resolve()
    assert not (tmp_path / "alternate.jsonl").exists()
    assert latest[HISTORICAL_CALL[0]] == HISTORICAL_CALL[1]
    # Only the unbound manual row is additional spend; the bound historical row is not counted twice.
    assert historical_compute_rows(ledger.compute_path)
    assert ledger._manual_spend() == manual_spend


def test_missing_historical_repository_ledger_fails_closed(tmp_path):
    ledger = repository_ledger(tmp_path)
    ledger.path.unlink()
    with pytest.raises(SpendError):
        ledger.total_usd()


def test_compute_table_without_final_newline_stays_well_formed(tmp_path):
    ledger = budget(tmp_path)
    ledger.compute_path.write_text("## Spend ledger\n| today | Modal | $3 | Keep | $7 |")
    reserve(ledger)
    assert "| $7 |\n| " in ledger.compute_path.read_text()


def test_failed_compute_sync_prevents_reservation_from_being_sent(tmp_path, monkeypatch):
    ledger = budget(tmp_path)
    import llm.spark.spend as spend

    def fail_replace(*args):
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(spend.os, "replace", fail_replace)
    with pytest.raises((OSError, SpendError)):
        reserve(ledger)
    assert not ledger.path.exists() or ledger.path.read_bytes() == b""


def test_compute_symlink_alias_uses_same_authoritative_budget(tmp_path):
    first = budget(tmp_path)
    alias = tmp_path / "alias.md"
    alias.symlink_to(first.compute_path)
    second = SpendLedger(tmp_path / "alternative.jsonl", alias, Decimal("10"))
    reserve(first)
    assert second.total_usd() == 6
    with pytest.raises(SpendCapExceeded):
        reserve(second, "6", "other")
    assert alias.is_symlink()


def test_compute_budget_lock_excludes_separate_process(tmp_path):
    ledger = budget(tmp_path)
    lock = ledger.compute_path.with_suffix(".md.spark.lock")
    probe = """
import fcntl, sys
with open(sys.argv[1], 'a') as handle:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)
sys.exit(1)
"""
    with ledger.locked():
        result = subprocess.run([sys.executable, "-c", probe, str(lock)], timeout=5, check=False)
    assert result.returncode == 0


def test_missing_authoritative_ledger_fails_closed(tmp_path):
    first = budget(tmp_path)
    reserve(first)
    first.path.unlink()
    with pytest.raises(SpendError):
        budget(tmp_path, "other.jsonl").check_capacity(Decimal("1"))


def test_post_append_sync_failure_preserves_hold_for_other_clients(tmp_path, monkeypatch):
    ledger = budget(tmp_path)
    import llm.spark.spend as spend
    original = spend.os.replace
    calls = 0

    def fail_second_replace(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated post-append summary failure")
        return original(*args)

    monkeypatch.setattr(spend.os, "replace", fail_second_replace)
    with pytest.raises(OSError):
        reserve(ledger)
    other = budget(tmp_path, "other.jsonl")
    assert other.total_usd() == 6
    with pytest.raises(SpendCapExceeded):
        reserve(other, "6", "other")
    other.sync_compute()
    assert "pending/unknown hold $6" in other.compute_path.read_text()
