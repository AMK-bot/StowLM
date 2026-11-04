"""D185 rule-provider accounting/provenance, without solver or model calls."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from experiments.mvp.strategy.headroom.layout import unrestricted_layout
from experiments.mvp.strategy.proposer import rule
from experiments.mvp.strategy.proposer.format import parse_layout
from stowage.model.fixtures import build_instance


@pytest.fixture
def policy(tmp_path, monkeypatch):
    instance = build_instance()
    source = tmp_path / "rule.py"
    source.write_text("POLICY_VERSION = 'synthetic-unit-policy'\n")
    base = unrestricted_layout(instance)
    from experiments.mvp.strategy.headroom.layout import BlockLayout
    layout = BlockLayout(base.allowed, "synthetic-unit-policy", {"rule_version": 1})
    calls = []
    def choose(value):
        calls.append(value)
        return layout
    module = SimpleNamespace(port_blocking_layout=choose)
    monkeypatch.setattr(rule, "BLOCKRULE_DIR", tmp_path)
    monkeypatch.setattr(rule.importlib, "import_module", lambda name: module)
    return instance, source, calls, layout


def test_rule_provider_reuses_shared_policy_without_outcome_selection(policy):
    instance, _, calls, layout = policy
    provider = rule.RuleProposer(instance, seed=179)
    first = provider.propose(1)
    provider.observe(first, {"checked_evaluations": 1, "check": {"passed": False}})
    second = provider.propose(3)
    assert calls == [instance]
    assert first.text == second.text
    assert first.metadata["attempt"] == 1
    assert second.metadata["attempt"] == 3
    assert parse_layout(first.text, instance).fingerprint == layout.fingerprint
    assert first.metadata["selection"] == rule.SCHEDULE


def test_rule_provider_pins_exact_shared_source_and_metadata(policy):
    instance, source, _, layout = policy
    proposal = rule.RuleProposer(instance).propose(1)
    assert proposal.metadata["provider"] == "rule"
    assert proposal.metadata["model_called"] is False
    assert proposal.metadata["policy_version"] == rule.POLICY_VERSION
    assert proposal.metadata["source_sha256"] == {"rule.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    assert proposal.metadata["fingerprint"] == layout.fingerprint
    assert proposal.metadata["layout"] == layout.to_dict()


def test_rule_provider_detects_policy_mutation(policy):
    instance, source, _, _ = policy
    provider = rule.RuleProposer(instance)
    source.write_text("POLICY_VERSION = 'changed'\n")
    with pytest.raises(ValueError, match="source changed"):
        provider.propose(1)


def test_rule_provider_metadata_cannot_mutate_pinned_identity(policy):
    instance, _, _, _ = policy
    provider = rule.RuleProposer(instance)
    first = provider.propose(1)
    first.metadata["source_sha256"].clear()
    assert provider.propose(2).metadata["source_sha256"]


@pytest.mark.parametrize("attempt", [0, -1, True, 1.5, "1", None])
def test_rule_provider_rejects_invalid_attempt(policy, attempt):
    with pytest.raises(ValueError, match="positive integer"):
        rule.RuleProposer(policy[0]).propose(attempt)


def test_rule_provider_missing_source_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(rule, "BLOCKRULE_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="not ready"):
        rule.RuleProposer(None)


def test_rule_provider_missing_module_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "rule.py").write_text("# synthetic unit source\n")
    monkeypatch.setattr(rule, "BLOCKRULE_DIR", tmp_path)
    def absent(name):
        raise ModuleNotFoundError(name)
    monkeypatch.setattr(rule.importlib, "import_module", absent)
    with pytest.raises(RuntimeError, match="not ready"):
        rule.RuleProposer(None)


def test_source_pins_exclude_tests_and_run_artifacts(tmp_path):
    (tmp_path / "rule.py").write_text("# real source\n")
    (tmp_path / "test_rule.py").write_text("# synthetic unit source\n")
    (tmp_path / "run.json").write_text("{}\n")
    assert set(rule.source_pins(tmp_path)) == {"rule.py"}


@pytest.mark.parametrize("size", ["small", "medium"])
def test_real_shared_rule_api_matches_without_reimplementation(size):
    from experiments.mvp.strategy.blockrule.rule import port_blocking_layout
    from stowage.rules.catalogue import rule_freeze_v1
    # A synthetic fixture exercises each policy branch, never a cohort outcome.
    instance = build_instance().model_copy(update={"size_class": size})
    with rule_freeze_v1(enabled=False):
        expected = port_blocking_layout(instance)
    provider = rule.RuleProposer(instance)
    proposal = provider.propose(1)
    assert proposal.metadata["layout"] == expected.to_dict()
    assert parse_layout(proposal.text, instance).allowed == expected.allowed
    assert expected.metadata["paired_transverse_covers"] is (size == "medium")
    assert expected.name == ("port-blocking-fore-aft-v1" if size == "medium" else "port-blocking-cover-v1")


def test_real_small_rule_keeps_ports_on_separate_hatch_covers():
    instance = build_instance().model_copy(update={"size_class": "small"})
    from experiments.mvp.strategy.headroom.layout import BlockLayout
    layout = BlockLayout.from_dict(rule.RuleProposer(instance).propose(1).metadata["layout"])
    by_port = {}
    for (pod, _), blocks in layout.allowed.items():
        by_port.setdefault(pod, set()).update(bid.split("/")[0] for bid in blocks)
    ports = sorted(by_port)
    assert len(ports) == 2
    assert not by_port[ports[0]] & by_port[ports[1]]


def test_real_medium_rule_pairs_transverse_covers_even_on_short_slice():
    instance = build_instance().model_copy(update={"size_class": "medium"})
    from experiments.mvp.strategy.headroom.layout import BlockLayout
    layout = BlockLayout.from_dict(rule.RuleProposer(instance).propose(1).metadata["layout"])
    assert layout.metadata["shared_station_due_to_short_slice"] is True
    assert layout.metadata["lateral_split_possible"] is False
    for blocks in layout.allowed.values():
        assert {bid.split("/")[0] for bid in blocks} == {"HC-02-1", "HC-02-2"}


def test_rule_default_fallback_is_separate_fixed_proposal(policy):
    instance, _, calls, _ = policy
    provider = rule.RuleProposer(instance)
    fallback = provider.propose(2)
    assert not calls
    assert fallback.metadata["kind"] == "default-fallback"
    assert fallback.metadata["fallback_checks_counted"] is True
    assert parse_layout(fallback.text, instance).allowed == unrestricted_layout(instance).allowed
    assert provider.propose(1).metadata["kind"] == "port-blocking"
    assert provider.propose(4).text == fallback.text


def test_rule_failure_is_wasted_proposal_and_default_fallback_still_available(policy):
    instance, _, _, _ = policy
    provider = rule.RuleProposer(instance)
    def geometry_failure(value):
        raise ValueError("synthetic geometry failure")
    provider.policy.port_blocking_layout = geometry_failure
    with pytest.raises(RuntimeError, match="rule proposal unavailable: ValueError"):
        provider.propose(1)
    assert provider.propose(2).metadata["kind"] == "default-fallback"


def test_rule_registration_pins_schedule_and_source(policy):
    registration = rule.policy_registration()
    assert registration["schedule"] == rule.SCHEDULE
    assert registration["source_sha256"] == rule.source_pins()
    assert registration["certificate_reuse"] is False
    assert registration["fallback_checks_counted"] is True


def test_rule_api_missing_fails_before_proposals(policy, monkeypatch):
    monkeypatch.setattr(rule.importlib, "import_module", lambda name: SimpleNamespace())
    with pytest.raises(RuntimeError, match="API is not ready"):
        rule.RuleProposer(policy[0])
