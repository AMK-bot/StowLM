"""Contract and provenance checks for the synthetic port simulator inputs."""

from copy import deepcopy
from datetime import date
import math
from pathlib import Path
import re
import tomllib
from urllib.parse import urlparse

import pytest


PORTS = Path(__file__).resolve().parents[2] / "params" / "ports"
PORT_IDS = ("laem_chabang_like", "singapore_like", "small_feeder", "us_west_coast")
NEXT_CALL_ENVIRONMENTS = {
    "cat_lai_like": ("VNSGN", 1000),
    "hong_kong_like": ("HKHKG", 1025),
    "port_klang_like": ("MYPKG", 1025),
}


def test_singapore_like_labels_the_implemented_gateway_arrival_scope():
    raw = tomllib.loads((PORTS / "singapore_like.toml").read_text())
    assert "gateway" in raw["label"].lower()
    assert "APPROXIMATION: no transshipment inflow" in raw["description"]
    assert "gateway-style gate-in only" in raw["description"]
    assert "gateway" in (PORTS / "README.md").read_text().split("`singapore_like.toml`")[1].splitlines()[0]


def test_a8_laem_chabang_yard_height_is_synthetic_five_in_both_regimes():
    raw = tomllib.loads((PORTS / "laem_chabang_like.toml").read_text())
    assert raw["yard"]["max_stack_tiers"]["value"] == 5
    assert raw["equipment"]["stack_height_tiers"]["value"] == 5
    assert raw["legacy_height_limits"]["yard_max_tiers"]["value"] == 5
    assert raw["legacy_height_limits"]["equipment_stack_tiers"]["value"] == 5
    assert raw["equipment"]["stacking_notation"] == "synthetic RTG; yard height 5"
    assert raw["legacy_height_limits"]["equipment_stacking_notation"] == "synthetic RTG; yard height 5"
    assert raw["equipment"]["clearance_height_tiers"]["value"] == 6
    assert "informational" in raw["equipment"]["clearance_height_tiers"]["assumption"]
    for table, field in ((raw["yard"], "max_stack_tiers"), (raw["equipment"], "stack_height_tiers")):
        assert "source" not in table[field]
        assert "synthetic RTG" in table[field]["assumption"]
    assert raw["max_crane_height_tiers"]["value"] == 8
    assert raw["max_crane_height_tiers"]["source"]["question"] == "Q3"
    assert raw["quay_crane"]["max_on_deck_tier_count"]["value"] == 8
    label = "yard height 5, synthetic RTG; quay crane H = 8, expert Q3"
    assert label in raw["description"]
    assert label in (PORTS / "README.md").read_text()


