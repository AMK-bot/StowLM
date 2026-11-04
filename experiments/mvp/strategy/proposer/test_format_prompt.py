"""Handwritten layout responses and deterministic public-summary contracts."""
from __future__ import annotations

import pytest

from experiments.mvp.strategy.headroom.layout import BlockLayout, BlockModel
from experiments.mvp.strategy.proposer.format import (
    LayoutError, format_layout, parse_layout, validate_layout,
)
from experiments.mvp.strategy.proposer.prompt import build_prompt, summary
from stowage.model.fixtures import build_instance


GOOD = """LAYOUT 1
THBKK/H = HC-02-1/deck,HC-02-2/deck
THBKK/M = HC-02-1/hold
THSGZ/M = HC-02-2/deck,HC-02-2/hold
"""


@pytest.fixture
def instance():
    return build_instance()


def test_handwritten_layout_preserves_exact_permissions(instance):
    layout = parse_layout(GOOD, instance)
    assert layout.allowed == {
        ("THBKK", "H"): frozenset({"HC-02-1/deck", "HC-02-2/deck"}),
        ("THBKK", "M"): frozenset({"HC-02-1/hold"}),
        ("THSGZ", "M"): frozenset({"HC-02-2/deck", "HC-02-2/hold"}),
    }
    assert format_layout(layout, instance) == GOOD
    assert parse_layout(GOOD.rstrip("\n"), instance).fingerprint == layout.fingerprint


def test_row_and_block_order_is_not_a_repair(instance):
    handwritten = """LAYOUT 1
THSGZ/M = HC-02-2/hold,HC-02-2/deck
THBKK/M = HC-02-1/hold
THBKK/H = HC-02-2/deck,HC-02-1/deck
"""
    assert format_layout(parse_layout(handwritten, instance)) == GOOD


@pytest.mark.parametrize("text", [
    "", "Here is the layout:\n" + GOOD, "```\n" + GOOD + "```",
    GOOD.replace("LAYOUT 1", "LAYOUT 2"), GOOD.replace("LAYOUT 1", "LAYOUT 1 "),
    GOOD + "\n", GOOD + "# done\n", GOOD.replace("\n", "\r\n"),
    GOOD.replace(" = ", "="), GOOD.replace(" = ", "  = "),
    GOOD.replace(",", ", "), GOOD.replace("THBKK/H", "thbkk/H"),
    GOOD.replace("THBKK/H", "THBKK/L"),
    GOOD.replace("HC-02-1/hold", "HC-02-9/hold"),
    GOOD.replace("HC-02-1/hold", "*"),
    GOOD.replace("HC-02-1/hold", ""),
    GOOD.replace("HC-02-1/hold", "HC-02-1/hold,HC-02-1/hold"),
    GOOD + "THBKK/M = HC-02-2/hold\n",
    GOOD.replace("THBKK/M = HC-02-1/hold\n", ""),
    GOOD + "SGSIN/H = HC-02-1/hold\n",
    GOOD.replace("THBKK/M", "THBKK/М"),  # Cyrillic M must not be normalized.
    GOOD.replace("THBKK/M", "THBKK/M\t"),
    None, {"allowed": []},
])
def test_invalid_handwritten_responses_are_never_repaired(instance, text):
    with pytest.raises(LayoutError):
        parse_layout(text, instance)


def test_permissions_are_not_capacity_reservations(instance):
    # Every booking may name the same small block. The parser does not silently
    # spread it across the ship or claim it physically packs; the solver judges.
    text = """LAYOUT 1
THBKK/H = HC-02-1/hold
THBKK/M = HC-02-1/hold
THSGZ/M = HC-02-1/hold
"""
    result = parse_layout(text, instance)
    assert all(blocks == frozenset({"HC-02-1/hold"}) for blocks in result.allowed.values())


