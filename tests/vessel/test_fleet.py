"""Feeder instances preserve the geometry and provenance of audited inputs."""

import hashlib
import sys
import tomllib
from types import ModuleType

import pytest

from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import Vessel
from stowage.model.fixtures import build_instance
from stowage.params import params_dir
from stowage.vessel.fleet import list_fleet, load_ship_params, load_vessel
from stowage.vessel.fleet_slices import SLICE_PRESETS, slice_preset


@pytest.mark.parametrize("name,expected", [("feeder_500", 516), ("feeder_800", 816), ("feeder_1700", 1604)])
def test_feeder_capacity_and_frozen_model(name, expected):
    vessel = load_vessel(name, with_hull=False)
    params = load_ship_params(name)
    assert len(vessel.cells) == expected
    assert abs(expected - vessel.nominal_teu) <= vessel.nominal_teu * params["teu_tolerance_fraction"]
    if name == "feeder_800":
        assert vessel.nominal_teu == 800
        assert abs(expected - vessel.nominal_teu) / vessel.nominal_teu <= 0.02
    assert vessel.hull is None
    assert vessel.tanks == []
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel
    assert vessel.bridge is not None
    assert vessel.heat_sources
    digest = hashlib.sha256((params_dir("fleet") / f"{name}.toml").read_bytes()).hexdigest()
    assert f"params/fleet/{name}.toml sha256:{digest}" in vessel.sources


@pytest.mark.parametrize("name", list_fleet())
def test_feeder_maps_every_model_field_and_hatch_member(name):
    vessel = load_vessel(name, with_hull=False)
    params = load_ship_params(name)
    for field in ("cells", "stacks", "hatch_covers", "heat_sources"):
        records = {record["id"]: record for record in params[field]}
        for item in getattr(vessel, field):
            for key, value in item.model_dump().items():
                if key in records[item.id]:
                    expected = records[item.id][key]
                    if field == "heat_sources" and key == "tank_id":
                        # Geometry-only builds omit tanks and their references;
                        # full-builder links are checked in the model contract suite.
                        expected = None
                    if key in {"bays", "hold_stack_ids", "deck_stack_ids", "dg_classes_allowed"} and expected is not None:
                        expected = sorted(expected)
                    assert value == expected
    cells = {cell.id: cell for cell in vessel.cells}
    stacks = {stack.id: stack for stack in vessel.stacks}
    assert all(parse_slot(cid).is_20ft_bay for cid in cells)
    assert sum(cell.reefer_plug for cell in vessel.cells) == params["layout"]["reefer_plugs"]
    for slot in params["layout"]["slots_45"]:
        assert all(cells[cid].accepts_45 for cid in cells_of_slot(slot))
    for cover in vessel.hatch_covers:
        for sid in cover.hold_stack_ids + cover.deck_stack_ids:
            assert stacks[sid].hatch_cover_id == cover.id
    assert load_ship_params(name)["cells"][0]["accepts_high_cube"] is True
    assert "hull_form" in params and "envelope" in params


@pytest.mark.parametrize("name,deck_tiers", [("feeder_500", 5), ("feeder_800", 5), ("feeder_1700", 6)])
def test_restored_deck_tiers_and_hatch_cover_depth(name, deck_tiers):
    # The convergence layout has a 0.6 m pontoon above a 0.6 m coaming;
    # its centre of mass and the stack floor are different elevations.
    vessel = load_vessel(name, with_hull=False)
    params = load_ship_params(name)
    depth = params["hull"]["depth_m"]
    cells = {cell.id: cell for cell in vessel.cells}
    coamings = {record["id"]: record for record in params["envelope"]["hatch_coamings"]}
    silhouettes = {record["id"]: record for record in params["windage"]["projected_rectangles"]}
    for cover in vessel.hatch_covers:
        assert cover.vcg_m == pytest.approx(depth + 0.9)
        coaming = coamings[cover.id]
        assert coaming["z_min_m"] == pytest.approx(depth)
        assert coaming["z_max_m"] == pytest.approx(depth + 0.6)
        assert cover.lcg_m == pytest.approx((coaming["x_min_m"] + coaming["x_max_m"]) / 2)
        assert cover.tcg_m == pytest.approx((coaming["y_min_m"] + coaming["y_max_m"]) / 2)
        assert silhouettes[cover.id]["z_max_m"] == pytest.approx(depth + 1.2)
    for stack in vessel.stacks:
        if stack.on_deck:
            assert len(stack.cell_ids) == deck_tiers
            assert stack.base_z_m == pytest.approx(depth + 1.2)
            assert cells[stack.cell_ids[0]].vcg_base_m == pytest.approx(stack.base_z_m)
    if name == "feeder_500":
        assert params["hull"]["design_draught_m"] == 7.05


