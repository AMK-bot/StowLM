"""A8: yard equipment and quay-crane heights are independent limits."""

import pytest

from stowage import ports
from stowage.model.port import PortProfile
from stowage.params import load_param_file, params_dir
from stowage.ports import profiles
from stowage.rules.catalogue import rule_freeze_v1


def test_effective_yard_height_does_not_use_lower_quay_height():
    port = ports.load_port("small_feeder")
    port.max_crane_height_tiers = 2
    port.quay_crane.max_on_deck_tiers = 2
    block = port.yard.blocks[0]
    assert ports.effective_yard_tiers(port, block) == 3
    assert ports.yard_blocking_tiers(port, block.id) == 3
    restored = PortProfile.model_validate_json(port.model_dump_json())
    assert ports.yard_blocking_tiers(restored, block.id) == 3


@pytest.mark.parametrize("binding", ["block", "port", "equipment"])
def test_public_yard_blocking_threshold_is_the_effective_storage_limit(binding):
    port = ports.load_port("small_feeder")
    block = port.yard.blocks[0]
    if binding == "block":
        block.max_tiers = 2
    elif binding == "port":
        port.yard_max_tiers = 2
    else:
        port.equipment[0].stack_tiers = 2
    assert ports.yard_blocking_tiers(port, block) == ports.effective_yard_tiers(port, block) == 2


def test_public_yard_blocking_threshold_retains_reference_validation():
    port = ports.load_port("small_feeder")
    with pytest.raises(ValueError, match="unknown yard block"):
        ports.yard_blocking_tiers(port, "MISSING")
    port.yard.blocks[0].equipment_id = "MISSING"
    with pytest.raises(ValueError, match="unknown equipment"):
        ports.yard_blocking_tiers(port, port.yard.blocks[0])


@pytest.mark.parametrize("enabled", [False, True])
def test_laem_yard_stays_five_while_only_quay_height_is_staged(enabled):
    with rule_freeze_v1(enabled=enabled):
        port = ports.load_port("laem_chabang_like")
        simulator = ports.load_simulator_params("laem_chabang_like")
    assert port.yard_max_tiers == port.equipment[0].stack_tiers == 5
    assert {ports.yard_blocking_tiers(port, block) for block in port.yard.blocks} == {5}
    assert port.quay_crane.max_on_deck_tiers == (8 if enabled else 7)
    assert port.max_crane_height_tiers == (8 if enabled else None)
    assert simulator.equipment_clearance_height_tiers == 6
    assert simulator.equipment_stacking_notation == "synthetic RTG; yard height 5"


@pytest.mark.parametrize("enabled", [False, True])
def test_clearance_and_retired_yard_staging_do_not_change_governing_limits(enabled, monkeypatch):
    raw = load_param_file("ports", "laem_chabang_like")
    raw["equipment"]["clearance_height_tiers"]["value"] = 11
    raw["equipment"]["stacking_notation"] = "synthetic equipment description"
    raw["legacy_height_limits"]["yard_max_tiers"]["value"] = 1
    raw["legacy_height_limits"]["equipment_stack_tiers"]["value"] = 1
    content = (params_dir("ports") / "laem_chabang_like.toml").read_bytes()
    monkeypatch.setattr(profiles, "load_toml_bytes", lambda value: raw)
    port, simulator = profiles._bundle.__wrapped__("laem_chabang_like", content, enabled, "test-catalogue")
    assert {ports.yard_blocking_tiers(port, block) for block in port.yard.blocks} == {5}
    assert port.quay_crane.max_on_deck_tiers == (8 if enabled else 7)
    assert simulator.equipment_clearance_height_tiers == 11
    assert simulator.equipment_stacking_notation == "synthetic equipment description"
