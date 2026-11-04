"""Offline project-budget regressions: no Modal client or provider calls."""
from decimal import Decimal
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from llm.rl import modal_grpo as grpo


def test_cli_credit_gate_precedes_project_ledger_and_modal(monkeypatch, tmp_path):
    instance = tmp_path / "instance.json"
    instance.write_text("{}")
    monkeypatch.delenv("MODAL_CREDIT_READY", raising=False)
    monkeypatch.setattr(sys, "argv", ["grpo", "--instance", str(instance), "--run-id", "gate", "--cap", "3"])
    monkeypatch.setattr(grpo.subprocess, "check_output", lambda *a, **kw: "a" * 40)
    def forbidden(*a, **kw):
        raise AssertionError("paid definitions constructed before credit gate")
    monkeypatch.setattr(grpo, "build_modal_app", forbidden)
    with pytest.raises(RuntimeError, match="credit"):
        grpo.main()


@pytest.fixture
def accounting(tmp_path, monkeypatch):
    from llm.rl.spend import ModalSpendLedger
    monkeypatch.setenv("MODAL_CREDIT_READY", "1")
    compute = tmp_path / "compute.md"
    compute.write_text("""# Compute
## Budgets
| Source | Total | Planned use | Allocation |
| Modal | $100 | GRPO | ~$80 GRPO; ~$20 reserve |
## Spend ledger
| Date | Source | Amount | What for | Remaining |
|---|---|---|---|---|
| 2026-10-27 | Modal | $0.08 metered ($0.00 billed: free credits) | historical CPU | unknown |
| 2026-10-27 | Meta Model API | $0.25 | unrelated Spark row | $149.75 |
""")
    return ModalSpendLedger(tmp_path / "spend.jsonl", compute_path=compute)


def reserve(ledger, cap="3", run_id="trial"):
    return ledger.reserve(run_id, Decimal(cap), compute_estimate=Decimal("1"), overhead_usd=Decimal(".25"))


@pytest.mark.parametrize("flag", [None, "0", "true", "yes", "01", " 1"])
def test_credit_gate_closed_by_default_and_exact_environment_value(accounting, monkeypatch, flag):
    from llm.rl.spend import ModalCreditNotReady
    if flag is None:
        monkeypatch.delenv("MODAL_CREDIT_READY", raising=False)
    else:
        monkeypatch.setenv("MODAL_CREDIT_READY", flag)
    before = accounting.compute_path.read_bytes()
    with pytest.raises(ModalCreditNotReady):
        reserve(accounting)
    assert not accounting.path.exists()
    assert accounting.compute_path.read_bytes() == before


def test_shared_budget_counts_fresh_run_ids_and_historical_modal_spend(accounting):
    from llm.rl.spend import ModalSpendLedger, SpendCapExceeded
    reserve(accounting, "40", "first")
    # Fresh local path and run identifier cannot create a fresh project budget.
    fresh = ModalSpendLedger(accounting.path.parent / "alternate.jsonl", accounting.compute_path, cap_usd=Decimal("1000"))
    with pytest.raises(SpendCapExceeded):
        reserve(fresh, "40", "second")
    assert fresh.path == accounting.path
    assert fresh.cap_usd == Decimal("80")
    assert fresh.total_usd() == Decimal("40.08")
    assert fresh.remaining_usd() == Decimal("39.92")


def test_managed_row_written_before_modal_attempt_and_preserves_other_rows(accounting):
    before = accounting.compute_path.read_text()
    record = reserve(accounting)
    after = accounting.compute_path.read_text()
    assert "conservative pending/unknown hold $3" in after
    assert "cap-usd=80" in after
    assert [line for line in before.splitlines() if line] == [
        line for line in after.splitlines() if line and "modal-jsonl" not in line]
    assert json.loads(accounting.path.read_text())["reservation_id"] == record["reservation_id"]


def test_pending_unknown_failed_attempts_are_never_released_without_final_itemization(accounting):
    from llm.rl.spend import SpendError
    record = reserve(accounting)
    charges = {key: Decimal("0") for key in ("gpu", "cpu", "memory", "lifecycle", "storage", "egress")}
    with pytest.raises(SpendError, match="Final itemized"):
        accounting.reconcile(record["reservation_id"], charges=charges, billing_final=False)
    with pytest.raises(SpendError, match="Final itemized"):
        accounting.reconcile(record["reservation_id"], charges={"gpu": Decimal(".1")}, billing_final=True)
    assert accounting.total_usd() == Decimal("3.08")
    charges.update(gpu=Decimal(".5"), storage=Decimal(".1"), egress=Decimal(".2"))
    accounting.reconcile(record["reservation_id"], charges=charges, billing_final=True)
    assert accounting.total_usd() == Decimal(".88")
    assert "conservative pending/unknown hold $0" in accounting.compute_path.read_text()
    with pytest.raises(SpendError):
        accounting.reconcile(record["reservation_id"], charges=charges, billing_final=True)


