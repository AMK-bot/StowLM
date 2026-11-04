"""boxslot action space (plan 3.8): registry, serialisation, hand-written fixture expansions and error cases.
Property tests (round trip on random/foreign plans) live in ``test_space_properties.py``."""
from __future__ import annotations

import random

import pytest

from stowage.actions import ActionSpace, get_action_space
from stowage.actions.samples import random_actions, tiny_instance
from stowage.actions.space import Discharge, Place, Relocate, RestowOff, box_moves
from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateIn, Load
from stowage.moves import apply_move, apply_plan, initial_state
from stowage.versions import ACTION_SPACE_VERSION

SPACE = get_action_space()
A14, A20, A35, A40 = "QZXU0000014", "QZXU0000020", "QZXU0000035", "QZXU0000040"  # fixture load list
DIS, THROUGH = "QZXU0000056", "QZXU0000061"  # layover fixture: DIS at 0010102 under THROUGH at 0010104


def kinds(plan):
    return [(m.kind, getattr(m, "container_id", None) or m.cover_id) for m in plan.moves]


def stacked_fixture(layover=False):
    """Fixture with A40 moved on top of A35 (A-003-01), so loading A35 needs one yard relocation."""
    data = build_instance(layover=layover).model_dump()
    stacks = [s for s in data["yard"]["stacks"] if s["key"] != "A-004-01"]
    for s in stacks:
        if s["key"] == "A-003-01":
            s["boxes"] = [A35, A40]
    data["yard"]["stacks"] = stacks
    return Instance.model_validate(data)


# ------------------------------------------------------------------ registry
def test_registry_and_protocol():
    assert get_action_space("boxslot") is SPACE
    assert SPACE.name == "boxslot"
    assert SPACE.version == ACTION_SPACE_VERSION
    assert isinstance(SPACE, ActionSpace)
    with pytest.raises(KeyError):
        get_action_space("no-such-space")


def test_plan_tags_action_space():
    plan = SPACE.expand(build_instance(), [Place(A35, "0010102")])
    assert plan.action_space == f"boxslot@{ACTION_SPACE_VERSION}"
    assert plan.instance_id == "fixture_empty_ship"


# ------------------------------------------------------------------ serialisation
def _sample_actions():
    out = []
    for seed in range(12):
        inst = tiny_instance(seed, layover=seed % 2 == 1)
        try:
            out.extend(random_actions(inst, seed))
        except RuntimeError:
            continue
    out += [Discharge(DIS), Discharge(DIS, "A-002-01"), RestowOff(THROUGH), Relocate(A40, "B-003-02")]
    return out


def test_encode_decode_round_trip():
    acts = _sample_actions()
    assert len(acts) > 50
    assert {type(a) for a in acts} == {Place, Discharge, Relocate, RestowOff}
    for a in acts:
        data = SPACE.encode(a)
        assert SPACE.decode(data) == a
        assert SPACE.decode(dict(data)) == a
        assert SPACE.encode(SPACE.decode(data)) == data


def test_encode_shapes():
    assert SPACE.encode(Place(A35, "0010102")) == {"op": "P", "box": A35, "slot": "0010102"}
    assert SPACE.encode(Discharge(DIS)) == {"op": "D", "box": DIS}
    assert SPACE.encode(Discharge(DIS, "A-001-02")) == {"op": "D", "box": DIS, "to": "A-001-02"}
    assert SPACE.encode(Relocate(A40, "A-005-01")) == {"op": "R", "box": A40, "to": "A-005-01"}
    assert SPACE.encode(RestowOff(THROUGH)) == {"op": "O", "box": THROUGH}
    with pytest.raises(ValueError):
        SPACE.encode(("P", A35))


@pytest.mark.parametrize("bad", [
    {"op": "X", "box": A35},
    {"box": A35, "slot": "0010102"},
    {"op": "P", "box": A35, "slot": "0010102", "to": "A-001-01"},
    {"op": "O", "box": A35, "slot": "0010102"},
    {"op": "D", "box": A35, "extra": 1},
    {"op": "P", "box": A35},  # missing field
    {"op": "R", "to": "A-001-01"},
])
def test_decode_rejects(bad):
    with pytest.raises(ValueError):
        SPACE.decode(bad)


