"""Binding heterogeneous mask evidence (C2 off, no model/GPU).

The A/B captures have no saved decoded sequence, so those tests cover complete
initial action domains, not an invented replay of the GPU samples. Original
background capacity audits are stale; these source-bound yard projections make
no physical-qualification claim. The four-action hull witness is handwritten;
its separate full-checker qualification remains explicit and optional.
"""
from __future__ import annotations

from collections import Counter
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker.full import report_matches_inputs
from stowage.checker.rules import placement_violations
from stowage.conventions import make_container_id
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import CheckReport, Instance
from stowage.model.moves import Load, Relocate
from stowage.model.yard import YardStack
from stowage.model.ship import Placement, ShipState
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state
from stowage.rules.catalogue import rule_freeze_v1

ROOT = Path(__file__).resolve().parents[2]
SPACE = get_action_space("boxslot")
NO_C2 = FeasibilityConfig(extra={"global_screen": 0})


def _module(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FULL = _module("tests/feasibility/test_w2_full_agreement.py", "heterogeneous_full_witness")
AGREEMENT = _module("tests/llm/test_mask_rule_freeze_agreement.py", "heterogeneous_mask_agreement")
IMDG = _module("tests/checker/test_rules_imdg.py", "heterogeneous_imdg_witness")
WEIGHT = _module("tests/feasibility/test_w2_weight_oog.py", "heterogeneous_weight_witness")


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def _offered(instance, action, tokenizer):
    codec = ActionCodec(tokenizer, [b.id for b in instance.containers])
    decoder = MaskedDecoder(make_feasibility_engine(instance, SPACE, config=NO_C2), codec,
                            MaskConfig(lookahead=0))
    for token in codec.encode_action(action):
        if token not in decoder.allowed_token_ids():
            return False
        decoder.advance(token)
    assert decoder.actions[-1] == action
    return True


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
def test_same_class_distinct_kg_order_binds_real_mask(tokenizer, enabled):
    with rule_freeze_v1(enabled=enabled):
        instance = WEIGHT.case(lower=12_000., upper=13_000.)
        heavy = next(b for b in instance.containers if b.id in instance.load_list)
        # Distinct kg inside the same legacy class; only exact kg rejects.
        action = Place(heavy.id, "0010184")
        violations = placement_violations(instance, instance.onboard, heavy, action.slot)
        if enabled:
            assert {v.rule_id for v in violations if v.severity == "hard"} == {
                "expert.e1.heavy_bottom", "expert.e2.weight_order"}
        assert _offered(instance, action, tokenizer) is (not enabled)


@pytest.mark.parametrize("far", [False, True], ids=["segregation_binds", "separated"])
def test_dg_segregation_binds_real_mask(tokenizer, far, record_property):
    # Synthetic published-IMDG pair from independent checker fixtures. Near and
    # far geometry differs only in its declared separation, no rule is stubbed.
    instance, _, a, b = IMDG._case(level=2, on=True, far=far)
    for box in (a, b):
        box.special_stowage, box.oog = [], None
        box.dg.ctu_closed = True
    a.gross_mass_kg, b.gross_mass_kg = 14_000., 13_000.
    instance.vessel.hull = None
    instance.port.max_crane_height_tiers = instance.port.quay_crane.max_on_deck_tiers = 8
    instance.port.yard_max_tiers = 5
    for block in instance.port.yard.blocks:
        block.max_tiers = 5
    for equipment in instance.port.equipment:
        equipment.stack_tiers = 5
    instance.containers, instance.load_list, instance.discharge_list = [a, b], [b.id], []
    instance.onboard = ShipState(vessel_id=instance.vessel.id,
                                placements=[Placement(container_id=a.id, slot="0010182")])
    instance.yard.stacks = [YardStack(key="B-001-01", length_ft=20, boxes=[b.id])]
    action = Place(b.id, "0030182")
    with rule_freeze_v1(enabled=True):
        violations = placement_violations(instance, instance.onboard, b, action.slot)
        hard = [v for v in violations if v.severity == "hard"]
        if far:
            assert not hard, hard
        else:
            assert {v.rule_id for v in hard} == {"imdg.segregation.7_4_3"}
        assert _offered(instance, action, tokenizer) is far
        offered = tuple(make_feasibility_engine(instance, SPACE, config=NO_C2).legal_actions())
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        decoder = MaskedDecoder(make_feasibility_engine(instance, SPACE, config=NO_C2), codec,
                                MaskConfig(lookahead=0))
        AGREEMENT._assert_complete_token_domain(decoder, codec, offered)
        for candidate in offered:
            trial, state = Expander(instance), initial_state(instance)
            assert trial.can_expand(candidate)
            for move in trial.step(candidate):
                before = state
                state = apply_move(instance, state, move)
            assert not [v for v in placement_violations(instance, before.ship, b, candidate.slot)
                        if v.severity == "hard"]
        record_property("dg_mask_initial_offers", len(offered))


def _hull_case():
    # Keep the real feeder's hull, tanks, fixed background and loading limits.
    try:
        instance, old_actions, plan = FULL._case("feeder_500", "tiny", 8)
    except ValueError as error:
        if "D64 feeder_500.C1 manual ballast witness requires requalification" in str(error):
            pytest.skip(f"native hull agreement unavailable; no guard waiver: {error}")
        raise
    heavy40, base20 = instance.containers
    heavy40.gross_mass_kg = 20_000.
    light40 = heavy40.model_copy(update={"id": make_container_id("QZWU", 80000), "gross_mass_kg": 19_000.})
    base20.gross_mass_kg = 9_000.
    other20 = base20.model_copy(update={"id": make_container_id("QZWU", 80001), "gross_mass_kg": 8_000.})
    obstacles = [base20.model_copy(update={"id": make_container_id("QZWU", 80000 + n),
                 "gross_mass_kg": float(6_000 + n)}) for n in (21, 37, 42, 58, 63)]
    block = instance.port.yard.blocks[0]
    block.max_tiers = instance.port.yard_max_tiers = 5
    instance.port.max_crane_height_tiers = instance.port.quay_crane.max_on_deck_tiers = 8
    for equipment in instance.port.equipment:
        equipment.stack_tiers = 5
    instance.containers = [heavy40, light40, base20, other20, *obstacles]
    instance.load_list = [b.id for b in instance.containers[:4]]
    source40 = f"{block.id}-001-02"
    source20 = f"{block.id}-004-02"
    obstacle = f"{block.id}-001-01"
    instance.yard.stacks = [YardStack(key=source40, length_ft=40, boxes=[light40.id, heavy40.id]),
        YardStack(key=source20, length_ft=20, boxes=[other20.id, base20.id]),
        YardStack(key=obstacle, length_ft=20, boxes=[b.id for b in obstacles])]
    actions = [old_actions[0], Place(light40.id, old_actions[0].slot[:-2] + "04"),
               old_actions[1], Place(other20.id, "003" + old_actions[1].slot[3:])]
    # These exact lifts are chosen independently of Expander, including a
    # explicit yard destination, so the witness cannot certify itself. This
    # destination also has the canonical implicit-Place representation.
    cover_off, _, cover_on, _ = plan.moves
    moves = [Relocate(call_index=0, container_id=obstacles[-1].id,
             src=obstacle + "-05", dst=f"{block.id}-001-03-01"), cover_off,
        Load(call_index=0, container_id=heavy40.id, src=source40 + "-02", dst=actions[0].slot),
        Load(call_index=0, container_id=light40.id, src=source40 + "-01", dst=actions[1].slot),
        cover_on,
        Load(call_index=0, container_id=base20.id, src=source20 + "-02", dst=actions[2].slot),
        Load(call_index=0, container_id=other20.id, src=source20 + "-01", dst=actions[3].slot)]
    instance = Instance.model_validate(instance.model_dump())
    plan = plan.model_copy(update={"moves": moves})
    return instance, actions, plan


@pytest.mark.slow
def test_hull_whole_plan_mixed_lengths_masses_yard_five_quay_eight(tokenizer, record_property):
    with rule_freeze_v1(enabled=True):
        instance, actions, plan = _hull_case()
        assert instance.vessel.hull and instance.vessel.slice_of == "feeder_500"
        assert len(actions) > 2 and len(actions) == len(instance.load_list)
        assert {b.length_ft for b in instance.containers} == {20, 40}
        assert len({b.gross_mass_kg for b in instance.containers}) == len(instance.containers)
        assert sorted(len(s.boxes) for s in instance.yard.stacks) == [2, 2, 5]
        assert instance.port.yard_max_tiers == 5 and instance.port.max_crane_height_tiers == 8
        # Naked retrieval is independently blocked; the written relocation
        # makes the complete plan legal before the mask is constructed.
        open_state = apply_move(instance, initial_state(instance), plan.moves[1])
        with pytest.raises(IllegalMoveError) as error:
            apply_move(instance, open_state, plan.moves[2])
        assert error.value.violation.rule_id == "port.e3.yard_crossing"
        result = apply_plan(instance, plan)
        assert result.legal, result.violations
        assert SPACE.from_plan(instance, plan) == actions
        counts = AGREEMENT._agreement(instance, actions, tokenizer)
    assert counts["prefixes"] == 4 and counts["witness_false_rejects"] == 0
    record_property("heterogeneous_hull_move_screen_whole_plan", dict(counts))


@pytest.mark.slow
def test_hull_whole_plan_full_checker_qualification(tokenizer, record_property):
    """Explicit physical gate; no saved/unbound verdict is treated as a pass."""
    if os.environ.get("W2_MASK_QUALIFY_HETEROGENEOUS") != "1":
        pytest.skip("set W2_MASK_QUALIFY_HETEROGENEOUS=1 to rerun the full-checker hull qualification")
    with rule_freeze_v1(enabled=True):
        instance, actions, plan = _hull_case()
        # A subprocess deadline bounds the entire checker including report
        # construction; the checker may catch an in-process timeout signal.
        script = """
import json, sys
from stowage.checker import check
from stowage.model import Instance, MovePlan
from stowage.rules.catalogue import rule_freeze_v1
payload = json.load(sys.stdin)
with rule_freeze_v1(enabled=True):
    report = check(Instance.model_validate_json(payload['instance']), MovePlan.model_validate_json(payload['plan']))
print(report.model_dump_json())
"""
        completed = subprocess.run([sys.executable, "-c", script],
            input=json.dumps({"instance": instance.model_dump_json(), "plan": plan.model_dump_json()}),
            capture_output=True, text=True, timeout=120, check=True,
            env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
        report = CheckReport.model_validate_json(completed.stdout)
        assert report.passed, [(v.rule_id, v.message) for v in report.violations]
        assert all(part.ran and not part.error for part in report.parts.values())
        assert report_matches_inputs(report, instance, plan)
        assert SPACE.from_plan(instance, plan) == actions
        if directory := os.environ.get("W2_MASK_HETEROGENEOUS_ARTIFACT_DIR"):
            output = Path(directory)
            output.mkdir(parents=True, exist_ok=True)
            payloads = {key: value.model_dump_json() for key, value in
                        (("instance", instance), ("plan", plan), ("report", report))}
            (output / "feeder500-heterogeneous-whole-plan.json").write_text(json.dumps({
                "activation": True, "payloads": payloads,
                "sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in payloads.items()},
                "actions": [{"box": a.box, "slot": a.slot} for a in actions]}))
        counts = AGREEMENT._agreement(instance, actions, tokenizer)
    assert counts["prefixes"] == 4 and counts["witness_false_rejects"] == 0
    record_property("heterogeneous_hull_checker_whole_plan", dict(counts))


@pytest.mark.parametrize("case_id", [120380002, 120380004, 120380005])
@pytest.mark.slow
def test_ab_small_complete_initial_mask_domain_replays(case_id, tokenizer, record_property):
    """Source-bound projections of all three A/B yards, every target, C2 off."""
    payload = json.loads((ROOT / f"tests/actions/fixtures/ab-clearance-{case_id}.json").read_text())
    counts = Counter(prefixes=1, offered_actions=0, expanded_moves=0, false_accepts=0)
    with rule_freeze_v1(enabled=True):
        source = ROOT / payload["source"]
        if not source.exists():
            pytest.skip(f"original A/B input unavailable: {source}")
        raw = source.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == payload["source_sha256"]
        original = json.loads(raw)
        for field in ("containers", "yard", "port", "load_list"):
            assert payload["instance"][field] == original[field]
        cells = {cell["id"]: cell for cell in original["vessel"]["cells"]}
        assert all(cell == cells[cell["id"]] for cell in payload["instance"]["vessel"]["cells"])
        instance = Instance.model_validate(payload["instance"])
        assert instance.vessel.hull is None  # Label the projection's physical limit.
        assert instance.id == f"v3.simulated_yard.small.{case_id}"
        assert len(payload["source_sha256"]) == 64
        expected = tuple(make_feasibility_engine(instance, SPACE, config=NO_C2).legal_actions())
        codec = ActionCodec(tokenizer, [b.id for b in instance.containers])
        decoder = MaskedDecoder(make_feasibility_engine(instance, SPACE, config=NO_C2), codec,
                                MaskConfig(lookahead=0))
        AGREEMENT._assert_complete_token_domain(decoder, codec, expected)
        rejected = SPACE.decode(payload["action"])
        assert rejected not in expected
        ex, world = Expander(instance), initial_state(instance)
        assert not ex.can_expand(rejected)
        for action in expected:
            trial, state = ex.copy(), world
            assert trial.can_expand(action), action
            moves = trial.step(action)
            for move in moves:
                before = state
                state = apply_move(instance, state, move)
            assert state == trial.world_state()
            box = next(b for b in instance.containers if b.id == action.box)
            hard = [v for v in placement_violations(instance, before.ship, box, action.slot)
                    if v.severity == "hard"]
            assert not hard, (action, hard)
            counts["offered_actions"] += 1
            counts["expanded_moves"] += len(moves)
    assert counts["offered_actions"] > 0
    record_property("ab_mask_initial_agreement", dict(counts))
    print(instance.id, dict(counts))