def test_actual_provider_overrun_is_recorded_and_blocks_future_runs(accounting):
    from llm.rl.spend import SpendCapExceeded
    record = reserve(accounting)
    charges = dict.fromkeys(("gpu", "cpu", "memory", "lifecycle", "storage", "egress"), Decimal("0"))
    charges["gpu"] = Decimal("90")
    accounting.reconcile(record["reservation_id"], charges=charges, billing_final=True)
    assert accounting.total_usd() == Decimal("90.08")
    with pytest.raises(SpendCapExceeded):
        reserve(accounting, run_id="future")


def test_existing_lower_cap_cannot_be_raised_or_missing_bound_ledger_ignored(accounting):
    from llm.rl.spend import ModalSpendLedger, SpendError
    accounting.cap_usd = Decimal("7")
    reserve(accounting)
    fresh = ModalSpendLedger(accounting.path.parent / "empty.jsonl", accounting.compute_path)
    assert fresh.remaining_usd() == Decimal("3.92")
    accounting.path.unlink()
    with pytest.raises(SpendError, match="missing"):
        fresh.remaining_usd()


def test_conflicting_populated_path_fails_closed(accounting):
    from llm.rl.spend import ModalSpendLedger, SpendError
    reserve(accounting)
    alternate = accounting.path.parent / "other.jsonl"
    alternate.write_text('{}\n')
    with pytest.raises(SpendError, match="Alternate"):
        reserve(ModalSpendLedger(alternate, accounting.compute_path))


@pytest.mark.parametrize("overhead,estimate,cap", [("0", "1", "3"), (".5", "3", "3"), (".1", "NaN", "3")])
def test_explicit_headroom_must_fit_total_reservation(accounting, overhead, estimate, cap):
    from llm.rl.spend import SpendError
    with pytest.raises(SpendError):
        accounting.reserve("run", Decimal(cap), compute_estimate=Decimal(estimate), overhead_usd=Decimal(overhead))
    assert not accounting.path.exists()


def test_compute_summary_failure_prevents_cloud_attempt(accounting, monkeypatch):
    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(accounting, "_sync_compute", fail)
    with pytest.raises(OSError):
        reserve(accounting)
    assert not accounting.path.read_text()


def test_cli_reserves_before_build_failure_and_retains_unknown_hold(accounting, monkeypatch, tmp_path):
    import llm.rl.spend as spend
    instance = tmp_path / "instance.json"
    instance.write_text('{}')
    monkeypatch.setattr(sys, "argv", ["grpo", "--instance", str(instance), "--run-id", "failed", "--cap", "3", "--overhead-usd", ".25"])
    monkeypatch.setattr(spend, "ModalSpendLedger", lambda: accounting)
    monkeypatch.setattr(grpo.subprocess, "check_output", lambda *a, **kw: "a" * 40)
    def fail(*a, **kw):
        assert accounting.total_usd() == Decimal("3.08")
        assert "pending/unknown hold $3" in accounting.compute_path.read_text()
        assert kw["project_receipt"]["run_id"] == "failed"
        assert kw["cap"] == 2.75
        raise RuntimeError("simulated image-build failure")
    monkeypatch.setattr(grpo, "build_modal_app", fail)
    with pytest.raises(RuntimeError, match="image-build"):
        grpo.main()
    assert accounting.total_usd() == Decimal("3.08")


def test_concurrent_alternate_paths_share_one_budget(accounting):
    from concurrent.futures import ThreadPoolExecutor
    from llm.rl.spend import ModalSpendLedger, SpendCapExceeded
    def attempt(index):
        ledger = ModalSpendLedger(accounting.path.parent / f"parallel-{index}.jsonl", accounting.compute_path)
        try:
            reserve(ledger, "40", f"run-{index}")
            return "reserved"
        except SpendCapExceeded:
            return "refused"
    # I/O lock contention only; no numerical worker pool or paid transport.
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, range(2))) == ["refused", "reserved"]
    assert accounting.total_usd() == Decimal("40.08")


def test_manual_unknown_spend_and_missing_project_budget_fail_closed(accounting):
    from llm.rl.spend import SpendError
    text = accounting.compute_path.read_text()
    accounting.compute_path.write_text(text.replace("$0.08 metered", "unknown metered"))
    with pytest.raises(SpendError, match="Unrecognized"):
        reserve(accounting)
    accounting.compute_path.write_text(text.replace("~$80 GRPO", "unallocated"))
    with pytest.raises(SpendError, match="allocation"):
        reserve(accounting)