def test_fleet_names_and_invalid_names():
    assert list_fleet() == ["feeder_1700", "feeder_500", "feeder_800"]
    for name in ("../feeder_500", "missing", "feeder_500.toml"):
        with pytest.raises(ValueError, match="unknown vessel"):
            load_vessel(name, with_hull=False)
    params = load_ship_params("feeder_500")
    params["cells"].clear()
    assert load_ship_params("feeder_500")["cells"]


@pytest.mark.parametrize("name", list_fleet())
def test_document_of_compliance_classes_and_input_metadata(name):
    vessel = load_vessel(name, with_hull=False)
    params = load_ship_params(name)
    raw = tomllib.loads((params_dir("fleet") / f"{name}.toml").read_text())
    assert params["dg_document"] == raw["dg_document"]
    assert params["envelope"]["openings"] == raw["envelope"]["openings"]
    areas = {area["id"]: area for area in params["dg_document"]["areas"]}
    model_areas = {area.id: area for area in vessel.dg_doc_areas}
    assert model_areas.keys() == areas.keys()
    document = params["dg_document"]
    for area_id, raw_area in areas.items():
        area = model_areas[area_id]
        for field in ("stack_ids", "classes_allowed", "packing_forms_allowed"):
            assert getattr(area, field) == sorted(raw_area[field])
        for field in ("kind", "hold_id", "even_bay", "equipment_assumptions",
                      "ventilation_air_changes_per_hour", "assumption"):
            assert getattr(area, field) == raw_area.get(field)
        # The synthetic area premise and public basis survive JSON and slicing.
        assert document["source"]["url"] in area.sources
        assert set(document["guidance_urls"]) <= set(area.sources)
        assert document["assumption"] in area.sources
        assert raw_area["assumption"] in area.sources
    records = {record["id"]: record for record in params["stacks"]}
    for stack in vessel.stacks:
        record = records[stack.id]
        area = areas[record["dg_doc_area_id"]]
        assert stack.dg_doc_area_id == area["id"]
        assert stack.id in area["stack_ids"]
        assert stack.id in model_areas[stack.dg_doc_area_id].stack_ids
        assert stack.dg_classes_allowed == sorted(set(area["classes_allowed"]))
        assert stack.dg_classes_allowed == sorted(set(record["dg_classes_allowed"]))
        assert area["packing_forms_allowed"]
        assert area["equipment_assumptions"]
    by_id = {stack.id: stack for stack in vessel.stacks}
    for size in SLICE_PRESETS:
        sliced = slice_preset(vessel, size)
        assert all(stack.dg_classes_allowed == by_id[stack.id].dg_classes_allowed
                   for stack in sliced.stacks)
        assert all(stack.dg_doc_area_id == by_id[stack.id].dg_doc_area_id
                   for stack in sliced.stacks)


def test_full_loader_delegates_complete_inputs(monkeypatch):
    # Fixture hydrostatics isolate assembly wiring from expensive production physics.
    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")
    from stowage.checker import strength_limits

    expected_params = load_ship_params("feeder_500")
    fixture_hull = build_instance().vessel.hull
    stations = [station.model_copy(deep=True) for station in fixture_hull.stations]
    stations[1].max_shear_n *= 2
    strength_calls = []

    def permissible(params, hull):
        assert params is seen[0]
        assert hull is fixture_hull
        strength_calls.append((params, hull))
        return stations

    monkeypatch.setattr(strength_limits, "permissible_stations", permissible)
    hull_module, tank_module = ModuleType("stowage.vessel.hull"), ModuleType("stowage.vessel.tanks")
    seen = []

    def hull_builder(params):
        seen.append(params)
        assert params["hull_form"] and params["envelope"]
        assert params["envelope"]["openings"] == expected_params["envelope"]["openings"]
        assert all({"x_m", "y_m", "z_m", "closure", "progressive_flooding"} <= opening.keys()
                   for opening in params["envelope"]["openings"])
        lightship_fields = {key: value for key, value in expected_params["hull"].items()
                            if key.startswith("lightship_")}
        assert {key: params["hull"][key] for key in lightship_fields} == lightship_fields
        assert {"lightship_mass_kg", "lightship_lcg_m", "lightship_tcg_m", "lightship_vcg_m",
                "lightship_distribution"} <= lightship_fields.keys()
        assert params["dg_document"] == expected_params["dg_document"]
        assert params["stacks"] == expected_params["stacks"]
        return fixture_hull

    def tank_builder(params):
        assert params is seen[0]
        assert params["tanks"][0]["geometry"]
        return []

    hull_module.build_hull_model = hull_builder
    tank_module.build_tanks = tank_builder
    monkeypatch.setitem(sys.modules, hull_module.__name__, hull_module)
    monkeypatch.setitem(sys.modules, tank_module.__name__, tank_module)
    vessel = load_vessel("feeder_500")
    assert vessel.nominal_teu == 500
    assert vessel.hull.stations == stations
    assert len(seen) == len(strength_calls) == 1
