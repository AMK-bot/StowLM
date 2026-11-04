"""Interrupted attempts and lost candidates cannot become successful evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


HERE = Path(__file__).resolve().parent


@pytest.fixture
def campaign(monkeypatch):
    spec = importlib.util.spec_from_file_location("g1_p1_evidence", HERE / "campaign.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "freeze_check", lambda _: {})
    monkeypatch.setattr(module, "runner_check", lambda *_: None)
    return module


def args():
    return SimpleNamespace(diagnostic=False, allow_drift=False, timeout_s=2)


def job(campaign):
    path = HERE.parents[1] / "data/fixtures/empty_ship.json"
    return dict(stage="4.7", id="one", input=dict(path=str(path),
        sha256=campaign.sha(path), rung="tiny"))


def test_failed_retry_cannot_inherit_uncheckpointed_success(campaign, tmp_path, monkeypatch):
    directory = tmp_path / "4.7/one"
    campaign.atomic_json(directory / "result.json", dict(adapter_completed=True,
        evidence_eligible=True, measurement=dict(approved=True, checker_passed=True, objective=8)))
    (directory / "approved-plan.json").write_text("old candidate")
    monkeypatch.setattr(campaign, "command", lambda *_: [sys.executable, "-c", "raise SystemExit(23)"])
    row = campaign.run_shard(args(), {"epoch_id": "e"}, job(campaign), tmp_path)
    assert row["process_returncode"] == 23
    assert row["adapter_completed"] is False
    assert row["evidence_eligible"] is False
    assert not row.get("measurement", {}).get("approved")
    assert "objective" not in row.get("measurement", {})
    assert not (directory / "done.json").exists()
    assert not (directory / "approved-plan.json").exists()
    assert list((directory / "quarantine").rglob("approved-plan.json"))


@pytest.mark.parametrize("mutation", ["corrupt", "delete"])
@pytest.mark.parametrize("names", [("first-plan.json", "second-plan.json"), ("a.json", "b.json")])
def test_candidate_inventory_is_checked_and_replay_obligations_survive(campaign, tmp_path, monkeypatch, mutation, names):
    from stowage.io import load_instance, save_plan
    from stowage.model import MovePlan
    selected = job(campaign)["input"]
    instance = load_instance(selected["path"])
    directory = tmp_path / "4.7/one"

    class Child:
        pid = 12345
        def __init__(self, *_, **kwargs):
            for identifier, name in zip(("first", "second"), names):
                save_plan(MovePlan(id=identifier, instance_id=instance.id,
                    call_index=instance.call_index, moves=[]), directory / name)
            campaign.atomic_json(directory / "result.json", dict(adapter_completed=True,
                attempt_id=kwargs['env']['CAMPAIGN_ATTEMPT_ID']))
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(campaign, "command", lambda *_: ["synthetic"])
    monkeypatch.setattr(campaign.subprocess, "Popen", Child)
    identity = {"epoch_id": "e"}
    campaign.run_shard(args(), identity, job(campaign), tmp_path)
    shard_identity = dict(campaign=identity, job=job(campaign))
    marker = directory / "done.json"
    assert campaign.valid_done(marker, shard_identity)
    assert len(campaign.discover_plans(tmp_path, [selected])) == 2
    candidate = directory / names[1]
    if mutation == "delete":
        candidate.unlink()
    else:
        candidate.write_text("{corrupt")
    assert not campaign.valid_done(marker, shard_identity)
    replays = campaign.discover_plans(tmp_path, [selected])
    assert len(replays) == 2
    assert sum(bool(row.get("artifact_error")) for row in replays) == 1
    successful = dict(stage="replay", input=selected, adapter_completed=True,
        measurement=dict(verified=True, checker_passed=True, finalized_status="ok"))
    summary = campaign.synthesize(SimpleNamespace(diagnostic=True), identity,
        [successful], replays, tmp_path)
    assert summary["returned_plan_replay_denominator"] == 2
    assert not summary["replay_gate_met"]
    assert not summary["solver_checker_gate_met"]
    with pytest.raises(ValueError, match="done marker|artifact"):
        campaign.run_shard(args(), identity, job(campaign), tmp_path)


def test_nonzero_current_attempt_discards_even_fresh_success(campaign, tmp_path, monkeypatch):
    directory = tmp_path / "4.7/one"
    class Child:
        pid = 12345
        def __init__(self, *_, **__):
            campaign.atomic_json(directory / "result.json", dict(adapter_completed=True,
                measurement=dict(approved=True, checker_passed=True, objective=8)))
        def wait(self, timeout=None):
            return 23
    monkeypatch.setattr(campaign, "command", lambda *_: ["synthetic"])
    monkeypatch.setattr(campaign.subprocess, "Popen", Child)
    row = campaign.run_shard(args(), {"epoch_id": "e"}, job(campaign), tmp_path)
    assert not row["adapter_completed"]
    assert not row["evidence_eligible"]
    assert not row.get("measurement", {}).get("approved")


def test_synthesis_refuses_uncheckpointed_success(campaign, tmp_path):
    row = dict(stage="replay", adapter_completed=True, evidence_eligible=True,
        process_returncode=0, attempt_id="uncheckpointed", input={"sha256": "input"},
        measurement=dict(verified=True, checker_passed=True, finalized_status="ok"))
    with pytest.raises(ValueError, match="checkpoint|done marker"):
        campaign.synthesize(args(), {"epoch_id": "e"}, [row], [{}], tmp_path)


@pytest.mark.parametrize("attempt", [None, "older-orphan-worker"])
def test_result_must_echo_current_child_attempt(campaign, tmp_path, monkeypatch, attempt):
    directory = tmp_path / "4.7/one"
    class Child:
        pid = 12345
        def __init__(self, *_, **__):
            campaign.atomic_json(directory / "result.json", dict(adapter_completed=True,
                attempt_id=attempt, measurement=dict(approved=True, objective=8)))
        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(campaign, "command", lambda *_: ["synthetic"])
    monkeypatch.setattr(campaign.subprocess, "Popen", Child)
    result = campaign.run_shard(args(), {"epoch_id": "e"}, job(campaign), tmp_path)
    assert not result["adapter_completed"]
    assert not result["evidence_eligible"]
    assert not result.get("measurement", {}).get("approved")


@pytest.mark.parametrize("markers", [{"tiny_run": True}, {"official": False}])
def test_reference_diagnostic_markers_cannot_be_laundered(campaign, markers):
    with pytest.raises(ValueError, match="diagnostic"):
        campaign.require_evidence({"solver": {"reference": markers}})


def test_controller_checkpoint_cannot_repin_replacement_output(campaign, tmp_path, monkeypatch):
    directory = tmp_path / '4.7/one'
    atomic = campaign.atomic_json
    class Child:
        pid = 12345
        def __init__(self, *_, **kwargs):
            atomic(directory / 'result.json', dict(adapter_completed=True,
                attempt_id=kwargs['env']['CAMPAIGN_ATTEMPT_ID']))
        def wait(self, timeout=None):
            return 0
    def replace_after_write(path, value):
        digest = atomic(path, value)
        if Path(path) == directory / 'result.json' and 'process_returncode' in value:
            atomic(path, dict(value, diagnostic=True, evidence_eligible=False))
        return digest
    monkeypatch.setattr(campaign, 'atomic_json', replace_after_write)
    monkeypatch.setattr(campaign, 'command', lambda *_: ['synthetic'])
    monkeypatch.setattr(campaign.subprocess, 'Popen', Child)
    with pytest.raises(ValueError, match='checksum'):
        campaign.run_shard(args(), {'epoch_id': 'e'}, job(campaign), tmp_path)
