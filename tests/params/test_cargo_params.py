"""Validate the public/synthetic cargo sampler contract and numeric provenance."""

from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path
import runpy
import tomllib
from urllib.parse import urlparse

import pytest


CARGO = Path(__file__).resolve().parents[2] / "params" / "cargo"


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_provenance(node, path="root"):
    """A numeric leaf must be in a finite, unit-bearing documented metric."""
    if isinstance(node, dict):
        if "value" in node and not isinstance(node["value"], dict):
            values = node["value"]
            values = values if isinstance(values, list) else [values]
            assert values and all(is_number(value) and math.isfinite(value) for value in values), path
            assert isinstance(node.get("unit"), str) and node["unit"].strip(), path
            assert ("source" in node) != ("assumption" in node), path
            if "assumption" in node:
                assert isinstance(node["assumption"], str) and node["assumption"].strip(), path
            else:
                source = node["source"]
                assert isinstance(source, dict), path
                for key in ("document", "locator", "url", "read_date"):
                    assert isinstance(source.get(key), str) and source[key].strip(), (path, key)
                parsed = urlparse(source["url"])
                assert parsed.scheme in {"http", "https"} and parsed.netloc, path
                assert date.fromisoformat(source["read_date"]).isoformat() == source["read_date"], path
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


def metric(table, key, unit=None, positive=False, probability=False):
    entry = table[key]
    assert isinstance(entry, dict), key
    value = entry["value"]
    assert is_number(value) and math.isfinite(value), key
    if unit is not None:
        assert entry["unit"] == unit, key
    if positive:
        assert value > 0, key
    if probability:
        assert 0 <= value <= 1, key
    return value


def distribution(rows, identity):
    assert isinstance(rows, list) and rows
    assert all(isinstance(row.get(identity), str) and row[identity].strip() for row in rows)
    assert len({row[identity] for row in rows}) == len(rows)
    shares = [metric(row, "share", probability=True) for row in rows]
    assert math.isclose(sum(shares), 1.0, rel_tol=0, abs_tol=1e-9)
    return {row[identity] for row in rows}


def oog_eligibility(cargo):
    """Use the generator's explicit list, checking its equipment-flag contract."""
    eligible = cargo["oog"]["eligible_equipment_ids"]
    compatible = {item["id"] for item in cargo["equipment"] if item["oog_compatible"]}
    assert eligible and len(eligible) == len(set(eligible))
    assert set(eligible) == compatible
    return set(eligible)


@pytest.fixture(scope="module")
def cargo():
    with (CARGO / "feeder_export.toml").open("rb") as stream:
        return tomllib.load(stream)


def test_all_cargo_tomls_parse_and_have_numeric_provenance():
    files = list(CARGO.glob("*.toml"))
    assert files, "Cargo parameter files are required"
    for path in files:
        with path.open("rb") as stream:
            check_provenance(tomllib.load(stream), path.name)


def test_equipment_and_full_empty_weights(cargo):
    equipment = cargo["equipment"]
    distribution(equipment, "id")
    lengths = set()
    for item in equipment:
        assert isinstance(item["iso_size_type"], str) and item["iso_size_type"]
        assert isinstance(item["reefer"], bool)
        assert isinstance(item["oog_compatible"], bool)
        lengths.add(round(metric(item, "length_m", "m", positive=True), 2))
        for key in ("width_m", "height_m"):
            metric(item, key, "m", positive=True)
        metric(item, "empty_share", probability=True)
        tare = metric(item, "tare_kg", "kg", positive=True)
        rating = metric(item, "max_gross_kg", "kg", positive=True)
        loaded = item["loaded_weight_kg"]
        assert loaded["family"] == "triangular"
        lower, mode, upper = (metric(loaded, key, "kg", positive=True) for key in ("min", "mode", "max"))
        assert tare <= lower <= mode <= upper <= rating
        assert lower < upper, "Triangular loaded weights must have nonzero support"
        empty = item["empty_weight_kg"]
        assert empty["family"] == "point_mass"
        assert metric(empty, "value", "kg", positive=True) == tare
    # Nominal ISO lengths: 20, 40 and 45 feet, expressed in SI in the file.
    assert {6.06, 12.19, 13.72} <= lengths
    assert any(item["reefer"] for item in equipment)
    assert any(metric(item, "height_m", "m") > 2.8 for item in equipment), "High cube required"


