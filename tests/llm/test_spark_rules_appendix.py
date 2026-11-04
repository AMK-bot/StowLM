"""D76 rules appendix: deterministic, and sourced from the checker's own values."""
from __future__ import annotations

import pytest

from llm.spark.rules_appendix import APPENDIX_VERSION, HEADER, rules_appendix
from llm.stowlm.format import BoxIndex
from stowage.io import load_instance
from stowage.model.cargo import DangerousGoods
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.tables import general_segregation


@pytest.fixture(params=[True, False], ids=["freeze-on", "freeze-off"])
def freeze(request):
    with rule_freeze_v1(enabled=request.param):
        yield request.param


@pytest.fixture
def instance(fixtures_dir, freeze):
    return load_instance(fixtures_dir / "empty_ship.json")


def _with_two_dg(instance, *, ship_type=None):
    """Add a class 2.1 box beside the fixture's class 3 box (SYNTHETIC test cargo)."""
    boxes = []
    for box in instance.containers:
        if box.dg is None and not any(b.dg is not None and b.dg.imdg_class == "2.1" for b in boxes):
            dg = DangerousGoods(un_number="1978", imdg_class="2.1", stowage_category="E",
                                packing_form="packaged_goods_in_closed_freight_containers", ctu_closed=True)
            box = box.model_copy(update={"dg": dg})
        boxes.append(box)
    overrides = dict(instance.rule_overrides)
    if ship_type is not None:
        overrides["imdg.segregation.7_4_3"] = {"ship_type": ship_type}
    return instance.model_copy(update={"containers": boxes, "rule_overrides": overrides})


def test_header_version_and_determinism(instance):
    first = rules_appendix(instance)
    assert first.startswith(HEADER + "\n") and APPENDIX_VERSION == "1.0"
    assert rules_appendix(instance.model_copy(deep=True)) == first
    assert rules_appendix(instance) == first


def test_stack_limits_match_checker_records(instance):
    # The checker reads Stack.max_mass_kg / max_mass_20_kg / max_mass_40_kg and
    # max_height_m (stowage.checker.rules._core); the appendix must show those values.
    text = rules_appendix(instance)
    for stack in instance.vessel.stacks:
        assert f"max stack mass {stack.max_mass_kg:.0f} kg" in text
        assert f"max height {stack.max_height_m:g} m" in text
        for rating in (stack.max_mass_20_kg, stack.max_mass_40_kg):
            if rating is not None:
                assert f"{rating:.0f} kg" in text


def test_crane_height_matches_port_profile(instance):
    port = instance.port
    height = port.max_crane_height_tiers or port.quay_crane.max_on_deck_tiers
    assert f"H = {height} on-deck tiers at {port.locode}" in rules_appendix(instance)


def test_each_dg_class_and_its_segregation(instance):
    mixed = _with_two_dg(instance)
    text = rules_appendix(mixed)
    index = BoxIndex([box.id for box in mixed.containers])
    dg_boxes = [box for box in mixed.containers if box.dg is not None]
    assert {box.dg.imdg_class for box in dg_boxes} == {"2.1", "3"}
    for box in dg_boxes:
        assert f"box {index.index(box.id):03d} UN{box.dg.un_number} class {box.dg.imdg_class}" in text
    level = general_segregation("2.1", "3").value
    assert level.isdigit()
    pair = "-".join(f"{index.index(box.id):03d}" for box in dg_boxes)
    assert f"Segregation level {level} " in text and pair in text
    # Without 7.4.3 ship facts the checker cannot clear the pair; say so.
    assert "fail closed" in text
    declared = rules_appendix(_with_two_dg(instance, ship_type="closed_holds"))
    assert "7.4.3 closed holds, closed/closed CTUs" in declared and "athwartships under deck" in declared


def test_instance_without_dg(instance):
    clean = instance.model_copy(update={"containers": [
        box.model_copy(update={"dg": None}) for box in instance.containers]})
    text = rules_appendix(clean)
    assert "No dangerous goods in this call's cargo." in text
    assert "Segregation level" not in text
