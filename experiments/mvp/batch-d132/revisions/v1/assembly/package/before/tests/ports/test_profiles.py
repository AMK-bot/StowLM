"""Port parameter conversion, physical tier limits and tiny-instance layouts."""

from hashlib import sha256
from copy import deepcopy
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from stowage.model.port import PortProfile
from stowage.model.yard import YardState
from stowage.params import load_param_file, params_dir
from stowage.ports import (
    effective_yard_tiers, empty_yard_state, list_ports, load_port,
    load_simulator_params, scaled_yard,
)
from stowage.ports import profiles
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1


PORT_IDS = ["laem_chabang_like", "singapore_like", "small_feeder", "us_west_coast"]
CRANE_HEIGHTS = {"laem_chabang_like": 8, "singapore_like": 10, "small_feeder": 4, "us_west_coast": 10}


@pytest.fixture(autouse=True)
def activated_parameter_contract():
    """This file validates declared D62 values; staging has separate off/on tests."""
    with rule_freeze_v1(enabled=True):
        yield


def _expected_values(node, key=""):
    """Read fixture magnitudes independently; duration assertions guard seconds."""
    if isinstance(node, dict):
        if "value" in node:
            if key.endswith("_s"):
                assert node["unit"] == "s"
            return node["value"]
        return {name: _expected_values(value, name) for name, value in node.items()}
    if isinstance(node, list):
        return [_expected_values(value) for value in node]
    return node