def test_dg_share_class_mix_and_un_choices(cargo):
    dg = cargo["dg"]
    metric(dg, "share_of_eligible_loaded_boxes", probability=True)
    equipment = {item["id"] for item in cargo["equipment"]}
    assert dg["eligible_equipment_ids"] and set(dg["eligible_equipment_ids"]) <= equipment
    assert distribution(dg["classes"], "id") == {
        "1.4", "2.1", "2.2", "2.3", "3", "4.1", "4.2", "4.3", "5.1", "5.2",
        "6.1", "6.2", "7", "8", "9",
    }
    for class_ in dg["classes"]:
        distribution(class_["un_numbers"], "un_number")
        for choice in class_["un_numbers"]:
            assert len(choice["un_number"]) == 4 and choice["un_number"].isdigit()
            assert 1 <= int(choice["un_number"]) <= 9999


def test_special_stowage_and_oog(cargo):
    special = cargo["special_stowage"]
    metric(special, "share_of_loaded_boxes", probability=True)
    assert distribution(special["requests"], "id") == {"under_deck", "on_deck", "away_from_heat", "top_stowage"}
    oog = cargo["oog"]
    metric(oog, "share_of_loaded_compatible_boxes", probability=True)
    distribution(oog["profiles"], "id")
    oog_eligibility(cargo)
    extents = ("over_height_m", "over_width_each_side_m", "over_length_each_end_m")
    for profile in oog["profiles"]:
        values = [metric(profile, key, "m") for key in extents]
        assert all(value >= 0 for value in values)
        assert any(value > 0 for value in values)
    for key in extents:
        assert any(metric(profile, key, "m") > 0 for profile in oog["profiles"])


def test_rotations_pod_shares_and_weight_dependence(cargo):
    rotations = cargo["rotations"]
    assert len(rotations) == 3
    distribution(rotations, "id")
    for rotation in rotations:
        calls = rotation["port_calls"]
        assert isinstance(calls, list) and all(isinstance(port, str) and port for port in calls)
        assert len(calls) >= 3
        pods = distribution(rotation["pods"], "port")
        # These demand profiles load at the first call. Returning to that origin
        # later does not make it an eligible discharge port (profile contract).
        assert calls[0] not in pods
        assert pods <= set(calls[1:])
        assert all(calls.index(port) > 0 for port in pods)
        for pod in rotation["pods"]:
            metric(pod, "payload_weight_multiplier", positive=True)


def test_sampling_policies_and_unconditioned_event_marginals(cargo):
    for key in ("order", "weight_dependence", "independence", "empty_policy", "dg_policy",
                "e8_policy", "oog_policy", "feasibility_policy", "onboard_policy"):
        assert isinstance(cargo["sampling"][key], str) and cargo["sampling"][key].strip(), key
    for key in ("authority", "url", "locator", "read_date", "synthetic_policy"):
        assert isinstance(cargo["vgm"][key], str) and cargo["vgm"][key].strip(), key
    equipment = cargo["equipment"]
    loaded_share = sum(metric(item, "share") * (1 - metric(item, "empty_share")) for item in equipment)
    eligible_dg = set(cargo["dg"]["eligible_equipment_ids"])
    assert all(not item["reefer"] and not item["oog_compatible"]
               for item in equipment if item["id"] in eligible_dg)
    dg_share = sum(metric(item, "share") * (1 - metric(item, "empty_share"))
                   for item in equipment if item["id"] in eligible_dg)
    dg_share *= metric(cargo["dg"], "share_of_eligible_loaded_boxes", probability=True)
    eligible_oog = oog_eligibility(cargo)
    oog_share = sum(metric(item, "share") * (1 - metric(item, "empty_share"))
                    for item in equipment if item["id"] in eligible_oog)
    oog_share *= metric(cargo["oog"], "share_of_loaded_compatible_boxes", probability=True)
    special = cargo["special_stowage"]
    assert special["share_of_loaded_boxes"]["unit"] == "fraction_of_loaded_non_oog_boxes"
    special_share = (loaded_share - oog_share) * metric(special, "share_of_loaded_boxes", probability=True)
    for table, expected in ((cargo["dg"], dg_share), (cargo["oog"], oog_share), (special, special_share)):
        actual = metric(table, "implied_share_of_all_boxes", probability=True)
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9)