# Exact units are part of the contract: consumers unwrap values without conversion.
METRIC_UNITS = {
    "schema_version": "version",
    "water_density_kg_m3": "kg/m3",
    "max_crane_height_tiers": "tiers",
    "berth_depth_m": "m",
    "gross_moves_per_hour": "moves/h/crane",
    "pre_operation_allowance_h": "h",
    "post_operation_allowance_h": "h",
    "yard_max_tiers": "tiers",
    "equipment_stack_tiers": "tiers",
    "equipment_clearance_height_tiers": "tiers",
    "quay_max_on_deck_tiers": "tiers",
    "berth_count": "berths",
    "block_count": "blocks",
    "blocks_per_berth": "blocks/berth",
    "bays_per_block": "bays",
    "rows_per_bay": "rows",
    "max_stack_tiers": "tiers",
    "initial_occupancy_fraction": "fraction",
    "stack_height_tiers": "tiers",
    "clearance_height_tiers": "tiers",
    "count": "machines",
    "max_on_deck_tier_count": "tiers",
    "count_per_berth": "cranes/berth",
    "transshipment_fraction": "fraction",
    "mean_teu_per_box": "TEU/box",
    "annual_reference_throughput_teu": "TEU/year",
    "calls_per_week": "calls/week",
    "mean_load_boxes": "boxes/call",
    "mean_discharge_boxes": "boxes/call",
    "call_volume_cv": "dimensionless",
    "arrival_jitter_s": "s",
    "window_before_cutoff_s": "s",
    "cutoff_before_arrival_s": "s",
    "start_before_cutoff_s": "s",
    "end_before_cutoff_s": "s",
    "share": "fraction",
    "import_mean_s": "s",
    "transshipment_mean_s": "s",
    "import_cv": "dimensionless",
    "transshipment_cv": "dimensionless",
    "cluster_misplacement_probability": "fraction",
    "retrieval_order_error_probability": "fraction",
}


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_provenance(node, path="root"):
    """All numeric leaves must belong to one documented, unit-bearing metric."""
    if isinstance(node, dict):
        if "value" in node:
            values = node["value"]
            values = values if isinstance(values, list) else [values]
            assert values and all(is_number(v) for v in values), path
            assert all(math.isfinite(v) for v in values), path
            assert isinstance(node.get("unit"), str) and node["unit"].strip(), path
            assert ("source" in node) != ("assumption" in node), path
            if "assumption" in node:
                assert isinstance(node["assumption"], str) and node["assumption"].strip(), path
            else:
                source = node["source"]
                assert isinstance(source, dict), path
                if "document" in source:
                    assert "url" not in source, path
                    assert source["document"] == "docs/expert/2026-10-28-written-answers.md", path
                    assert source["question"] == "Q3", path
                    assert source["read_date"] == "2026-10-28", path
                    assert (PORTS.parents[1] / source["document"]).is_file(), path
                else:
                    url = urlparse(source.get("url", ""))
                    assert url.scheme in ("http", "https") and url.netloc, path
                assert isinstance(source.get("says"), str) and source["says"].strip(), path
                read_date = source.get("read_date")
                assert isinstance(read_date, str), path
                assert date.fromisoformat(read_date).isoformat() == read_date, path
            for key, value in node.items():
                if key != "value":
                    check_provenance(value, f"{path}.{key}")
        else:
            for key, value in node.items():
                check_provenance(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            check_provenance(value, f"{path}[{index}]")
    else:
        assert not is_number(node), f"Undocumented numeric leaf at {path}"


def metric(table, key, *, unit=None, positive=False, integer=False, probability=False):
    entry = table[key]
    assert isinstance(entry, dict), key
    value = entry["value"]
    assert is_number(value), key
    expected_unit = METRIC_UNITS[key]
    if unit is not None:
        assert unit == expected_unit, key
    assert entry["unit"] == expected_unit, key
    if positive:
        assert value > 0, key
    if integer:
        assert isinstance(value, int), key
    if probability:
        assert 0 <= value <= 1, key
    return value


def expert_assumption(table, keys, question):
    for key in keys:
        reason = table[key].get("assumption", "")
        assert re.search(rf"\b(?:Q\s*|question\s*){question}\b", reason, re.I), key


def test_four_operational_archetypes_and_three_next_call_environments():
    assert {p.stem for p in PORTS.glob("*.toml")} == set(PORT_IDS) | set(NEXT_CALL_ENVIRONMENTS)


def check_next_call_environment_contract(port, port_id):
    check_provenance(port)
    assert port["id"] == port_id
    assert port["synthetic"] is True
    assert port["profile_kind"] == "next_call_environment"
    assert re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", port["locode"])
    for field in ("label", "description"):
        assert isinstance(port[field], str) and port[field].strip(), field
    assert metric(port, "schema_version", positive=True, integer=True) == 1
    assert "max_crane_height_tiers" not in port  # no crane capability is declared
    assert port["berthing_side"] == "port"
    assert port["berthing_side_assumption"].startswith("ASSUMPTION:")
    metric(port, "water_density_kg_m3", positive=True)
    assumption = port["water_density_kg_m3"]["assumption"]
    assert assumption.startswith("ASSUMPTION:")
    assert "no measured dock data" in assumption
    assert not {"yard", "equipment", "traffic", "services", "simulation"} & port.keys()
    # Raw timing evidence does not assert a crane count, reach or operational H.
    assert set(port["quay_crane"]) == {
        "gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h",
    }
    metric(port, "berth_depth_m", positive=True)
    for field in port["quay_crane"]:
        metric(port["quay_crane"], field, positive=True)


@pytest.mark.parametrize("port_id", NEXT_CALL_ENVIRONMENTS)
def test_next_call_environment_contract_and_density_provenance(port_id):
    with (PORTS / f"{port_id}.toml").open("rb") as stream:
        port = tomllib.load(stream)
    check_next_call_environment_contract(port, port_id)
    locode, density = NEXT_CALL_ENVIRONMENTS[port_id]
    assert port["locode"] == locode
    assert port["water_density_kg_m3"]["value"] == density
    assumption = port["water_density_kg_m3"]["assumption"]
    if locode == "VNSGN":
        for description in ("Cat Lai", "Saigon River", "fresh water", "conservative", "draught", "brackish", "1015"):
            assert description in assumption
    else:
        assert "D34" in assumption and "seaport" in assumption


@pytest.mark.parametrize("field", [
    "id", "label", "description", "synthetic", "profile_kind", "locode",
    "schema_version", "water_density_kg_m3", "berthing_side", "berthing_side_assumption",
])
def test_next_call_environment_contract_rejects_missing_required_fields(field):
    with (PORTS / "cat_lai_like.toml").open("rb") as stream:
        port = tomllib.load(stream)
    del port[field]
    with pytest.raises(KeyError, match=field):
        check_next_call_environment_contract(port, "cat_lai_like")


@pytest.mark.parametrize("field", ["schema_version", "water_density_kg_m3"])
def test_next_call_environment_contract_rejects_wrong_metric_units(field):
    with (PORTS / "cat_lai_like.toml").open("rb") as stream:
        port = tomllib.load(stream)
    port[field]["unit"] = "incorrect_unit"
    with pytest.raises(AssertionError, match=field):
        check_next_call_environment_contract(port, "cat_lai_like")


def check_port_contract(port, port_id):
    check_provenance(port)
    assert port["id"] == port_id
    assert port["synthetic"] is True
    for field in ("label", "description"):
        assert isinstance(port[field], str) and port[field].strip()
    metric(port, "schema_version", positive=True, integer=True)
    crane_height = metric(port, "max_crane_height_tiers", positive=True, integer=True)
    assert port["berthing_side"] in {"port", "starboard"}
    assert port["berthing_side_assumption"].startswith("ASSUMPTION:")

    yard = port["yard"]
    assert yard["truck_lane_side"] in {"low", "high"}
    assert yard["truck_lane_side_assumption"].startswith("ASSUMPTION:")
    dimensions = {key: metric(yard, key, positive=True, integer=True) for key in (
        "berth_count", "block_count", "blocks_per_berth", "bays_per_block",
        "rows_per_bay", "max_stack_tiers",
    )}
    assert dimensions["block_count"] == dimensions["berth_count"] * dimensions["blocks_per_berth"]
    metric(yard, "initial_occupancy_fraction", probability=True)

    equipment = port["equipment"]
    assert equipment["type"] in {"RTG", "RMG", "straddle", "reach_stacker"}
    height = metric(equipment, "stack_height_tiers", positive=True, integer=True)
    assert equipment["stacking_notation"] == (
        "synthetic RTG; yard height 5" if port_id == "laem_chabang_like" else f"1-over-{height}"
    )
    clearance = metric(equipment, "clearance_height_tiers", positive=True, integer=True)
    metric(equipment, "count", positive=True, integer=True)
    assert dimensions["max_stack_tiers"] <= height
    assert clearance == height + 1
    assert dimensions["max_stack_tiers"] <= height
    if port_id == "laem_chabang_like":
        legacy = port["legacy_height_limits"]
        for key, expected in (("yard_max_tiers", 5), ("equipment_stack_tiers", 5),
                              ("equipment_clearance_height_tiers", 6), ("quay_max_on_deck_tiers", 7)):
            assert metric(legacy, key, positive=True, integer=True) == expected
        assert legacy["equipment_stacking_notation"] == "synthetic RTG; yard height 5"
        assert crane_height == 8
        assert dimensions["max_stack_tiers"] == height == 5
        assert port["max_crane_height_tiers"]["source"]["question"] == "Q3"
        for table, key in ((yard, "max_stack_tiers"), (equipment, "stack_height_tiers")):
            assert table[key]["assumption"].startswith("ASSUMPTION:")
            assert "synthetic RTG" in table[key]["assumption"]
    else:
        expert_assumption(yard, ("max_stack_tiers",), 3)
        expert_assumption(equipment, ("stack_height_tiers",), 3)
        assert port["max_crane_height_tiers"]["assumption"].startswith("ASSUMPTION:")
    expert_assumption(equipment, ("clearance_height_tiers",), 3)

    quay = port["quay_crane"]
    metric(port, "berth_depth_m", positive=True)
    for field in ("gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h"):
        metric(quay, field, positive=True)
    assert metric(quay, "max_on_deck_tier_count", positive=True, integer=True) == crane_height
    metric(quay, "count_per_berth", positive=True, integer=True)
    if port_id == "laem_chabang_like":
        assert quay["max_on_deck_tier_count"]["source"]["question"] == "Q3"
        assert port["port_rules"] == [
            {"rule_id": "port.dg.refused_class", "params": {"classes": ["7"]}},
            {"rule_id": "port.dg.direct_delivery", "params": {"classes": ["1"]}},
        ]
        assert "Q9, 28 Oct 2026" in port["port_rules_source"]
    else:
        expert_assumption(quay, ("max_on_deck_tier_count",), 3)

    traffic = port["traffic"]
    metric(traffic, "transshipment_fraction", probability=True)
    teu_per_box = metric(traffic, "mean_teu_per_box", positive=True)
    assert 1 <= teu_per_box <= 2
    if "annual_reference_throughput_teu" in traffic:
        metric(traffic, "annual_reference_throughput_teu", positive=True)

    services = port["services"]
    assert isinstance(services, list) and services
    assert len({service["id"] for service in services}) == len(services)
    for service in services:
        for field in ("id", "kind"):
            assert isinstance(service[field], str) and service[field].strip()
        metric(service, "calls_per_week", positive=True, integer=True)
        for key in ("mean_load_boxes", "mean_discharge_boxes"):
            mean = metric(service, key, positive=True)
            # The documented negative binomial requires variance greater than mean.
            assert (service["call_volume_cv"]["value"] * mean) ** 2 > mean
        for key in ("call_volume_cv", "arrival_jitter_s"):
            assert metric(service, key, unit="s" if key.endswith("_s") else None) >= 0
        expert_assumption(service, ("calls_per_week", "mean_load_boxes", "mean_discharge_boxes"), 8)

    gate = port["gate_in"]
    assert gate["process"] == "piecewise_uniform_before_cutoff"
    window = metric(gate, "window_before_cutoff_s", unit="s", positive=True)
    assert metric(gate, "cutoff_before_arrival_s", unit="s") >= 0
    bins = gate["bins"]
    assert isinstance(bins, list) and bins
    intervals = []
    for bin_ in bins:
        start = metric(bin_, "start_before_cutoff_s", unit="s")
        end = metric(bin_, "end_before_cutoff_s", unit="s")
        share = metric(bin_, "share", probability=True)
        assert 0 <= end < start <= window
        intervals.append((end, start, share))
    intervals.sort()
    assert intervals[0][0] == 0 and intervals[-1][1] == window
    for left, right in zip(intervals, intervals[1:]):
        assert left[1] == right[0]
    assert math.isclose(sum(i[2] for i in intervals), 1.0, abs_tol=1e-9)

    dwell = port["dwell"]
    assert dwell["distribution"] == "gamma"
    for key in ("import_mean_s", "transshipment_mean_s"):
        metric(dwell, key, unit="s", positive=True)
    for key in ("import_cv", "transshipment_cv"):
        metric(dwell, key, positive=True)

    placement = port["placement"]
    assert placement["cluster_keys"] == ["vessel_call_id", "pod", "size_type", "weight_class"]
    assert placement["preferred_stack_order"] == "later_retrieval_below_earlier_retrieval"
    assert placement["noise_model"] == "independent_cluster_and_order_errors"
    noise_fields = ("cluster_misplacement_probability", "retrieval_order_error_probability")
    for key in noise_fields:
        metric(placement, key, probability=True)
    expert_assumption(placement, noise_fields, 5)

    expected_simulation = {
        "call_arrival_process": "weekly_schedule_with_uniform_jitter",
        "volume_distribution": "negative_binomial",
        "overflow_policy": "hold_in_external_queue",
        "import_exit_process": "sample_dwell_at_discharge",
        "transshipment_connection_policy": "next_compatible_call_after_dwell",
    }
    for key, value in expected_simulation.items():
        assert port["simulation"][key] == value


@pytest.mark.parametrize("port_id", PORT_IDS)
def test_port_contract(port_id):
    with (PORTS / f"{port_id}.toml").open("rb") as stream:
        check_port_contract(tomllib.load(stream), port_id)


@pytest.mark.parametrize("key,wrong_unit", [
    ("mean_load_boxes", "TEU/call"),
    ("calls_per_week", "calls/s"),
])
def test_port_contract_rejects_wrong_service_units(key, wrong_unit):
    with (PORTS / "laem_chabang_like.toml").open("rb") as stream:
        port = tomllib.load(stream)
    port["services"][0][key]["unit"] = wrong_unit
    with pytest.raises(AssertionError, match=key):
        check_port_contract(port, "laem_chabang_like")


def metric_paths(node, path=()):
    """Enumerate all metric occurrences, including repeated services and bins."""
    if isinstance(node, dict):
        if "value" in node:
            yield path
        else:
            for key, value in node.items():
                yield from metric_paths(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from metric_paths(value, (*path, index))


@pytest.mark.parametrize("port_id", PORT_IDS)
def test_port_contract_rejects_wrong_units_for_every_metric(port_id):
    with (PORTS / f"{port_id}.toml").open("rb") as stream:
        port = tomllib.load(stream)
    for path in metric_paths(port):
        mutated = deepcopy(port)
        entry = mutated
        for key in path:
            entry = entry[key]
        entry["unit"] = "incorrect_unit"
        with pytest.raises(AssertionError, match=path[-1]):
            check_port_contract(mutated, port_id)


@pytest.mark.parametrize("value", [3, 0.5, [1, 2]])
def test_provenance_rejects_bare_numbers(value):
    with pytest.raises(AssertionError, match="Undocumented numeric leaf"):
        check_provenance({"nested": {"undocumented": value}})


@pytest.mark.parametrize("mutation", ["no_provenance", "both", "empty_assumption", "no_unit", "bad_url", "no_claim", "bad_date"])
def test_provenance_rejects_incomplete_metric(mutation):
    entry = {
        "value": [1, 2], "unit": "count",
        "source": {"url": "https://example.org/reference", "says": "Public reference count.", "read_date": "2026-10-27"},
    }
    entry = deepcopy(entry)
    if mutation == "no_provenance":
        del entry["source"]
    elif mutation == "both":
        entry["assumption"] = "Synthetic choice."
    elif mutation == "empty_assumption":
        del entry["source"]
        entry["assumption"] = " "
    elif mutation == "no_unit":
        del entry["unit"]
    elif mutation == "bad_url":
        entry["source"]["url"] = "file:///private/data"
    elif mutation == "no_claim":
        entry["source"]["says"] = ""
    elif mutation == "bad_date":
        entry["source"]["read_date"] = "yesterday"
    with pytest.raises((AssertionError, ValueError)):
        check_provenance({"metric": entry})


def test_provenance_accepts_documented_scalar_and_array_and_boolean():
    check_provenance({
        "synthetic": True,
        "count": {"value": 3, "unit": "count", "assumption": "Q8: synthetic workload pending expert input."},
        "shares": {"value": [0.25, 0.75], "unit": "fraction", "source": {
            "url": "https://example.org/reference", "says": "Reference shares.", "read_date": "2026-10-27",
        }},
    })
