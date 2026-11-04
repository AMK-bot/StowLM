"""5.1 flip gate: the complete token language equals the 4.4 action domain.

C2 stays off pending linear requalification. Native geometry witnesses establish
move/static agreement; optional bound full-checker witnesses establish observed
false rejects, without treating a necessary screen as a completion certificate.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker import check
from stowage.checker.full import report_matches_inputs
from stowage.checker.rules import placement_violations
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import CheckReport, Instance, MovePlan, PartReport
from stowage.moves import apply_move, initial_state
from stowage.rules.catalogue import rule_freeze_v1

ROOT = Path(__file__).resolve().parents[2]
SPACE = get_action_space("boxslot")
NO_C2 = FeasibilityConfig(extra={"global_screen": 0})


def _module(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FLEET = _module("tests/feasibility/test_w2_agreement.py", "mask_fleet_witnesses")
CANARIES = _module("experiments/campaign-g1/migration_canaries.py", "mask_migration_canaries")


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def _assert_complete_token_domain(decoder, codec, actions):
    """Visit every legal action's every prefix, detecting extra tokens too.

    Equality at every node of the reference trie proves the entire offered
    language equals that trie, including all target slots, not a sampled box.
    pop_action restores the same prefix and reuses the decoder's lazy caches.
    """
    trie = codec.trie(actions, allow_end=decoder.engine.is_complete())
    if not actions:
        assert decoder.allowed_token_ids() == tuple(sorted(trie.allowed_next_token_ids(())))
    for action in actions:
        prefix = []
        for token in codec.encode_action(action):
            assert decoder.allowed_token_ids() == tuple(sorted(trie.allowed_next_token_ids(prefix)))
            decoder.advance(token)
            prefix.append(token)
        assert decoder.actions[-1] == action
        assert decoder.pop_action() == action


def _agreement(instance, witness, tokenizer):
    engine = make_feasibility_engine(instance, SPACE, config=NO_C2)
    # Separate construction is intentional: engine.copy() shares immutable
    # evaluated snapshots, which could accidentally exercise the eager path.
    reference = make_feasibility_engine(instance, SPACE, config=NO_C2)
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    decoder = MaskedDecoder(engine, codec, MaskConfig(lookahead=0))
    expander, world = Expander(instance), initial_state(instance)
    counts = Counter(prefixes=0, offered_actions=0, false_accepts=0, witness_false_rejects=0)
    for expected in witness:
        offered = tuple(reference.legal_actions())
        _assert_complete_token_domain(decoder, codec, offered)
        # Independent move replay of every offered target, including yard lifts
        # and cover handling; no successful prefix is counted as a full plan.
        for candidate in offered:
            probe, current = expander.copy(), world
            for move in probe.step(candidate):
                before = current
                current = apply_move(instance, current, move)
            if isinstance(candidate, Place):
                box = next(box for box in instance.containers if box.id == candidate.box)
                hard = [v for v in placement_violations(instance, before.ship, box, candidate.slot)
                        if v.severity == "hard"]
                assert not hard, (candidate, hard)
            successor = reference.copy()
            successor.push(candidate)
            assert all(successor.completion_check().values()), (candidate, successor.completion_check())
            counts["offered_actions"] += 1
        counts["prefixes"] += 1
        counts["witness_false_rejects"] += expected not in offered
        assert expected in offered, (instance.id, expected, engine.dead_end(), counts)
        for move in expander.step(expected):
            world = apply_move(instance, world, move)
        for token in codec.encode_action(expected):
            decoder.advance(token)
        reference.push(expected)
        engine = decoder.engine
    assert engine.is_complete(), "the witness must load the complete target list"
    assert set(instance.load_list) <= {action.box for action in decoder.actions}
    for token in codec.end_plan_tokens:
        decoder.advance(token)
    assert decoder.status == "complete"
    return counts


def _replay_completion(instance, actions, tokenizer):
    """Replay a checker-accepted witness without probing alternate completions."""
    engine = make_feasibility_engine(instance, SPACE, config=NO_C2)
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    decoder = MaskedDecoder(engine, codec, MaskConfig(lookahead=0))
    counts = Counter(prefixes=0, witness_false_rejects=0, tokens=0)
    for action in actions:
        for token in codec.encode_action(action):
            assert token in decoder.allowed_token_ids(), (instance.id, action, decoder.dead_end)
            decoder.advance(token)
            counts["tokens"] += 1
        counts["prefixes"] += 1
    assert decoder.engine.is_complete()
    assert set(instance.load_list) <= {action.box for action in decoder.actions}
    for token in codec.end_plan_tokens:
        decoder.advance(token)
    assert decoder.status == "complete"
    return counts


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
@pytest.mark.parametrize("vessel", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("rung", ["tiny", "small"])
@pytest.mark.parametrize("crane_h", [4, 8])
def test_mask_complete_fleet_action_domains(tokenizer, enabled, vessel, rung, crane_h, record_property):
    with rule_freeze_v1(enabled=enabled):
        instance, witness = FLEET._case(vessel, rung, crane_h, seed=23)
        # Independent quay H=4/8, effective yard height=5 (contract A8).
        instance.port.yard_max_tiers = 5
        for block in instance.port.yard.blocks:
            block.max_tiers = 5
        for equipment in instance.port.equipment:
            equipment.stack_tiers = 5
        assert instance.port.yard_max_tiers == 5
        counts = _agreement(instance, witness, tokenizer)
    record_property("mask_engine_agreement", dict(counts))
    print(instance.id, "activated" if enabled else "legacy", dict(counts))


def test_activated_decoder_uses_lazy_box_then_complete_slot_domains(tokenizer, monkeypatch):
    """Fail first on the former eager index path; no all-pairs query is needed."""
    with rule_freeze_v1(enabled=True):
        instance, witness = FLEET._case("feeder_500", "tiny", 4, seed=23)
        engine = make_feasibility_engine(instance, SPACE, config=NO_C2)
        def eager_forbidden(*args, **kwargs):
            raise AssertionError("activated token decoding eagerly materialized every action")
        monkeypatch.setattr(type(engine), "legal_index", eager_forbidden)
        monkeypatch.setattr(type(engine), "legal_actions", eager_forbidden)
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        decoder = MaskedDecoder(engine, codec, MaskConfig(lookahead=0))
        for action in witness:
            for token in codec.encode_action(action):
                assert token in decoder.allowed_token_ids()
                decoder.advance(token)
        assert decoder.engine.is_complete()


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
def test_bound_replay_rejects_unbound_action_destination(tmp_path, monkeypatch, enabled):
    """Changing only an unhashed destination must fail before mask replay.

    The synthetic report exercises input binding only; it is not physical
    qualification evidence. The separate bound-artifact gate supplies that.
    """
    with rule_freeze_v1(enabled=enabled):
        instance, actions = FLEET._case("feeder_500", "tiny", 8, seed=23)
        # The reduced fleet fixture bypasses model validation; retain the full
        # native vessel metadata when round-tripping this synthetic payload.
        instance.vessel = FLEET.load_vessel("feeder_500", with_hull=False)
        instance = Instance.model_validate_json(instance.model_dump_json())
        plan = MovePlan.model_validate_json(SPACE.expand(instance, actions).model_dump_json())
    report = CheckReport(passed=True, parts={"in_port": PartReport(name="in_port")})
    report.provenance.parents = [
        f"checker.{name}.sha256:" + hashlib.sha256(json.dumps(
            value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()).hexdigest()
        for name, value in (("instance", instance), ("plan", plan))]
    assert report_matches_inputs(report, instance, plan)
    payloads = {"instance": instance.model_dump_json(), "plan": plan.model_dump_json(),
                "report": report.model_dump_json()}
    supplied = [{"box": action.box, "slot": action.slot} for action in actions]
    supplied[-1]["slot"] = "003" + supplied[-1]["slot"][3:]
    assert supplied[-1]["slot"] != actions[-1].slot
    bound = {"activation": enabled, "payloads": payloads, "actions": supplied,
             "sha256": {key: hashlib.sha256(value.encode()).hexdigest()
                        for key, value in payloads.items()}}
    # Satisfy the real gate's corpus-size contract without external artifacts;
    # its first record must be rejected before either replay or domain counting.
    for index in range(24):
        (tmp_path / f"{index:02}.json").write_text(json.dumps(bound))
    monkeypatch.setenv("W2_MASK_ACCEPTED_COMPLETIONS", str(tmp_path))
    def forbidden_replay(*args, **kwargs):
        raise AssertionError("unbound destination reached mask replay")
    monkeypatch.setitem(globals(), "_replay_completion", forbidden_replay)
    with pytest.raises(AssertionError, match="actions differ from checker-bound plan"):
        test_mask_replays_bound_full_checker_completions(None, lambda *args: None)


@pytest.mark.slow
def test_mask_replays_bound_full_checker_completions(tokenizer, record_property):
    """Optional exact-input replay of the 4.4 physical qualification artifacts.

    Saved verdicts remain bound to their original checker epoch. This replay
    re-executes only the current mask/screens and never claims requalification.
    """
    directory = os.environ.get("W2_MASK_ACCEPTED_COMPLETIONS")
    if not directory:
        pytest.skip("set W2_MASK_ACCEPTED_COMPLETIONS to the 24 bound 4.4 completion witnesses")
    paths = sorted(Path(directory).glob("*.json"))
    assert len(paths) == 24, "expected both activation settings, 3 feeders, 2 rungs, 2 quay heights"
    counts = Counter(accepted_completions=0, full_checker_false_rejects=0)
    domains = set()
    for path in paths:
        bound = json.loads(path.read_text())
        payloads = bound["payloads"]
        assert bound["sha256"] == {key: hashlib.sha256(value.encode()).hexdigest()
                                   for key, value in payloads.items()}
        instance = Instance.model_validate_json(payloads["instance"])
        plan = MovePlan.model_validate_json(payloads["plan"])
        report = CheckReport.model_validate_json(payloads["report"])
        domains.add((instance.id, bound["activation"]))
        assert report.passed and report_matches_inputs(report, instance, plan)
        assert all(part.ran and not part.error for part in report.parts.values())
        if bound["activation"]:
            assert "in_port" in report.parts
        actions = [Place(**value) for value in bound["actions"]]
        with rule_freeze_v1(enabled=bound["activation"]):
            # The report hashes instance/plan, not this convenient action list.
            # Reconstruct under its original semantics before trusting replay.
            assert SPACE.from_plan(instance, plan) == actions, (
                "actions differ from checker-bound plan", path)
            result = _replay_completion(instance, actions, tokenizer)
        counts.update(result)
        counts["accepted_completions"] += 1
        counts["full_checker_false_rejects"] += result["witness_false_rejects"]
    assert domains == {(f"w2-full-{vessel}-{rung}-H{height}", enabled)
                       for vessel in ("feeder_500", "feeder_800", "feeder_1700")
                       for rung in ("tiny", "small") for height in (4, 8)
                       for enabled in (False, True)}
    assert counts["full_checker_false_rejects"] == 0
    record_property("bound_checker_completion_replay", dict(counts))
    print("BOUND_CHECKER_COMPLETIONS", dict(counts))


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(CANARIES.CANARIES))
def test_mask_replays_checker_accepted_freeze_canary_members(tokenizer, name, record_property):
    """Build actual gate inputs; missing physical qualification stays explicit."""
    with rule_freeze_v1(enabled=True):
        pair = CANARIES._cases_module().build_pair(name)
        if pair.get("status") != "ready":
            pytest.skip(f"freeze gate {name} unavailable: {pair.get('missing')}: {pair.get('reason', '')}")
        accepted = 0
        for member_name in ("baseline", "defect"):
            member = pair[member_name]
            if member.get("expectation", "accept") == "deny":
                continue
            instance = Instance.model_validate(member["input"])
            plan = MovePlan.model_validate(member["plan"])
            report = check(instance, plan)
            assert report.passed, (name, member_name, report.violations)
            assert all(part.ran and not part.error for part in report.parts.values())
            assert report_matches_inputs(report, instance, plan)
            actions = [SPACE.decode(value) for value in [*member.get("prefix_actions", []), member["action"]]]
            result = _replay_completion(instance, actions, tokenizer)
            assert result["witness_false_rejects"] == 0
            accepted += 1
        assert accepted, f"{name} has no checker-accepted member"
        record_property("freeze_canary_accepted_completions", accepted)