@pytest.mark.parametrize("value", [3, 0.5, [1, 2]])
def test_numeric_provenance_rejects_bare_numbers(value):
    with pytest.raises(AssertionError, match="Undocumented numeric leaf"):
        check_provenance({"undocumented": value})


@pytest.mark.parametrize("mutation", ["no_provenance", "both", "empty_assumption", "no_unit", "bad_url", "no_document", "no_locator", "bad_date", "nan"])
def test_numeric_provenance_rejects_incomplete_metrics(mutation):
    entry = deepcopy({
        "value": 1, "unit": "kg", "source": {
            "document": "Public source", "locator": "Table 1", "url": "https://example.org/source",
            "read_date": "2026-10-27",
        },
    })
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
    elif mutation in {"no_document", "no_locator"}:
        del entry["source"][mutation.removeprefix("no_")]
    elif mutation == "bad_date":
        entry["source"]["read_date"] = "yesterday"
    elif mutation == "nan":
        entry["value"] = float("nan")
    with pytest.raises((AssertionError, ValueError)):
        check_provenance(entry)


@pytest.mark.parametrize("eligible", [
    [],
    ["unknown_equipment"],
    ["dry_20"],
    ["flat_rack_high_cube_40", "flat_rack_high_cube_40"],
])
def test_oog_contract_rejects_mismatched_eligibility(cargo, eligible):
    invalid = deepcopy(cargo)
    invalid["oog"]["eligible_equipment_ids"] = eligible
    with pytest.raises(AssertionError):
        test_special_stowage_and_oog(invalid)
    with pytest.raises(AssertionError):
        test_sampling_policies_and_unconditioned_event_marginals(invalid)


@pytest.mark.parametrize("return_to_origin", [False, True])
def test_pod_contract_rejects_origin_even_when_repeated_later(cargo, return_to_origin):
    invalid = deepcopy(cargo)
    rotation = invalid["rotations"][0]
    rotation["pods"][0]["port"] = rotation["port_calls"][0]
    if not return_to_origin:
        rotation["port_calls"].pop()
    with pytest.raises(AssertionError):
        test_rotations_pod_shares_and_weight_dependence(invalid)


def test_pod_contract_allows_repeated_future_destination(cargo):
    valid = deepcopy(cargo)
    rotation = valid["rotations"][0]
    rotation["port_calls"].insert(-1, rotation["pods"][0]["port"])
    test_rotations_pod_shares_and_weight_dependence(valid)


def test_benchmark_summary_keeps_weight_labels_unit_neutral(capsys):
    script = CARGO.parents[1] / "experiments/cargo-params/summarize_benchmark.py"
    runpy.run_path(str(script), run_name="__main__")
    summary = json.loads(capsys.readouterr().out)
    assert "weight_class" in summary["counts"]
    assert "weight_class" in summary["shares"]
    assert "weight_class_t" not in summary["counts"]
    assert "unit is unverified" in summary["note"]
    assert sum(summary["counts"]["weight_class"].values()) == summary["boxes"]