def _quantity_paths(node, path=()):
    if isinstance(node, dict):
        if "value" in node:
            yield path
        else:
            for key, value in node.items():
                yield from _quantity_paths(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _quantity_paths(value, (*path, index))


UNIT_CASES = [
    (archetype, path)
    for archetype in PORT_IDS
    for path in _quantity_paths(load_param_file("ports", archetype))
]


def test_available_archetypes():
    assert list_ports() == sorted([*PORT_IDS, "cat_lai_like", "hong_kong_like", "port_klang_like"])


@pytest.mark.parametrize("archetype", PORT_IDS)
def test_profiles_are_valid_empty_yards_with_parameter_limits(archetype):
    port = load_port(archetype)
    assert port.profile_kind == "operational"
    raw = load_param_file("ports", archetype)
    yard, equipment, quay = raw["yard"], raw["equipment"], raw["quay_crane"]
    assert PortProfile.model_validate_json(port.model_dump_json()) == port
    assert empty_yard_state(port) == YardState(yard_id=port.yard.id)
    assert len(port.yard.blocks) == yard["block_count"]["value"]
    assert port.max_crane_height_tiers == raw["max_crane_height_tiers"]["value"] == CRANE_HEIGHTS[archetype]
    assert port.berthing_side == raw["berthing_side"] == "port"
    for block in port.yard.blocks:
        assert block.n_bays == yard["bays_per_block"]["value"]
        assert block.n_rows == yard["rows_per_bay"]["value"]
        assert block.truck_lane_side == yard["truck_lane_side"] == "low"
        assert effective_yard_tiers(port, block) == min(
            yard["max_stack_tiers"]["value"], equipment["stack_height_tiers"]["value"])
        assert block.reefer_rows == [1]
    assert sum(block.dg_block for block in port.yard.blocks) == 1
    assert port.yard_max_tiers == yard["max_stack_tiers"]["value"]
    assert port.equipment[0].count == equipment["count"]["value"]
    assert port.equipment[0].stack_tiers == equipment["stack_height_tiers"]["value"]
    assert port.quay_crane.count == quay["count_per_berth"]["value"]
    assert port.quay_crane.max_on_deck_tiers == quay["max_on_deck_tier_count"]["value"]
    assert port.quay_crane.max_on_deck_tiers == port.max_crane_height_tiers
    assert port.quay_crane.max_lift_height_m is None
    assert port.water_density_kg_m3 == 1025
    assert port.max_draught_m == raw["berth_depth_m"]["value"]
    assert all(load_catalogue().get_rule(rule.rule_id) for rule in port.port_rules)
    digest = sha256((params_dir("ports") / f"{archetype}.toml").read_bytes()).hexdigest()
    assert f"params/ports/{archetype}.toml sha256:{digest}" in port.sources
    assert any("ASSUMPTION Q9" in source for source in port.sources)
    assert any("No tide or UKC increment" in source for source in port.sources)
    assert any("synthetic port-side berthing" in source for source in port.sources)
    assert any("synthetic lane" in source for source in port.sources)


def test_laem_expert_height_and_dg_handling_contract():
    port = load_port("laem_chabang_like")
    assert port.max_crane_height_tiers == 8
    assert port.yard_max_tiers == port.equipment[0].stack_tiers == 5
    assert all(block.max_tiers == 5 for block in port.yard.blocks)
    assert load_simulator_params("laem_chabang_like").equipment_clearance_height_tiers == 6
    refs = {rule.rule_id: rule.params for rule in port.port_rules}
    assert refs["port.dg.refused_class"] == {"classes": ["7"]}
    assert refs["port.dg.direct_delivery"] == {"classes": ["1"]}
    assert any("expert Q3, 2026-10-28" in source for source in port.sources)
    assert any("Expert Q9, 28 Oct 2026" in source for source in port.sources)


@pytest.mark.parametrize("archetype", PORT_IDS)
def test_parameter_loader_preserves_other_declared_berth_and_lane_sides(archetype):
    raw = deepcopy(load_param_file("ports", archetype))
    raw["berthing_side"] = "starboard"
    raw["yard"]["truck_lane_side"] = "high"
    content = (params_dir("ports") / f"{archetype}.toml").read_bytes()
    with patch.object(profiles.tomllib, "loads", return_value=raw):
        port, _ = profiles._bundle.__wrapped__(archetype, content)
    assert port.berthing_side == "starboard"
    assert all(block.truck_lane_side == "high" for block in port.yard.blocks)
    assert PortProfile.model_validate_json(port.model_dump_json()) == port


@pytest.mark.parametrize("field,value", [("max_crane_height_tiers", 0),
    ("max_crane_height_tiers", 7), ("berthing_side", "bow")])
def test_parameter_loader_rejects_invalid_governing_height_and_berthing_side(field, value):
    raw = deepcopy(load_param_file("ports", "laem_chabang_like"))
    if field == "max_crane_height_tiers":
        raw[field]["value"] = value
    else:
        raw[field] = value
    content = (params_dir("ports") / "laem_chabang_like.toml").read_bytes()
    with patch.object(profiles.tomllib, "loads", return_value=raw):
        with pytest.raises(ValueError):
            profiles._bundle.__wrapped__("laem_chabang_like", content)


@pytest.mark.parametrize("binding", ["block", "port", "equipment"])
def test_effective_tiers_resolve_all_three_limits(binding):
    port = load_port("laem_chabang_like")
    block = next(block for block in port.yard.blocks if block.dg_block)
    if binding == "block":
        block.max_tiers = 2
    elif binding == "port":
        port.yard_max_tiers = 2
    else:
        port.equipment[0].stack_tiers = 2
    assert effective_yard_tiers(port, block.id) == 2
    assert scaled_yard(port, blocks=1, bays=2, rows=1).yard.blocks[0].max_tiers == 2


@pytest.mark.parametrize("include_null_height", [False, True])
def test_legacy_yard_and_quay_limits_remain_independent_without_governing_height(include_null_height):
    payload = load_port("small_feeder").model_dump()
    if include_null_height:
        payload["max_crane_height_tiers"] = None
    else:
        del payload["max_crane_height_tiers"]
    payload["quay_crane"]["max_on_deck_tiers"] = 2
    legacy = PortProfile.model_validate(payload)
    assert legacy.max_crane_height_tiers is None
    assert legacy.yard_max_tiers == legacy.equipment[0].stack_tiers == 3
    assert effective_yard_tiers(legacy, legacy.yard.blocks[0]) == 3
    tiny = scaled_yard(legacy, blocks=1, bays=2, rows=1)
    assert tiny.max_crane_height_tiers is None
    assert tiny.yard.blocks[0].max_tiers == tiny.yard_max_tiers == 3
    assert tiny.quay_crane.max_on_deck_tiers == 2
    restored = PortProfile.model_validate_json(tiny.model_dump_json())
    assert restored == tiny
    assert effective_yard_tiers(restored, restored.yard.blocks[0].id) == 3


@pytest.mark.parametrize("archetype", PORT_IDS)
def test_tiny_yard_retains_dg_and_reefer_capabilities_without_mutating_source(archetype):
    source = load_port(archetype)
    before = source.model_dump_json()
    tiny = scaled_yard(source, blocks=1, bays=2, rows=1)
    assert len(tiny.yard.blocks) == 1
    block = tiny.yard.blocks[0]
    assert (block.n_bays, block.n_rows) == (2, 1)
    assert block.dg_block and block.reefer_rows == [1]
    assert block.max_tiers == effective_yard_tiers(source, block.id)
    assert empty_yard_state(tiny).stacks == []
    assert tiny.yard.id != source.yard.id
    tiny.equipment[0].count = 0
    assert source.model_dump_json() == before


@pytest.mark.parametrize("kwargs", [
    {"blocks": 0, "bays": 2, "rows": 1}, {"blocks": True, "bays": 2, "rows": 1},
    {"blocks": 1, "bays": 2.5, "rows": 1}, {"blocks": 100, "bays": 2, "rows": 1},
    {"blocks": 1, "bays": 999, "rows": 1}, {"blocks": 1, "bays": 2, "rows": 99},
])
def test_scaling_rejects_invalid_or_expanding_dimensions(kwargs):
    with pytest.raises(ValueError):
        scaled_yard(load_port("small_feeder"), **kwargs)


@pytest.mark.parametrize("archetype", PORT_IDS)
def test_typed_simulator_inputs_preserve_all_operational_values(archetype):
    raw = _expected_values(load_param_file("ports", archetype))
    simulator = load_simulator_params(archetype)
    for name in ("services", "traffic", "gate_in", "dwell", "placement", "simulation"):
        # JSON mode normalizes immutable tuples back into parameter-file lists.
        actual = simulator.model_dump(mode="json")[name]
        if name == "traffic":
            actual = {key: value for key, value in actual.items() if value is not None}
        assert actual == raw[name]
    assert simulator.berth_count == raw["yard"]["berth_count"]
    assert simulator.blocks_per_berth == raw["yard"]["blocks_per_berth"]
    assert simulator.initial_occupancy_fraction == raw["yard"]["initial_occupancy_fraction"]
    assert simulator.equipment_stacking_notation == raw["equipment"]["stacking_notation"]
    assert simulator.equipment_clearance_height_tiers == raw["equipment"]["clearance_height_tiers"]
    assert simulator.services[0].calls_per_second == simulator.services[0].calls_per_week / 604800
    with pytest.raises(ValidationError):
        simulator.dwell.import_cv = 1


def test_dwell_days_are_rejected_instead_of_becoming_seconds():
    content = (params_dir("ports") / "small_feeder.toml").read_bytes()
    changed = content.replace(
        b'import_mean_s = { value = 259200, unit = "s"',
        b'import_mean_s = { value = 3, unit = "days"',
    )
    assert changed != content
    with pytest.raises(ValueError, match=r"dwell\.import_mean_s.*s.*days"):
        profiles._bundle("small_feeder", changed)


@pytest.mark.parametrize("archetype,path", UNIT_CASES)
def test_every_quantity_rejects_wrong_units(archetype, path):
    raw = deepcopy(load_param_file("ports", archetype))
    entry = raw
    for key in path:
        entry = entry[key]
    entry["unit"] = "incorrect_unit"
    content = (params_dir("ports") / f"{archetype}.toml").read_bytes()
    with patch.object(profiles.tomllib, "loads", return_value=raw):
        with pytest.raises(ValueError, match="incorrect_unit"):
            # Bypass only caching so each independent malformed input is parsed.
            profiles._bundle.__wrapped__(archetype, content)


@pytest.mark.parametrize("mutation", ["missing_unit", "bare_numeric"])
def test_expected_quantity_rejects_missing_units_and_bare_numbers(mutation):
    raw = load_param_file("ports", "small_feeder")
    entry = raw["dwell"]["import_mean_s"]
    if mutation == "missing_unit":
        del entry["unit"]
    else:
        raw["dwell"]["import_mean_s"] = entry["value"]
    content = (params_dir("ports") / "small_feeder.toml").read_bytes()
    with patch.object(profiles.tomllib, "loads", return_value=raw):
        with pytest.raises(ValueError, match=r"dwell\.import_mean_s: expected quantity with unit 's'"):
            profiles._bundle.__wrapped__("small_feeder", content)


def test_small_feeder_durations_have_independent_second_expectations():
    simulator = load_simulator_params("small_feeder")
    assert simulator.dwell.import_mean_s == 3 * 24 * 60 * 60
    assert simulator.dwell.transshipment_mean_s == 3 * 24 * 60 * 60
    assert simulator.services[0].arrival_jitter_s == 3 * 60 * 60
    assert simulator.gate_in.cutoff_before_arrival_s == 4 * 60 * 60
    assert simulator.gate_in.window_before_cutoff_s == 3 * 24 * 60 * 60


def test_fresh_profiles_and_shared_parse_cache():
    profiles._bundle.cache_clear()
    with patch.object(profiles.tomllib, "loads", wraps=profiles.tomllib.loads) as parse:
        a = load_port("small_feeder")
        b = load_port("small_feeder")
        load_simulator_params("small_feeder")
        assert parse.call_count == 1
    assert a == b
    a.yard.blocks[0].max_tiers = 1
    assert load_port("small_feeder") == b


def test_unknown_inputs_fail_explicitly():
    for name in ("missing", "../cargo/feeder_export"):
        with pytest.raises(ValueError, match="unknown port archetype"):
            load_port(name)
    with pytest.raises(ValueError, match="unknown yard block"):
        effective_yard_tiers(load_port("small_feeder"), "MISSING")