def test_direct_layout_validation_is_nonmutating(instance):
    layout = parse_layout(GOOD, instance)
    assert validate_layout(layout, instance) is layout
    missing = BlockLayout({("THBKK", "H"): frozenset({"HC-02-1/deck"})})
    with pytest.raises(LayoutError, match="missing"):
        validate_layout(missing, instance)
    empty = BlockLayout({**layout.allowed, ("THBKK", "M"): frozenset()})
    with pytest.raises(LayoutError, match="empty"):
        validate_layout(empty, instance)


def test_summary_uses_vgm_not_cached_class_and_no_box_ids(instance):
    expected = summary(instance)
    assert "THBKK/H n=1 f=2 len=40:1 rf=1" in expected
    assert "THBKK/M n=1 f=1 len=20:1" in expected
    assert "THSGZ/M n=2 f=2 len=20:2" in expected
    assert "THBKK/A n=2 s=2 top=2 above=0" in expected
    assert "HC-02-1/deck cap=4 free=4 accept=4/4/4 plugs=4 retained=-" in expected
    for box in instance.containers:
        assert box.id not in expected
        box.weight_class = "L"
    assert summary(instance) == expected


def test_summary_is_deterministic_under_input_order(instance):
    expected = build_prompt(instance)
    instance.containers.reverse()
    instance.load_list.reverse()
    instance.yard.stacks.reverse()
    instance.vessel.cells.reverse()
    instance.vessel.stacks.reverse()
    instance.vessel.hatch_covers.reverse()
    assert build_prompt(instance) == expected


def test_capacity_subtracts_through_but_releases_discharge():
    instance = build_instance(layover=True)
    text = summary(instance)
    assert "HC-02-1/hold cap=4 free=3 accept=3/3/3 plugs=0 retained=THBKK:1" in text
    assert "rotation=THLCH>THBKK>THSGZ" in text
    assert "THLCH/" not in text  # discharged cargo is not a new booking.


def test_yard_exposure_counts_all_boxes_above(instance):
    stacks = {stack.key: stack for stack in instance.yard.stacks}
    bottom, top = stacks["B-001-01"].boxes[0], stacks["A-004-01"].boxes[0]
    stacks["B-001-01"].boxes.append(top)
    instance.yard.stacks = [stack for stack in instance.yard.stacks if stack.key != "A-004-01"]
    assert "THSGZ/B n=2 s=1 top=1 above=1" in summary(instance)
    assert bottom != top


def test_explicit_model_and_empty_load_contract(instance):
    model = BlockModel.from_instance(instance)
    assert summary(instance, model) == summary(instance)
    instance.load_list = []
    assert parse_layout("LAYOUT 1\n", instance, model).allowed == {}
    assert format_layout(BlockLayout({}), instance) == "LAYOUT 1\n"


def test_prompt_and_parser_pin_rule_epoch_inside_freeze_enabled_session(instance):
    from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled

    # Legacy E2 overrides classify 12t as L and 20t as M. In the enabled epoch
    # headroom.category_of ignores these overrides, so an unpinned caller would
    # instead prompt/parse M and H while its evaluator plans under L and M.
    instance.rule_overrides = {"expert.e2.weight_order": {
        "medium_min_kg": 15000, "heavy_min_kg": 30000,
    }}
    text = """LAYOUT 1
THBKK/L = HC-02-1/hold
THBKK/M = HC-02-1/deck,HC-02-2/deck
THSGZ/L = HC-02-2/deck,HC-02-2/hold
"""
    with rule_freeze_v1(enabled=False):
        expected_summary = summary(instance)
        expected_prompt = build_prompt(instance)
        expected_layout = parse_layout(text, instance)
    with rule_freeze_v1(enabled=True):
        assert rule_freeze_v1_enabled() is True
        assert summary(instance) == expected_summary
        assert build_prompt(instance) == expected_prompt
        assert parse_layout(text, instance).allowed == expected_layout.allowed
        assert format_layout(expected_layout, instance) == text
        assert rule_freeze_v1_enabled() is True  # restore the caller's context.
