from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.binding.review3 import measurement as m, campaign


def sample(kind=None):
    approved = json.loads(m.APPROVAL.read_text())
    for value in approved["identities"].values():
        actual = (value["payload"]["canonical_rule"] or {}).get("constraint", {}).get("type")
        if actual == kind:
            return deepcopy(value)
    raise AssertionError(kind)


def test_approved_snapshot_preserves_every_nonhatch_and_only_stales_hatch():
    assert m.core_scope()["approved_core"]
    approved = json.loads(m.APPROVAL.read_text())
    for old in approved["identities"].values():
        row = {"measurement_identity": old, "evidence": {}}
        valid, reasons = m.compatibility(row, old)
        assert valid == (not m.is_hatch(old["payload"]))


def test_unapproved_old_loaded_code_is_not_upgraded_from_matching_sources():
    old = sample()
    old["payload"]["implementation"]["loaded_code_sha256"] = "unknown old bytecode"
    old["sha256"] = m.identity.digest(old["payload"])
    assert not m.compatibility({"measurement_identity": old}, old)[0]


def test_runtime_planning_monkeypatch_invalidates_unchanged_disk(monkeypatch):
    from experiments.mvp.pipeline.binding import adapter
    old = sample()
    assert m.compatibility({"measurement_identity": old}, old)[0]
    monkeypatch.setattr(adapter, "plan_with_rules", lambda *args, **kwargs: None)
    assert not m.core_scope()["approved_core"]
    assert not m.compatibility({"measurement_identity": old}, old)[0]


def test_reviewed_export_delta_is_normalized_but_any_other_init_is_rejected(monkeypatch):
    target = m.HERE / "__init__.py"
    raw = target.read_bytes()
    delta = json.loads((m.HERE / "review3/init-export-delta.json").read_text())
    if hashlib.sha256(raw).hexdigest() == delta["new_sha256"]:
        raw = raw[:-len(delta["append"].encode())]
    new = raw + delta["append"].encode()
    assert hashlib.sha256(new).hexdigest() == delta["new_sha256"]
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: new if path == target else original(path))
    assert m.core_scope()["approved_core"]
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"unauthorized" if path == target else original(path))
    assert not m.core_scope()["approved_core"]


def test_ladder_classifiers_without_trials_are_not_reported_as_search():
    row = {"evidence": {"stages": {"peel_seam": {"trials": [
        {"level": 1, "search": {"proposal": {"trials": []}}},
        {"level": 2, "search": {"proposal": {"trials": []}}}]}}}}
    assert m.ladder_annotation(row)["ladder_work"] == "none"
    row["evidence"]["stages"]["peel_seam"]["trials"][1]["search"]["proposal"]["trials"] = [{"attempt": 0}]
    annotation = m.ladder_annotation(row)
    assert annotation["ladder_levels_searched"] == [2]
    assert annotation["ladder_levels_classified"] == [1, 2]


def test_baseline_failed_and_first_checked_fallback_are_separate_annotations():
    row = {"key": "r", "cell": "medium", "rule_type": "close_bay", "passed": False,
           "evidence": {"status": "no_checked_plan", "first_checked_wall_s": 12}}
    result = m.annotate(row, {"passed": False})
    assert result["baseline_also_failed"] and result["report_status"] == "baseline_also_failed"
    assert result["first_checked_wall_s"] == 12
    assert "refusal_kind" in result and "planner_message" in result


def test_tiny_single_hatch_geometry_is_not_a_refusal_rate_test(tmp_path, monkeypatch):
    monkeypatch.setattr(m.measure, "ROOT", tmp_path)
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"vessel": {"hatch_covers": [{"id": "one"}]},
        "load_list": ["a", "b"], "containers": [{"id": "a", "pod": "P1"}, {"id": "b", "pod": "P2"}]}))
    row = {"key": "r", "map": "map.json", "cell": "tiny", "rule_type": "one_port_per_hatch", "evidence": {}}
    result = m.annotate(row)
    assert result["not_a_test"] and result["excluded_from_refusal_rates"]


def test_explicit_unknown_retry_is_archived_and_bounded(tmp_path):
    key = "recorded-close_bay"
    path = tmp_path / "review3/claims" / (key + ".json")
    path.parent.mkdir(parents=True)
    original = {"state": "unknown", "attempt": 1, "pid": 123}
    path.write_text(json.dumps(original))
    assert not campaign.claim_available(tmp_path, key)
    (tmp_path / "review3/retry-unknown.json").write_text(json.dumps({"keys": [key]}))
    claimed = campaign.claim(tmp_path, {"key": key, "kind": "recorded"})
    assert claimed == path and json.loads(path.read_text())["attempt"] == 2
    assert json.loads(next((path.parent / "history").glob("*.json")).read_text()) == original
    path.write_text(json.dumps({"state": "unknown", "attempt": 2}))
    assert not campaign.claim_available(tmp_path, key)


def test_recorded_active_claim_is_never_recovered(tmp_path, monkeypatch):
    key = "recorded-close_bay"
    path = tmp_path / "review3/claims" / (key + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "claimed", "attempt": 1, "pid": 123}))
    (tmp_path / "review3/retry-unknown.json").write_text(json.dumps({"keys": [key]}))
    monkeypatch.setattr(campaign, "pid_present", lambda pid: True)
    assert not campaign.claim_available(tmp_path, key)
    monkeypatch.setattr(campaign, "pid_present", lambda pid: False)
    assert campaign.claim_available(tmp_path, key)