def test_sort_key_total_order():
    acts = _sample_actions()
    distinct = set(acts)
    keys = {SPACE.sort_key(a) for a in distinct}
    assert len(keys) == len(distinct)  # injective on distinct actions -> total order
    for a in acts:  # equal actions, equal keys (also across fresh copies)
        assert SPACE.sort_key(a) == SPACE.sort_key(SPACE.decode(SPACE.encode(a)))
    ordered = sorted(distinct, key=SPACE.sort_key)
    shuffled = list(distinct)
    random.Random(0).shuffle(shuffled)
    assert sorted(shuffled, key=SPACE.sort_key) == ordered


# ------------------------------------------------------------------ fixture expansions
def test_lazy_hatch_covers_stage_a():
    inst = build_instance()
    acts = [Place(A35, "0010102"), Place(A40, "0010182"), Place(A20, "0010202"), Place(A14, "0020282")]
    plan = SPACE.expand(inst, acts)
    assert kinds(plan) == [
        ("hatch_cover_off", "HC-02-1"), ("load", A35),
        ("hatch_cover_on", "HC-02-1"), ("load", A40),
        ("hatch_cover_off", "HC-02-2"), ("load", A20),
        ("hatch_cover_on", "HC-02-2"), ("load", A14),
    ]
    assert all(m.dst == "quay" for m in plan.moves if m.kind == "hatch_cover_off")
    result = apply_plan(inst, plan)
    assert result.legal, result.violations
    assert (result.count.loads, result.count.hatch_cover_lifts, result.count.relocations) == (4, 4, 0)
    assert [SPACE.decode(a) for a in plan.actions] == acts


def test_covers_closed_at_end():
    inst = build_instance()
    plan = SPACE.expand(inst, [Place(A35, "0010102"), Place(A40, "0030102"), Place(A20, "0010202")])
    assert kinds(plan) == [
        ("hatch_cover_off", "HC-02-1"), ("load", A35), ("load", A40),
        ("hatch_cover_off", "HC-02-2"), ("load", A20),
        ("hatch_cover_on", "HC-02-1"), ("hatch_cover_on", "HC-02-2"),
    ]
    result = apply_plan(inst, plan)
    assert result.legal, result.violations
    assert result.final_state.ship.covers_off == {}


def test_deck_only_needs_no_cover_lifts():
    inst = build_instance()
    plan = SPACE.expand(inst, [Place(A35, "0010182"), Place(A40, "0010184")])
    assert kinds(plan) == [("load", A35), ("load", A40)]
    assert apply_plan(inst, plan).legal


def test_layover_discharge_restows_blocker_to_quay():
    inst = build_instance(layover=True)
    acts = [Discharge(DIS), Place(THROUGH, "0010102"), Place(A35, "0010182")]
    plan = SPACE.expand(inst, acts)
    assert kinds(plan) == [
        ("hatch_cover_off", "HC-02-1"), ("restow_off", THROUGH), ("discharge", DIS),
        ("restow_on", THROUGH),
        ("hatch_cover_on", "HC-02-1"), ("load", A35),
    ]
    result = apply_plan(inst, plan)
    assert result.legal, result.violations
    assert (result.count.discharges, result.count.restow_lifts, result.count.hatch_cover_lifts) == (1, 2, 2)
    # canonical actions: the auto restow-off is not written
    assert [SPACE.decode(a) for a in plan.actions] == acts


def test_layover_explicit_discharge_target():
    inst = build_instance(layover=True)
    plan = SPACE.expand(inst, [Discharge(DIS, "A-005-02"), Place(THROUGH, "0010102")])
    assert [(m.kind, getattr(m, "dst", None)) for m in plan.moves if m.kind == "discharge"] == [
        ("discharge", "A-005-02-01")]
    assert apply_plan(inst, plan).legal
    assert SPACE.decode(plan.actions[0]) == Discharge(DIS, "A-005-02")