def fake_modal_app(tmp_path, monkeypatch, *, receipt):
    from types import ModuleType
    module = ModuleType("modal")
    functions = []
    uploads = []
    class Image:
        @classmethod
        def from_registry(cls, *a, **kw):
            return cls()
        def pip_install(self, *a, **kw):
            return self
        def env(self, *a, **kw):
            return self
        def workdir(self, *a, **kw):
            return self
        def add_local_dir(self, *a, **kw):
            uploads.append(a)
            return self
    class App:
        def __init__(self, *a):
            pass
        def function(self, **kwargs):
            functions.append(kwargs)
            return lambda fn: fn
    module.Image = Image
    module.App = App
    module.Volume = SimpleNamespace(from_name=lambda *a, **kw: SimpleNamespace(commit=lambda: None, reload=lambda: None))
    monkeypatch.setitem(sys.modules, "modal", module)
    (tmp_path / "src").mkdir()
    adapter = tmp_path / "sft"
    adapter.mkdir()
    manifest = grpo.PinManifest.capture("a" * 40, tmp_path / "src")
    result = grpo.build_modal_app(tmp_path, manifest, cap=3, project_receipt=receipt, sft_adapter=adapter)
    assert any(args[1] == "/root/stowlm/sft-adapter" for args in uploads)
    assert "/root/stowlm/experiments/cache" in functions[0]["volumes"]
    return result, manifest


def test_admission_requires_project_receipt_before_remote_reservation(tmp_path, monkeypatch):
    (_, admit, smoke), _ = fake_modal_app(tmp_path, monkeypatch, receipt=None)
    def forbidden(*a, **kw):
        raise AssertionError("remote budget must not be reserved")
    monkeypatch.setattr(grpo, "reserve_run_budget", forbidden)
    with pytest.raises(RuntimeError, match="shared project"):
        admit("trial", instance_json={})
    with pytest.raises(RuntimeError, match="shared project"):
        smoke({}, "trial")


def test_admission_preflights_mounted_cache_before_remote_reservation_and_uploads_sft(tmp_path, monkeypatch):
    import llm.rl.preflight as preflight
    (_, admit, smoke), manifest = fake_modal_app(tmp_path, monkeypatch, receipt={"run_id": "trial"})
    events = []
    monkeypatch.setattr(grpo, "verify_manifest", lambda *a, **kw: None)
    def check(instance_json, actual_manifest, cache_root):
        assert instance_json == {"id": "synthetic"}
        assert actual_manifest == manifest
        assert cache_root == Path("/root/stowlm/experiments/cache")
        events.append("preflight")
    monkeypatch.setattr(preflight, "preflight_physics_cache", check)
    monkeypatch.setattr(grpo, "StepCheckpoint", lambda root, volume: SimpleNamespace(root=root, load=lambda: None))
    def reserve_remote(*a, **kw):
        events.append("reserve")
        return {"remote": "reserved"}
    monkeypatch.setattr(grpo, "reserve_run_budget", reserve_remote)
    assert admit("trial", instance_json={"id": "synthetic"}) == {"remote": "reserved"}
    assert events == ["preflight", "reserve"]
    def reject(*a, **kw):
        raise RuntimeError("stale physics receipt")
    monkeypatch.setattr(preflight, "preflight_physics_cache", reject)
    with pytest.raises(RuntimeError, match="stale physics"):
        admit("trial", instance_json={"id": "synthetic"})
    assert events == ["preflight", "reserve"]
    forwarded = {}
    monkeypatch.setattr(grpo, "run_gpu_smoke", lambda *a, **kw: forwarded.update(kw))
    stops = []
    monkeypatch.setattr(grpo, "hard_stop", stops.append)
    smoke({}, "trial", reservation={"seconds": 600, "deadline_epoch": 1e12})
    assert forwarded["sft_adapter"] == "/root/stowlm/sft-adapter"
    assert stops == [1e12]  # armed from the reservation before the GPU work


def test_offline_final_reconciliation_cli_needs_no_paid_credit(accounting, monkeypatch, tmp_path):
    import llm.rl.spend as spend
    record = reserve(accounting)
    monkeypatch.delenv("MODAL_CREDIT_READY", raising=False)
    charges = tmp_path / "charges.json"
    charges.write_text(json.dumps(dict.fromkeys(("gpu", "cpu", "memory", "lifecycle", "storage", "egress"), "0.10")))
    monkeypatch.setattr(sys, "argv", ["reconcile", "--reservation-id", record["reservation_id"], "--charges", str(charges), "--billing-final"])
    monkeypatch.setattr(spend, "ModalSpendLedger", lambda: accounting)
    spend.main()
    assert accounting.total_usd() == Decimal(".68")
