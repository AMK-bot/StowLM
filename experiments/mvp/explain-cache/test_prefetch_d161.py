"""Financial guard regressions: synthetic responses only, never paid requests."""
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.spark.spend import SpendCapExceeded, SpendLedger

spec = importlib.util.spec_from_file_location("prefetch_d161_test_subject", Path(__file__).with_name("prefetch_d161.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LocalClient:
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = 0

    def complete(self, prompt, **kwargs):
        # The per-job decision must remain inside the ordinary ledger's lock.
        assert self.ledger._depth > 0
        assert kwargs["purpose"] == runner.PURPOSE + "/synthetic"
        self.calls += 1
        return SimpleNamespace(text="synthetic recorded response", cost_usd=Decimal("0.01"), request_id="test")


@pytest.fixture
def setup_guard(tmp_path, monkeypatch):
    compute = tmp_path / "compute.md"
    compute.write_text("")
    ledger = SpendLedger(path=tmp_path / "calls.jsonl", compute_path=compute)
    client = LocalClient(ledger)
    monkeypatch.setattr(runner, "install_digest", lambda: "installed")
    monkeypatch.setattr(runner, "EVIDENCE", tmp_path / "evidence")
    return ledger, client, runner.CappedRecorder(client, "synthetic", "installed")


@pytest.mark.parametrize("spent,allowed", [("3.640128", True), ("3.64012801", False), ("5", False)])
def test_full_context_next_call_bound_prevents_overspend(setup_guard, spent, allowed):
    ledger, client, guard = setup_guard
    ledger.append({"purpose": runner.PURPOSE + "/earlier", "cost_usd": spent})
    # The next request's full-context reservation is exactly $1.359872.
    if allowed:
        guard.complete("synthetic", max_output_tokens=16384)
    else:
        with pytest.raises(SpendCapExceeded):
            guard.complete("synthetic", max_output_tokens=16384)
    assert client.calls == int(allowed)
    assert ledger.cap_usd == Decimal("150"), "job cap must not lower the shared project ceiling"


def test_unknown_billing_stops_further_requests(setup_guard):
    ledger, client, guard = setup_guard
    ledger.append({"purpose": runner.PURPOSE + "/earlier", "phase": "reservation", "reservation_id": "unknown",
                   "cost_usd": "1.359872"})
    with pytest.raises(SpendCapExceeded):
        guard.complete("synthetic", max_output_tokens=16384)
    assert client.calls == 0


def test_concurrent_instruction_parser_spend_shares_d161_cap(setup_guard):
    ledger, client, guard = setup_guard
    ledger.append({"purpose": "instruction-parser", "time": "2026-10-30T18:44:12+00:00", "cost_usd": "0.00599375"})
    ledger.append({"purpose": runner.PURPOSE + "/earlier", "cost_usd": "3.640128"})
    with pytest.raises(SpendCapExceeded):
        guard.complete("synthetic", max_output_tokens=16384)
    assert client.calls == 0


def test_changed_install_stops_before_request(setup_guard, monkeypatch):
    _, client, guard = setup_guard
    monkeypatch.setattr(runner, "install_digest", lambda: "new-install")
    with pytest.raises(RuntimeError):
        guard.complete("synthetic", max_output_tokens=16384)
    assert client.calls == 0