def test_layover_restow_off_clears_above_first():
    inst = build_instance(layover=True)
    acts = [RestowOff(DIS), Place(DIS, "0030102"), Place(THROUGH, "0010102")]
    with pytest.raises(ValueError):  # DIS is not on the discharge path here: it is restowed, never discharged
        SPACE.expand(inst, [RestowOff(DIS), Discharge(THROUGH)])
    plan = SPACE.expand(inst, acts)
    assert kinds(plan)[:3] == [("hatch_cover_off", "HC-02-1"), ("restow_off", THROUGH), ("restow_off", DIS)]
    assert [m.kind for m in plan.moves[3:5]] == ["restow_on", "restow_on"]
    # not legal: the discharge-list box was never discharged; the lifts themselves replay fine
    result = apply_plan(inst, plan)
    assert [v.message for v in result.violations] == ["Every discharge-list box must be discharged before departure."]


def test_yard_blocker_relocated_to_clean_stack():
    inst = stacked_fixture()
    plan = SPACE.expand(inst, [Place(A35, "0010182")])
    assert [m.kind for m in plan.moves] == ["relocate", "load"]
    reloc = plan.moves[0]
    assert reloc.container_id == A40 and reloc.src == "A-003-01-02"
    key = reloc.dst.rsplit("-", 1)[0]
    stacks = {s.key: s.boxes for s in inst.yard.stacks}
    assert not set(stacks.get(key, [])) & set(inst.load_list)  # clean stack
    assert key.startswith("A-")  # stays in its block
    assert apply_plan(inst, plan).legal
    # explicit override: written before the Place, no auto relocation then
    plan2 = SPACE.expand(inst, [Relocate(A40, "A-001-02"), Place(A35, "0010182")])
    assert [(m.kind, getattr(m, "dst", None)) for m in plan2.moves] == [
        ("relocate", "A-001-02-01"), ("load", "0010182")]
    assert [SPACE.decode(a) for a in plan2.actions] == [Relocate(A40, "A-001-02"), Place(A35, "0010182")]


def test_auto_relocations_target_clean_stacks_on_tiny_instances():
    checked = 0
    for seed in range(40):
        inst = tiny_instance(seed, n_load=6, n_other=4)
        load_set = set(inst.load_list)
        try:
            acts = [a for a in random_actions(inst, seed, p_relocate=0.0) if isinstance(a, Place)]
        except RuntimeError:
            continue
        plan = SPACE.expand(inst, acts)
        state = initial_state(inst)
        for m in plan.moves:
            if m.kind == "relocate":
                key = m.dst.rsplit("-", 1)[0]
                stack = next((s.boxes for s in state.yard.stacks if s.key == key), [])
                assert not set(stack) & load_set, (seed, m)
                checked += 1
            state = apply_move(inst, state, m)
    assert checked >= 10


# ------------------------------------------------------------------ errors
def test_expand_errors():
    inst = build_instance()
    with pytest.raises(ValueError):
        SPACE.expand(inst, [Place(make_container_id(SYNTHETIC_OWNER_CODES[1], 999_999), "0010182")])
    with pytest.raises(ValueError):
        SPACE.expand(inst, [Place(A35, "0990102")])
    with pytest.raises(ValueError):
        SPACE.expand(inst, [Place(A35, "garbage")])
    with pytest.raises(ValueError):
        SPACE.expand(stacked_fixture(), [Relocate(A35, "A-005-01")])
    with pytest.raises(ValueError):
        SPACE.expand(inst, [Discharge(A35)])
    with pytest.raises(ValueError):
        SPACE.expand(build_instance(layover=True), [Discharge(A35)])
    with pytest.raises(ValueError):
        SPACE.expand(inst, [Relocate(A35, "not-a-key")])
    with pytest.raises(ValueError):
        SPACE.expand(inst, [RestowOff(A35)])


def test_from_plan_errors():
    inst = build_instance()
    gate = MovePlan(id="g", instance_id=inst.id, call_index=0, moves=[
        GateIn(call_index=0, container_id=make_container_id(SYNTHETIC_OWNER_CODES[1], 999_998), dst="A-006-02-01")])
    with pytest.raises(ValueError):
        SPACE.from_plan(inst, gate)
    plan = SPACE.expand(inst, [Place(A35, "0010182")])
    with pytest.raises(ValueError):
        SPACE.from_plan(build_instance(layover=True), plan)
    # a lift of a box that is not on top can't be reproduced
    bad = MovePlan(id="b", instance_id=stacked_fixture().id, call_index=0, moves=[
        Load(call_index=0, container_id=A35, src="A-003-01-01", dst="0010182")])
    with pytest.raises(ValueError):
        SPACE.from_plan(stacked_fixture(), bad)
