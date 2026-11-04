"""Contract checks for synthetic fleet inputs; no calculated class limits are invented here."""

from copy import deepcopy
from datetime import date
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import re
import tomllib
from urllib.parse import urlparse

import pytest
import numpy as np

from stowage.conventions import cells_of_slot, parse_slot, stack_id
from stowage.vessel.hull_offsets import read_offsets
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.tanks import TankGeometry


FLEET = Path(__file__).resolve().parents[2] / "params" / "fleet"
RAW = FLEET.parents[1] / "data" / "raw"

# Independent source inventory, not inferred from mutable parameter provenance.
# Brochure dimensions: PDF p1; listing: Vessel's Specification; Hamburg: Draught.
# Review-corrected synthetic depth (500), synthetic Lpp (1700), and estimated
# lightship are deliberately not misrepresented as directly sourced dimensions.
SOURCED_HULL_DIMENSIONS = {
    "feeder_500": {
        "loa_m": ("reference-ships/arctic-trader.txt", "LOA", "arctic-trader.pdf"),
        "lpp_m": ("reference-ships/arctic-trader.txt", "Lpp", "arctic-trader.pdf"),
        "beam_m": ("reference-ships/arctic-trader.txt", "Breadth (moulded)", "arctic-trader.pdf"),
        "scantling_draught_m": ("hafen-hamburg/bf-philipp.html", "Draught", "Port of Hamburg BF PHILIPP IMO9123324"),
    },
    "feeder_800": {
        "loa_m": ("reference-ships/dina-trader.txt", "LOA", "dina-trader.pdf"),
        "lpp_m": ("reference-ships/dina-trader.txt", "Lpp", "dina-trader.pdf"),
        "beam_m": ("reference-ships/dina-trader.txt", "Breadth (moulded)", "dina-trader.pdf"),
        "depth_m": ("reference-ships/dina-trader.txt", "Depth (moulded)", "dina-trader.pdf"),
        "scantling_draught_m": ("reference-ships/dina-trader.txt", "Draft (max)", "dina-trader.pdf"),
    },
    "feeder_1700": {
        "loa_m": ("reference-ships/bangkokmax-1700.html", "Loa", "bangkokmax-1700.html"),
        "beam_m": ("reference-ships/bangkokmax-1700.html", "Breadth", "bangkokmax-1700.html"),
        "depth_m": ("reference-ships/bangkokmax-1700.html", "Depth", "bangkokmax-1700.html"),
        "scantling_draught_m": ("reference-ships/bangkokmax-1700.html", "Draft", "bangkokmax-1700.html"),
    },
}
SOURCE_URLS = {
    "arctic-trader.pdf": "https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf",
    "dina-trader.pdf": "https://mmred.no/images/Dina_Trader_Brochure-2022.pdf",
    "bangkokmax-1700.html": "https://www.eshiptrading.com/SS94778.html",
    "Port of Hamburg BF PHILIPP IMO9123324": "https://www.hafen-hamburg.de/en/vessels/bf-philipp/",
}


def _source_metres(text, label):
    """Read an explicitly metre-valued fact; never silently convert an unknown unit."""
    text = " ".join(re.sub(r"<[^>]*>", " ", text).split())
    match = re.search(r"\b" + re.escape(label) + r"\s*[:：]?\s*(\d+(?:[.,]\d+)?)\s*([a-z]+)\b", text, re.I)
    assert match is not None, label
    assert match[2].lower() == "m", (label, match[2])
    return float(match[1].replace(",", "."))


def _check_sourced_hull_dimensions(ship):
    for field, (relative_path, label, document) in SOURCED_HULL_DIMENSIONS[ship["id"]].items():
        hull = ship["hull"]
        assert field in hull.get("provenance", {}), (ship["id"], field, "missing per-field provenance")
        source = hull["provenance"][field].get("source", {})
        assert source.get("document") == document, (ship["id"], field, "source document")
        assert source.get("url") == SOURCE_URLS[document], (ship["id"], field, "source URL")
        if relative_path.endswith(".txt"):
            assert source.get("section") == "PDF p1 Dimensions", (ship["id"], field, "source page")
        expected = _source_metres((RAW / relative_path).read_text(), label)
        # The public parameter contract uses metres for *_m, as does the source.
        assert field.endswith("_m")
        assert hull[field] == pytest.approx(expected, rel=0, abs=1e-10), (ship["id"], field)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_numeric_provenance(node, inherited=None, path="root"):
    """A table's provenance applies to its scalar numbers and descendant tables."""
    if isinstance(node, dict):
        if node.get("id") in SOURCED_HULL_DIMENSIONS:
            _check_sourced_hull_dimensions(node)
        label = inherited
        if "assumption" in node:
            assert isinstance(node["assumption"], str) and node["assumption"].strip(), path
            label = node["assumption"]
        if "source" in node:
            source = node["source"]
            assert isinstance(source, dict), path
            for key in ("document", "section", "url", "read_date"):
                assert isinstance(source.get(key), str) and source[key].strip(), (path, key)
            parsed = urlparse(source["url"])
            assert parsed.scheme in {"http", "https"} and parsed.netloc, path
            assert date.fromisoformat(source["read_date"]).isoformat() == source["read_date"], path
            label = source
        for key, value in node.items():
            if key not in {"source", "assumption"}:
                check_numeric_provenance(value, label, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            check_numeric_provenance(value, inherited, f"{path}[{index}]")
    elif _is_number(node):
        assert inherited, f"Undocumented numeric value at {path}"
        assert math.isfinite(node), f"Non-finite numeric value at {path}"


def _unique_by_id(entries):
    indexed = {entry["id"]: entry for entry in entries}
    assert len(indexed) == len(entries), "Duplicate identifiers"
    return indexed


def test_exactly_three_fleet_parameter_files():
    assert len(list(FLEET.glob("*.toml"))) == 3


@pytest.mark.parametrize("value", [1, 0.5, [1, 2], {"nested": 3}])
def test_numeric_provenance_rejects_unlabelled_values(value):
    with pytest.raises(AssertionError, match="Undocumented numeric value"):
        check_numeric_provenance(value)


def test_numeric_provenance_inherits_assumptions_and_excludes_booleans():
    check_numeric_provenance({"assumption": "Synthetic geometry for tests.", "children": [{"x": [0, 1]}]})
    check_numeric_provenance({"enabled": True})


@lru_cache(maxsize=1)
def _load_fleet():
    return [tomllib.loads(path.read_text()) for path in sorted(FLEET.glob("*.toml"))]


@pytest.mark.parametrize("mutation", ["missing_loa_provenance", "changed_loa", "wrong_source"])
def test_sourced_anchor_cannot_hide_behind_inherited_assumption(mutation):
    ship = deepcopy(next(ship for ship in _load_fleet() if ship["id"] == "feeder_800"))
    if mutation == "missing_loa_provenance":
        del ship["hull"]["provenance"]["loa_m"]
    elif mutation == "changed_loa":
        ship["hull"]["loa_m"] += 1
    else:
        ship["hull"]["provenance"]["loa_m"]["source"]["document"] = "arctic-trader.pdf"
    with pytest.raises(AssertionError):
        check_numeric_provenance(ship)


@pytest.mark.parametrize("ship_id,field", [
    (ship_id, field)
    for ship_id, fields in SOURCED_HULL_DIMENSIONS.items()
    for field in fields
])
@pytest.mark.parametrize("mutation", ["missing_provenance", "changed_value", "wrong_source", "wrong_url"])
def test_every_sourced_hull_dimension_is_bound_to_its_source(ship_id, field, mutation):
    ship = deepcopy(next(ship for ship in _load_fleet() if ship["id"] == ship_id))
    if mutation == "missing_provenance":
        del ship["hull"]["provenance"][field]
    elif mutation == "changed_value":
        ship["hull"][field] *= 100  # centimetres accidentally supplied as metres
    else:
        key = "document" if mutation == "wrong_source" else "url"
        ship["hull"]["provenance"][field]["source"][key] = "https://example.org/unrelated"
    with pytest.raises(AssertionError):
        check_numeric_provenance(ship)


@pytest.mark.parametrize("unit", ["cm", "mm", "ft"])
def test_sourced_hull_dimension_unit_cannot_be_assumed(unit):
    with pytest.raises(AssertionError):
        _source_metres(f"LOA 134.44 {unit}", "LOA")


def test_middle_feeder_has_independently_bounded_800_teu_class():
    # D33 approves relabelling the unchanged 816-cell geometry, not an
    # input-defined acceptance bound that could grow with a mistaken label.
    ship = next(ship for ship in _load_fleet() if ship["id"] == "feeder_800")
    assert ship["nominal_teu"] == 800
    assert len(ship["cells"]) == 816
    assert ship["teu_tolerance_fraction"] == pytest.approx(0.02)
    assert abs(len(ship["cells"]) - 800) / 800 <= 0.02


def test_fleet_geometry_contract():
    ships = _load_fleet()
    assert sorted(ship["nominal_teu"] for ship in ships) == [500, 800, 1700]
    for ship in ships:
        check_numeric_provenance(ship)
        for section in ("cells", "stacks", "hatch_covers", "tanks", "heat_sources", "bridge", "hull", "hull_form", "envelope", "layout"):
            assert ship[section], (ship["id"], section)
        cells = _unique_by_id(ship["cells"])
        stacks = _unique_by_id(ship["stacks"])
        covers = _unique_by_id(ship["hatch_covers"])
        assert 0 <= ship["teu_tolerance_fraction"] < 1
        assert abs(len(cells) - ship["nominal_teu"]) <= ship["nominal_teu"] * ship["teu_tolerance_fraction"]
        layout = ship["layout"]
        deck_counts = {500: 360, 800: 560, 1700: 1032}
        assert sum(parse_slot(cell_id).on_deck for cell_id in cells) == deck_counts[ship["nominal_teu"]]
        assert len(cells) == {500: 516, 800: 816, 1700: 1604}[ship["nominal_teu"]]
        assert sum(not parse_slot(cid).on_deck for cid in cells) == {500: 156, 800: 256, 1700: 572}[ship["nominal_teu"]]
        audit = layout["kcs_fit_audit"]
        assert audit["retained_cell_count"] == len(cells)
        assert audit["original_cell_count"] - audit["removed_cell_count"] == len(cells)
        assert sum(not parse_slot(cid).on_deck for cid in cells) / len(cells) >= 0.28
        assert audit["deck_tiers_removed"] == 0
        assert all(cell_id not in cells for stack in audit["removed_stacks"] for cell_id in stack["cell_ids"])
        assert layout["high_cube_height_m"] > layout["standard_height_m"] > 0
        assert layout["hatch_coaming_height_m"] == 0.6
        assert layout["hatch_cover_depth_m"] == 0.6
        assert layout["hold_cover_clearance_m"] == 0.05
        assert sum(cell["reefer_plug"] for cell in cells.values()) == layout["reefer_plugs"]
        addressed = []
        for stack in stacks.values():
            assert stack["id"] == stack_id(stack["bay"], stack["row"], stack["on_deck"])
            ids = stack["cell_ids"]
            assert ids and len(ids) == len(set(ids))
            assert ids == sorted(ids, key=lambda cell_id: parse_slot(cell_id).tier)
            assert stack["max_mass_kg"] > 0
            assert stack["max_mass_20_kg"] > 0 and stack["max_mass_40_kg"] > 0
            assert stack["max_height_m"] >= len(ids) * layout["standard_height_m"] - 1e-8
            if stack["on_deck"]:
                assert len(ids) == {500: 5, 800: 5, 1700: 6}[ship["nominal_teu"]]
                assert math.isclose(stack["base_z_m"], ship["hull"]["depth_m"] + 1.2, abs_tol=1e-9)
            else:
                assert math.isclose(stack["max_height_m"], ship["hull"]["depth_m"] + 0.6 - 0.05 - stack["base_z_m"], abs_tol=1e-9)
            for index, cell_id in enumerate(ids):
                cell = cells[cell_id]
                address = parse_slot(cell_id)
                assert address.is_20ft_bay
                assert cell["stack_id"] == stack["id"]
                assert (address.bay, address.row, address.on_deck) == (stack["bay"], stack["row"], stack["on_deck"])
                assert math.isclose(cell["vcg_base_m"], stack["base_z_m"] + index * layout["standard_height_m"], abs_tol=1e-8)
                assert math.isclose(cell["vcg_m"], cell["vcg_base_m"] + layout["standard_height_m"] / 2, abs_tol=1e-8)
                assert abs(cell["tcg_m"]) < ship["hull"]["beam_m"] / 2
                if address.row:
                    assert (cell["tcg_m"] > 0) == (address.row % 2 == 0)
                else:
                    assert cell["tcg_m"] == 0
                addressed.append(cell_id)
            cover_id = stack.get("hatch_cover_id")
            if not stack["on_deck"]:
                assert cover_id in covers
            if cover_id:
                key = "deck_stack_ids" if stack["on_deck"] else "hold_stack_ids"
                assert stack["id"] in covers[cover_id][key]
        assert sorted(addressed) == sorted(cells), "Every physical cell belongs to exactly one stack"
        for cover in covers.values():
            assert cover["mass_kg"] > 0
            assert math.isclose(cover["vcg_m"], ship["hull"]["depth_m"] + 0.9, abs_tol=1e-9)
            linked_cells = []
            for key in ("hold_stack_ids", "deck_stack_ids"):
                assert len(cover[key]) == len(set(cover[key]))
                if key == "deck_stack_ids":
                    assert cover[key]
                for stack_id_ in cover[key]:
                    stack = stacks[stack_id_]
                    assert stack["hatch_cover_id"] == cover["id"]
                    assert stack["on_deck"] == (key == "deck_stack_ids")
                    linked_cells.extend(stack["cell_ids"])
            assert set(cover["cell_ids"]) <= cells.keys()
            assert sorted(cover["cell_ids"]) == sorted(linked_cells)
        for pair in layout["bay_pairs"]:
            assert pair["even_bay"] % 2 == 0
            assert pair["odd_bays"] == [pair["even_bay"] - 1, pair["even_bay"] + 1]
            first = [cell for cell in cells.values() if parse_slot(cell["id"]).bay == pair["odd_bays"][0]]
            for cell in first:
                address = parse_slot(cell["id"])
                partners = cells_of_slot(f"{pair['even_bay']:03d}{address.row:02d}{address.tier:02d}")
                assert all(partner in cells for partner in partners)
                assert all(cells[partner]["accepts_40"] for partner in partners)
                if pair["accepts_45"]:
                    assert all(cells[partner]["accepts_45"] for partner in partners)
        assert ship["bridge"]["eye_z_m"] > ship["hull"]["depth_m"]
        assert ship["bridge"]["bow_x_m"] > ship["bridge"]["x_m"]


def test_hull_mass_and_tank_contract():
    for ship in _load_fleet():
        hull = ship["hull"]
        for key in ("loa_m", "lpp_m", "beam_m", "depth_m", "design_draught_m", "scantling_draught_m", "lightship_mass_kg", "lightship_lcg_m", "lightship_vcg_m"):
            assert hull[key] > 0, (ship["id"], key)
        assert hull["loa_m"] >= hull["lpp_m"]
        assert hull["depth_m"] > hull["scantling_draught_m"] >= hull["design_draught_m"]
        distribution = hull["lightship_distribution"]
        assert distribution
        assert math.isclose(sum(block[2] for block in distribution), hull["lightship_mass_kg"], rel_tol=1e-9)
        longitudinal_moment = 0
        for aft, fore, mass in distribution:
            assert fore > aft and mass > 0
            longitudinal_moment += mass * (aft + fore) / 2
        assert math.isclose(longitudinal_moment / hull["lightship_mass_kg"], hull["lightship_lcg_m"], abs_tol=1e-8)
        assert ship["envelope"]["deck_z_m"] == hull["depth_m"]
        assert ship["envelope"]["count_deckhouse"] is False
        # Class-computed SF/BM and GM limits belong to later physics tasks.
        assert "min_gm_m" not in hull
        assert not hull.get("stations")
        assert not hull.get("hydrostatics")
        assert not hull.get("cross_curves")
        tanks = _unique_by_id(ship["tanks"])
        heat = _unique_by_id(ship["heat_sources"])
        assert {tank["kind"] for tank in tanks.values()} >= {"ballast", "fuel_oil", "fresh_water"}
        assert any(source["kind"] == "engine_room" for source in heat.values())
        for source in heat.values():
            for axis in "xyz":
                assert source[f"{axis}_max_m"] > source[f"{axis}_min_m"]
        for tank in tanks.values():
            geometry = tank["geometry"]
            assert geometry["shape"] in {"rectangular_prism", "hull_sections"}
            assert tank["x_aft_m"] == geometry["x_min_m"]
            assert tank["x_fore_m"] == geometry["x_max_m"]
            assert tank["content_density_kg_m3"] > 0
            table = tank["table"]
            assert len(table) >= 3 and table[0]["volume_m3"] == 0
            assert math.isclose(table[-1]["volume_m3"], tank["capacity_m3"], rel_tol=1e-9)
            assert all(a["volume_m3"] < b["volume_m3"] for a, b in zip(table, table[1:]))
            assert table[0]["fsm_m4"] == table[-1]["fsm_m4"] == 0
            if geometry["shape"] == "rectangular_prism":
                lengths = {axis: geometry[f"{axis}_max_m"] - geometry[f"{axis}_min_m"] for axis in "xyz"}
                assert math.isclose(tank["capacity_m3"], math.prod(lengths.values()), rel_tol=1e-9)
            else:
                volume, moment = _section_tank_volume_and_moment(geometry)
                assert math.isclose(tank["capacity_m3"], volume, rel_tol=1e-9)
                for key, value in zip(("lcg_m", "tcg_m", "vcg_m"), moment / volume):
                    assert math.isclose(table[-1][key], value, abs_tol=1e-7)
            for point in table:
                for axis, key in zip("xyz", ("lcg_m", "tcg_m", "vcg_m")):
                    assert geometry[f"{axis}_min_m"] - 1e-7 <= point[key] <= geometry[f"{axis}_max_m"] + 1e-7
                assert point["fsm_m4"] >= 0
            if tank["heated"]:
                assert any(source["kind"] == "heated_tank" and all(
                    source[f"{axis}_min_m"] <= geometry[f"{axis}_min_m"] and source[f"{axis}_max_m"] >= geometry[f"{axis}_max_m"]
                    for axis in "xyz"
                ) for source in heat.values())


@pytest.mark.parametrize("mutation", ["empty_assumption", "bad_url", "missing_section", "bad_date", "not_finite"])
def test_numeric_provenance_rejects_invalid_labels(mutation):
    node = {"value": 1, "source": {
        "document": "Public reference", "section": "Section 1", "url": "https://example.org/reference", "read_date": "2026-10-27",
    }}
    if mutation == "empty_assumption":
        node = {"value": 1, "assumption": " "}
    elif mutation == "bad_url":
        node["source"]["url"] = "file:///private/data"
    elif mutation == "missing_section":
        del node["source"]["section"]
    elif mutation == "bad_date":
        node["source"]["read_date"] = "yesterday"
    else:
        node["value"] = float("inf")
    with pytest.raises((AssertionError, ValueError)):
        check_numeric_provenance(node)


KCS_SHA256 = "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"


@lru_cache(maxsize=4)
def _source_offsets(path, sha256):
    return read_offsets(path, expected_sha256=sha256, format="kcs_fix")


@lru_cache(maxsize=4096)
def _source_section(path, sha256, x):
    """Independent arbitrary-x loft using source arc lengths, before target scaling."""
    table = _source_offsets(path, sha256)
    stations = table.stations
    positions = [station.x_m for station in stations]
    assert positions[0] <= x <= positions[-1]
    index = min(len(stations) - 2, max(0, int(np.searchsorted(positions, x)) - 1))
    left, right = stations[index:index + 2]
    contours = [station.half_section_yz_m for station in (left, right)]
    parameters = []
    for contour in contours:
        distances = np.r_[0, np.cumsum(np.linalg.norm(np.diff(contour, axis=0), axis=1))]
        parameters.append(distances / distances[-1])
    knots = np.unique(np.concatenate(parameters))
    paired = [np.column_stack([np.interp(knots, params, contour[:, axis]) for axis in (0, 1)]) for contour, params in zip(contours, parameters)]
    fraction = (x - left.x_m) / (right.x_m - left.x_m)
    half = paired[0] * (1 - fraction) + paired[1] * fraction
    return np.vstack((half, half[::-1] * [-1, 1]))


def _contains_yz(polygon, y, z):
    """Vectorized ray crossing and boundary check for a nonconvex section."""
    left, right = polygon, np.roll(polygon, -1, axis=0)
    dy, dz = (right - left).T
    lengths = np.hypot(dy, dz)
    boundary = (lengths > 0) & (np.abs(dy * (z - left[:, 1]) - dz * (y - left[:, 0])) <= 1e-8 * lengths)
    boundary &= (np.minimum(left[:, 0], right[:, 0]) - 1e-8 <= y) & (y <= np.maximum(left[:, 0], right[:, 0]) + 1e-8)
    boundary &= (np.minimum(left[:, 1], right[:, 1]) - 1e-8 <= z) & (z <= np.maximum(left[:, 1], right[:, 1]) + 1e-8)
    if np.any(boundary):
        return True
    crossing = (left[:, 1] > z) != (right[:, 1] > z)
    intersection = left[crossing, 0] + (z - left[crossing, 1]) * dy[crossing] / dz[crossing]
    return bool(np.count_nonzero(y < intersection) % 2)


def _assert_box_fits_offset_hull(ship, box, context):
    h, f = ship["hull"], ship["hull_form"]
    table = _source_offsets(f["offsets_path"], f["offsets_sha256"])
    x_values = [box["x_min_m"], box["x_max_m"]]
    x_values += [station.x_m * h["lpp_m"] / f["reference_lpp_m"] for station in table.stations if box["x_min_m"] < station.x_m * h["lpp_m"] / f["reference_lpp_m"] < box["x_max_m"]]
    for x in x_values:
        polygon = _source_section(f["offsets_path"], f["offsets_sha256"], x * f["reference_lpp_m"] / h["lpp_m"])
        # Inverse piecewise scaling preserves the exact source-loft contour.
        def source_z(z):
            if z <= h["design_draught_m"]:
                return z * f["reference_draught_m"] / h["design_draught_m"]
            return f["reference_draught_m"] + (z - h["design_draught_m"]) * (f["reference_depth_m"] - f["reference_draught_m"]) / (h["depth_m"] - h["design_draught_m"])
        low, high = source_z(box["z_min_m"]), source_z(box["z_max_m"])
        z_values = np.unique([low, high] + [float(z) for z in polygon[:, 1] if low < z < high])
        for y in (box["y_min_m"], box["y_max_m"]):
            for z in z_values:
                assert _contains_yz(polygon, y * f["reference_beam_m"] / h["beam_m"], z), (ship["id"], context, x, y, z)


def test_checksum_pinned_kcs_inputs_and_integrated_diagnostics():
    for ship in _load_fleet():
        hull, form = ship["hull"], ship["hull_form"]
        assert form["family"] == "stowlm_offsets_v1" and form["parent"] == "kcs"
        assert form["offsets_format"] == "kcs_fix"
        assert form["offsets_path"] == "data/raw/kcs/kcs.fix"
        assert form["offsets_sha256"] == KCS_SHA256
        assert form["source"] and form["assumption"].strip()
        assert form["provenance"]["reference_depth_m"]["assumption"].strip()
        assert hashlib.sha256((FLEET.parents[1] / form["offsets_path"]).read_bytes()).hexdigest() == KCS_SHA256
        for key, value in {"reference_lpp_m": 230, "reference_beam_m": 32.2, "reference_draught_m": 10.8, "reference_depth_m": 16}.items():
            assert form[key] == value
        check_numeric_provenance(form)
        for key in ("block_coefficient", "midship_coefficient", "prismatic_coefficient", "waterplane_coefficient", "lcb_m"):
            assert key not in hull, "Derived coefficients must not generate geometry"
        derived = ship["derived_hydrostatics"]
        geometry = HullGeometry.from_params(ship, nx=96, nz=128)
        assert geometry.source_sha256 == KCS_SHA256
        draught = hull["design_draught_m"]
        submerged = geometry.submerged(draught, draught)
        length, beam = hull["lpp_m"], hull["beam_m"]
        expected = {
            "displacement_volume_m3": submerged.volume_m3,
            "displacement_kg": submerged.displacement_kg,
            "block_coefficient": submerged.volume_m3 / (length * beam * draught),
            "midship_coefficient": max(submerged.sectional_areas_m2) / (beam * draught),
            "prismatic_coefficient": submerged.volume_m3 / (length * max(submerged.sectional_areas_m2)),
            "waterplane_coefficient": submerged.waterplane_area_m2 / (length * beam),
            "lcb_m": submerged.lcb_m,
            "implied_deadweight_kg": submerged.displacement_kg - hull["lightship_mass_kg"],
        }
        for key, value in expected.items():
            assert math.isclose(derived[key], value, rel_tol=1e-10, abs_tol=1e-9), (ship["id"], key)
        assert 0.64 < derived["block_coefficient"] < 0.66
        assert ship["envelope"]["buoyant_x_min_m"] == geometry.buoyant_x_min_m
        assert ship["envelope"]["buoyant_x_max_m"] == geometry.buoyant_x_max_m


def _intersects(a, b):
    return all(min(a[f"{axis}_max_m"], b[f"{axis}_max_m"]) - max(a[f"{axis}_min_m"], b[f"{axis}_min_m"]) > 1e-8 for axis in "xyz")


def _polygon_area_centroid(poly):
    poly = np.asarray(poly, dtype=float)
    cross = poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1]
    area = cross.sum() / 2
    assert area > 0
    centroid = ((poly + np.roll(poly, -1, axis=0)) * cross[:, None]).sum(axis=0) / (6 * area)
    return area, centroid


def _section_tank_volume_and_moment(geometry):
    xs, weights, sections = (geometry[key] for key in ("x_m", "integration_weights_m", "sections_yz_m"))
    assert len(xs) == len(weights) == len(sections) > 0
    assert all(a < b for a, b in zip(xs, xs[1:])) and all(w > 0 for w in weights)
    volume, moment = 0.0, np.zeros(3)
    for x, weight, polygons in zip(xs, weights, sections):
        assert polygons
        for poly in polygons:
            area, yz = _polygon_area_centroid(poly)
            volume += weight * area
            moment += weight * area * np.r_[x, yz]
    return volume, moment


def _clip_to_rectangle(poly, ymin, ymax, zmin, zmax):
    poly = np.asarray(poly, dtype=float)
    if len(poly) < 3 or min(ymax, poly[:, 0].max()) <= max(ymin, poly[:, 0].min()) + 1e-10 or min(zmax, poly[:, 1].max()) <= max(zmin, poly[:, 1].min()) + 1e-10:
        return 0.0
    points = [np.asarray(point, dtype=float) for point in poly]
    for axis, bound, keep_less in ((0, ymin, False), (0, ymax, True), (1, zmin, False), (1, zmax, True)):
        result = []
        for left, right in zip(points, points[1:] + points[:1]):
            li = left[axis] <= bound if keep_less else left[axis] >= bound
            ri = right[axis] <= bound if keep_less else right[axis] >= bound
            if li:
                result.append(left)
            if li != ri:
                result.append(left + (bound - left[axis]) / (right[axis] - left[axis]) * (right - left))
        points = result
        if not points:
            break
    if len(points) < 3:
        return 0.0
    arr = np.asarray(points)
    return abs(np.sum(arr[:, 0] * np.roll(arr[:, 1], -1) - np.roll(arr[:, 0], -1) * arr[:, 1])) / 2


def _points_fit_source_hull(ship, x, points):
    h, f = ship["hull"], ship["hull_form"]
    polygon = _source_section(f["offsets_path"], f["offsets_sha256"], x * f["reference_lpp_m"] / h["lpp_m"])
    points = np.asarray(points, dtype=float).copy()
    points[:, 0] *= f["reference_beam_m"] / h["beam_m"]
    z = points[:, 1].copy()
    points[:, 1] = np.where(z <= h["design_draught_m"], z * f["reference_draught_m"] / h["design_draught_m"], f["reference_draught_m"] + (z - h["design_draught_m"]) * (f["reference_depth_m"] - f["reference_draught_m"]) / (h["depth_m"] - h["design_draught_m"]))
    left, right = polygon, np.roll(polygon, -1, axis=0)
    dy, dz = (right - left).T
    for start in range(0, len(points), 64):
        yy, zz = points[start:start + 64].T[:, :, None]
        cross = dy * (zz - left[:, 1]) - dz * (yy - left[:, 0])
        boundary = np.abs(cross) <= 1e-8 * np.hypot(dy, dz)
        boundary &= (np.minimum(left[:, 0], right[:, 0]) - 1e-8 <= yy) & (yy <= np.maximum(left[:, 0], right[:, 0]) + 1e-8)
        boundary &= (np.minimum(left[:, 1], right[:, 1]) - 1e-8 <= zz) & (zz <= np.maximum(left[:, 1], right[:, 1]) + 1e-8)
        crossing = (left[:, 1] > zz) != (right[:, 1] > zz)
        intersections = left[:, 0] + (zz - left[:, 1]) * dy / np.where(dz == 0, 1, dz)
        inside = np.count_nonzero(crossing & (yy < intersections), axis=1) % 2 == 1
        assert np.all(inside | np.any(boundary, axis=1)), (ship["id"], x, "tank outside source hull")


def _polygon_intersection_area(left, right):
    """Exact horizontal sweep of two simple piecewise-linear section polygons."""
    left, right = np.asarray(left), np.asarray(right)
    low = max(left[:, 1].min(), right[:, 1].min())
    high = min(left[:, 1].max(), right[:, 1].max())
    if high <= low + 1e-10 or min(left[:, 0].max(), right[:, 0].max()) <= max(left[:, 0].min(), right[:, 0].min()) + 1e-10:
        return 0.0
    p, q = left, right
    r, t = np.roll(left, -1, axis=0) - left, np.roll(right, -1, axis=0) - right
    delta = q[None, :, :] - p[:, None, :]
    denominator = r[:, None, 0] * t[None, :, 1] - r[:, None, 1] * t[None, :, 0]
    safe = np.where(np.abs(denominator) > 1e-12, denominator, 1)
    u = (delta[:, :, 0] * t[None, :, 1] - delta[:, :, 1] * t[None, :, 0]) / safe
    v = (delta[:, :, 0] * r[:, None, 1] - delta[:, :, 1] * r[:, None, 0]) / safe
    crossing = (np.abs(denominator) > 1e-12) & (u > 0) & (u < 1) & (v > 0) & (v < 1)
    heights = p[:, None, 1] + u * r[:, None, 1]
    breaks = np.unique(np.r_[low, high, left[:, 1], right[:, 1], heights[crossing]])
    breaks = breaks[(breaks >= low) & (breaks <= high)]

    def intervals(poly, z):
        nxt = np.roll(poly, -1, axis=0)
        mask = (poly[:, 1] > z) != (nxt[:, 1] > z)
        hits = np.sort(poly[mask, 0] + (z - poly[mask, 1]) * (nxt[mask, 0] - poly[mask, 0]) / (nxt[mask, 1] - poly[mask, 1]))
        assert len(hits) % 2 == 0
        return list(zip(hits[::2], hits[1::2]))

    area = 0.0
    for bottom, top in zip(breaks, breaks[1:]):
        if top - bottom <= 1e-10:
            continue
        z = (bottom + top) / 2
        overlap = sum(max(0, min(b, d) - max(a, c)) for a, b in intervals(left, z) for c, d in intervals(right, z))
        area += overlap * (top - bottom)
    return float(area)


def _hold_cargo_envelopes(ship):
    """20/40ft footprints through every physically permitted stack height."""
    cells = _unique_by_id(ship["cells"])
    stacks = _unique_by_id(ship["stacks"])
    envelopes = []
    for stack in stacks.values():
        if stack["on_deck"]:
            continue
        cell = cells[stack["cell_ids"][0]]
        envelopes.append((cell["lcg_m"] - 3.029, cell["lcg_m"] + 3.029, cell["tcg_m"] - 1.219, cell["tcg_m"] + 1.219, stack["base_z_m"], stack["base_z_m"] + stack["max_height_m"]))
    for pair in ship["layout"]["bay_pairs"]:
        for stack in stacks.values():
            if stack["on_deck"] or stack["bay"] != pair["odd_bays"][0]:
                continue
            partner = stacks[f"{pair['odd_bays'][1]:03d}{stack['row']:02d}H"]
            cell = cells[stack["cell_ids"][0]]
            envelopes.append((pair["lcg_m"] - 6.096, pair["lcg_m"] + 6.096, cell["tcg_m"] - 1.219, cell["tcg_m"] + 1.219, min(stack["base_z_m"], partner["base_z_m"]), max(stack["base_z_m"] + stack["max_height_m"], partner["base_z_m"] + partner["max_height_m"])))
    return envelopes


def test_tanks_fit_hull_and_do_not_overlap_tanks_or_hold_cargo():
    for ship in _load_fleet():
        cargo = _hold_cargo_envelopes(ship)
        rooms = [source for source in ship["heat_sources"] if source["kind"] == "engine_room"]
        at_x = {}
        boxes = []
        for tank in ship["tanks"]:
            geometry = tank["geometry"]
            if geometry["shape"] == "rectangular_prism":
                _assert_box_fits_offset_hull(ship, geometry, tank["id"])
                boxes.append((tank["id"], geometry))
                continue
            for x, polygons in zip(geometry["x_m"], geometry["sections_yz_m"]):
                at_x.setdefault(x, []).extend((tank["id"], np.asarray(poly)) for poly in polygons)
                for poly in polygons:
                    _points_fit_source_hull(ship, x, poly)
                    for xmin, xmax, ymin, ymax, zmin, zmax in cargo:
                        if xmin < x < xmax:
                            assert _clip_to_rectangle(poly, ymin, ymax, zmin, zmax) < 1e-7, (ship["id"], tank["id"], x, "20/40ft cargo envelope overlap")
                    for room in rooms:
                        if room["x_min_m"] < x < room["x_max_m"]:
                            assert _clip_to_rectangle(poly, room["y_min_m"], room["y_max_m"], room["z_min_m"], room["z_max_m"]) < 1e-7, (ship["id"], tank["id"], x, "engine room overlap")
        for x, components in at_x.items():
            for tank_id, box in boxes:
                if box["x_min_m"] < x < box["x_max_m"]:
                    components.append((tank_id, np.array([[box["y_min_m"], box["z_min_m"]], [box["y_max_m"], box["z_min_m"]], [box["y_max_m"], box["z_max_m"]], [box["y_min_m"], box["z_max_m"]]])))
            for index, (left_id, left) in enumerate(components):
                for right_id, right in components[index + 1:]:
                    assert _polygon_intersection_area(left, right) < 1e-7, (ship["id"], left_id, right_id, x, "actual tank polygons overlap")


def test_explicit_45ft_slots_match_cell_capabilities():
    for ship in _load_fleet():
        cells = _unique_by_id(ship["cells"])
        slots = ship["layout"]["slots_45"]
        assert slots and len(slots) == len(set(slots))
        covered = set()
        for slot in slots:
            assert parse_slot(slot).bay % 2 == 0
            partners = cells_of_slot(slot)
            assert all(cell_id in cells and cells[cell_id]["accepts_45"] for cell_id in partners)
            covered.update(partners)
        assert covered == {cell["id"] for cell in cells.values() if cell["accepts_45"]}


def test_hold_cells_fit_watertight_hull_and_clear_engine_room():
    for ship in _load_fleet():
        stacks = _unique_by_id(ship["stacks"])
        rooms = [heat for heat in ship["heat_sources"] if heat["kind"] == "engine_room"]
        for cell in ship["cells"]:
            address = parse_slot(cell["id"])
            if address.on_deck:
                continue
            stack = stacks[cell["stack_id"]]
            tier_index = stack["cell_ids"].index(cell["id"])
            floor = stack["base_z_m"] + tier_index * ship["layout"]["standard_height_m"]
            cargo = {
                "x_min_m": cell["lcg_m"] - 6.058 / 2, "x_max_m": cell["lcg_m"] + 6.058 / 2,
                "y_min_m": cell["tcg_m"] - 2.438 / 2, "y_max_m": cell["tcg_m"] + 2.438 / 2,
                "z_min_m": floor, "z_max_m": floor + ship["layout"]["standard_height_m"],
            }
            assert cargo["z_max_m"] <= ship["hull"]["depth_m"] + 0.6 - 0.05
            if cargo["z_max_m"] > ship["hull"]["depth_m"]:
                assert any(all(cover[f"{axis}_min_m"] <= cargo[f"{axis}_min_m"] + 1e-8 and cover[f"{axis}_max_m"] >= cargo[f"{axis}_max_m"] - 1e-8 for axis in "xy") and cover["z_max_m"] >= cargo["z_max_m"] for cover in ship["envelope"]["hatch_coamings"])
            _assert_box_fits_offset_hull(ship, dict(cargo, z_max_m=min(cargo["z_max_m"], ship["hull"]["depth_m"])), cell["id"])
            assert all(not _intersects(cargo, room) for room in rooms), (ship["id"], cell["id"], "engine room")


def _projected_union_area_and_first_moment(rectangles):
    """Exact x-sweep of z-interval unions; overlapping silhouettes count once."""
    edges = sorted({rectangle[key] for rectangle in rectangles for key in ("x_min_m", "x_max_m")})
    area = first_moment = 0.0
    for left, right in zip(edges, edges[1:]):
        midpoint = (left + right) / 2
        intervals = sorted((rectangle["z_min_m"], rectangle["z_max_m"]) for rectangle in rectangles if rectangle["x_min_m"] < midpoint < rectangle["x_max_m"])
        merged = []
        for bottom, top in intervals:
            assert top > bottom
            if merged and bottom <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(top, merged[-1][1]))
            else:
                merged.append((bottom, top))
        area += (right - left) * sum(top - bottom for bottom, top in merged)
        first_moment += (right - left) * sum((top**2 - bottom**2) / 2 for bottom, top in merged)
    return area, first_moment


def test_operating_limits_are_labelled_and_match_design_envelope():
    keys = {"min_draught_fwd_m", "max_trim_m", "max_list_rad", "min_gm_m", "bilge_keel", "windage_area_m2", "windage_lever_m"}
    for ship in _load_fleet():
        limits = ship["operating_limits"]
        assert keys <= limits.keys()
        for key in keys:
            label = limits["provenance"].get(key, {"assumption": limits["assumption"]})
            assert isinstance(label, dict) and ({"assumption", "source"} & label.keys()), (ship["id"], key)
            check_numeric_provenance({"value": limits[key], **label}, path=f"{ship['id']}.operating_limits.{key}")
        hull = ship["hull"]
        assert math.isclose(limits["min_draught_fwd_m"], 0.35 * hull["design_draught_m"], rel_tol=1e-12)
        assert math.isclose(limits["max_trim_m"], 0.01 * hull["lpp_m"], rel_tol=1e-12)
        assert math.isclose(limits["max_list_rad"], math.radians(2), rel_tol=1e-12)
        assert limits["min_gm_m"] == 0.15
        assert limits["bilge_keel"] is False
        assert limits["windage_area_m2"] > 0 and limits["windage_lever_m"] > 0


def test_windage_is_union_projection_with_is_code_lever():
    for ship in _load_fleet():
        hull = ship["hull"]
        length, depth, draught = hull["lpp_m"], hull["depth_m"], hull["design_draught_m"]
        windage = ship["windage"]
        check_numeric_provenance(ship)
        expected_rectangles = [
            {"x_min_m": -6 * length / 230, "x_max_m": 236.9 * length / 230, "z_min_m": draught, "z_max_m": depth},
            {"x_min_m": 0.025 * length, "x_max_m": 0.11 * length, "z_min_m": depth, "z_max_m": depth + 19},
        ]
        expected_rectangles.extend(dict(coaming, z_max_m=depth + 1.2) for coaming in ship["envelope"]["hatch_coamings"])
        expected_rectangles.extend(source for source in ship["heat_sources"] if source["kind"] == "funnel")
        area, first_moment = _projected_union_area_and_first_moment(expected_rectangles)
        recorded_area, recorded_moment = _projected_union_area_and_first_moment(windage["projected_rectangles"])
        assert math.isclose(recorded_area, area, rel_tol=1e-10)
        assert math.isclose(recorded_moment, first_moment, rel_tol=1e-10)
        assert math.isclose(windage["area_m2"], area, rel_tol=1e-10)
        assert math.isclose(windage["first_moment_m3"], first_moment, rel_tol=1e-10)
        assert math.isclose(ship["operating_limits"]["windage_area_m2"], area, rel_tol=1e-10)
        assert math.isclose(windage["centroid_z_m"], first_moment / area, rel_tol=1e-10)
        assert windage["reference_waterline_m"] == draught
        assert windage["reference_underwater_centroid_z_m"] == draught / 2
        assert math.isclose(ship["operating_limits"]["windage_lever_m"], first_moment / area - draught / 2, rel_tol=1e-10)


def test_projected_union_ignores_duplicate_and_nested_rectangles():
    outer = {"x_min_m": 0, "x_max_m": 4, "z_min_m": 2, "z_max_m": 6}
    inner = {"x_min_m": 1, "x_max_m": 2, "z_min_m": 3, "z_max_m": 5}
    assert _projected_union_area_and_first_moment([outer, outer, inner]) == (16, 64)


def test_synthetic_dg_document_covers_every_stack_and_packing_form():
    valid_classes = {"1.4S", "2.1", "2.2", "2.3", "3", "4.1", "4.2", "4.3", "5.1", "5.2", "6.1", "6.2", "7", "8", "9"}
    excluded_hold_classes = {"1.4S", "2.1", "2.3", "5.2", "6.2", "7"}
    allowed_forms = {"packaged_goods_in_closed_freight_containers"}
    prohibited_forms = {"bulk", "portable_tank", "tank_container", "open_freight_container"}
    for ship in _load_fleet():
        document = ship["dg_document"]
        assert document["assumption"].strip() and document["source"]
        check_numeric_provenance(document)
        assert set(document["packing_forms_allowed"]) == allowed_forms
        assert set(document["packing_forms_prohibited"]) == prohibited_forms
        stacks = _unique_by_id(ship["stacks"])
        areas = _unique_by_id(document["areas"])
        addressed = []
        hold_class_sets = set()
        for area in areas.values():
            assert area["kind"] in {"deck", "hold"}
            assert area["assumption"].strip() and area["equipment_assumptions"]
            assert set(area["packing_forms_allowed"]) == allowed_forms
            classes = area["classes_allowed"]
            assert classes and len(classes) == len(set(classes))
            assert set(classes) <= valid_classes
            if area["kind"] == "hold":
                assert not (set(classes) & excluded_hold_classes)
                hold_class_sets.add(frozenset(classes))
            assert len(area["stack_ids"]) == len(set(area["stack_ids"]))
            for stack_id_ in area["stack_ids"]:
                stack = stacks[stack_id_]
                assert stack["on_deck"] == (area["kind"] == "deck")
                assert stack["dg_doc_area_id"] == area["id"]
                assert len(stack["dg_classes_allowed"]) == len(set(stack["dg_classes_allowed"]))
                assert set(stack["dg_classes_allowed"]) == set(classes)
                addressed.append(stack_id_)
        assert sorted(addressed) == sorted(stacks), "DoC areas must partition every hold and deck stack"
        assert len(hold_class_sets) >= 2, "The forward hold has a separately restricted DoC area"


def test_unprotected_openings_have_coordinates_and_clear_container_envelopes():
    for ship in _load_fleet():
        hull = ship["hull"]
        openings = _unique_by_id(ship["envelope"]["openings"])
        assert openings
        hold_areas = [area for area in ship["dg_document"]["areas"] if area["kind"] == "hold"]
        assert len(openings) == 2 * len(hold_areas) + 2
        assert sum(opening["kind"] == "hold_ventilator" for opening in openings.values()) == 2 * len(hold_areas)
        assert sum(opening["kind"] == "engine_room_air_inlet" for opening in openings.values()) == 2
        for area in hold_areas:
            assert all(f"{area['id']}-VENT-{side}" in openings for side in ("P", "S"))
        cells = _unique_by_id(ship["cells"])
        for opening in openings.values():
            assert opening["assumption"].strip()
            check_numeric_provenance(opening)
            assert opening["kind"]
            assert opening["closure"] == "not_weathertight_closable"
            assert opening["progressive_flooding"] is True
            assert opening["coordinate_reference"] == "lowest_unprotected_aperture_edge"
            assert 0 <= opening["x_m"] <= hull["lpp_m"]
            assert abs(opening["y_m"]) < hull["beam_m"] / 2
            assert opening["z_m"] > hull["depth_m"]
            # Full 45ft deck boxes are the widest longitudinal cargo envelope.
            for slot in ship["layout"]["slots_45"]:
                pair = [cells[cell_id] for cell_id in cells_of_slot(slot)]
                centre_x = sum(cell["lcg_m"] for cell in pair) / 2
                bottom = pair[0]["vcg_base_m"]
                inside = (
                    abs(opening["x_m"] - centre_x) < 13.716 / 2
                    and abs(opening["y_m"] - pair[0]["tcg_m"]) < 2.438 / 2
                    and bottom < opening["z_m"] < bottom + ship["layout"]["high_cube_height_m"]
                )
                assert not inside, (ship["id"], opening["id"], slot)


def test_lightship_review_design_values_and_distribution_are_consistent():
    for ship in _load_fleet():
        hull = ship["hull"]
        derivation = hull["lightship_derivation"]
        assert derivation["method"] == "physics_review_component_design_2026"
        assert derivation["assumption"].strip() and "UNVERIFIED" in derivation["assumption"]
        assert derivation["source"]
        assert derivation["confidence_mass"] == "M" and derivation["confidence_cg"] == "L"
        check_numeric_provenance(derivation)
        assert "AP" in derivation["coordinate_assumption"]
        assert "baseline" in derivation["coordinate_assumption"].lower()
        n = ship["nominal_teu"]
        mass, lcg, vcg = {500: (2900000, 49.6, 0.72 * 9.2), 800: (5200000, 57.2, 7.8), 1700: (8074000, 73.6, 9.24)}[n]
        assert derivation["vcg_uncertainty_m"] == {500: 0.5, 800: 0.6, 1700: 0.7}[n]
        for key, value in {"mass_kg": mass, "lcg_m": lcg, "vcg_m": vcg}.items():
            assert math.isclose(derivation[key], value, rel_tol=1e-12)
        if n == 500:
            assert hull["depth_m"] == 9.2
            assert hull["design_draught_m"] == hull["scantling_draught_m"] == 7.05
        expected = {"lightship_mass_kg": mass, "lightship_lcg_m": lcg, "lightship_vcg_m": vcg, "lightship_tcg_m": 0}
        for key, value in expected.items():
            assert math.isclose(hull[key], value, rel_tol=1e-12, abs_tol=1e-10), (ship["id"], key)
        distribution = hull["lightship_distribution"]
        distribution_label = hull["provenance"]["lightship_distribution"]["assumption"]
        assert "APPROXIMATION" in distribution_label
        assert "uniform" in distribution_label.lower()
        assert len(distribution) == 5
        assert distribution[0][0] == 0 and distribution[-1][1] == hull["lpp_m"]
        assert all(math.isclose(left[1], right[0], abs_tol=1e-10) for left, right in zip(distribution, distribution[1:]))
        total_mass = sum(block[2] for block in distribution)
        longitudinal_moment = sum((aft + fore) / 2 * mass for aft, fore, mass in distribution)
        assert math.isclose(total_mass, expected["lightship_mass_kg"], rel_tol=1e-12)
        assert math.isclose(longitudinal_moment / total_mass, expected["lightship_lcg_m"], abs_tol=1e-8)
        for key in ("lightship_mass_kg", "lightship_lcg_m", "lightship_vcg_m"):
            label = hull["provenance"][key]
            assert label["source"]["document"].strip()
            assert label["source"]["section"].strip()
            check_numeric_provenance({"value": hull[key], **label})


def test_review_ballast_and_hfo_capacity_targets_are_explicit():
    for ship in _load_fleet():
        design = ship["tank_design"]
        assert design["assumption"].startswith("APPROXIMATION")
        # D64 rev3 expands only the500 HEEL inner wall into verified free space;
        # larger ships repartition existing wing solids at unchanged total volume.
        expected_wb, expected_fo = {500: (3159.3350118278818, 554), 800: (4100, 790), 1700: (7000, 1500)}[ship["nominal_teu"]]
        for kind, expected, key in (("ballast", expected_wb, "ballast_capacity_m3"), ("fuel_oil", expected_fo, "hfo_capacity_m3")):
            total = sum(tank["capacity_m3"] for tank in ship["tanks"] if tank["kind"] == kind)
            assert math.isclose(total, expected, abs_tol=1e-4)
            assert math.isclose(design[key], total, rel_tol=1e-12)
            assert all(tank["geometry"]["shape"] == "hull_sections" for tank in ship["tanks"] if tank["kind"] == kind)
        for kind, expected, key, prefix in (("diesel_oil", {500: 103, 800: 115, 1700: 250}[ship["nominal_teu"]], "diesel_oil_capacity_m3", "DO"), ("fresh_water", {500: 110, 800: 120, 1700: 200}[ship["nominal_teu"]], "fresh_water_capacity_m3", "FW")):
            records = [tank for tank in ship["tanks"] if tank["kind"] == kind]
            assert {tank["id"] for tank in records} == {f"{prefix}-P", f"{prefix}-S"}
            assert all(tank["geometry"]["shape"] == "hull_sections" for tank in records)
            total = sum(tank["capacity_m3"] for tank in records)
            assert math.isclose(total, expected, abs_tol=1e-4)
            assert math.isclose(design[key], total, rel_tol=1e-12)
            for tank in records:
                label = tank["capacity_provenance"]
                assert label["confidence"] == ("L" if ship["nominal_teu"] == 1700 else "H")
                assert label["source"]
                assert label["aggregate_target_m3"] == expected


def test_forward_peak_and_deep_tanks_have_distinct_declared_extents():
    for ship in _load_fleet():
        tanks = _unique_by_id(ship["tanks"])
        length = ship["hull"]["lpp_m"]
        for prefix, aft, fore in (("FPT", 0.92, 0.97), ("FWD-DEEP", 0.88, 0.92)):
            for side in ("P", "S"):
                tank = tanks[f"{prefix}-{side}"]
                assert tank["kind"] == "ballast" and tank["adjustable"]
                assert math.isclose(tank["x_aft_m"], aft * length, abs_tol=1e-7)
                assert math.isclose(tank["x_fore_m"], fore * length, abs_tol=1e-7)


def test_production_tank_api_accepts_declared_finite_angle_geometry():
    for ship in _load_fleet():
        inclined_checked = False
        for tank in ship["tanks"]:
            physical = TankGeometry.from_params(tank)
            assert math.isclose(physical.capacity_m3, tank["capacity_m3"], rel_tol=1e-9)
            for declared in tank["table"]:
                actual = physical.sounding(declared["volume_m3"])
                for key in ("volume_m3", "lcg_m", "tcg_m", "vcg_m", "fsm_m4"):
                    assert math.isclose(getattr(actual, key), declared[key], rel_tol=1e-7, abs_tol=1e-7), (ship["id"], tank["id"], key)
            if tank["kind"] == "ballast" and not inclined_checked:
                target = 0.5 * physical.capacity_m3
                heel, trim = math.radians(30), 0.02
                liquid = physical.liquid(target, heel_rad=heel, trim_rad=trim)
                _, weights, areas = physical.section_liquid_areas(target, heel_rad=heel, trim_rad=trim)
                assert math.isclose(float(np.dot(weights, areas)), target, rel_tol=1e-8, abs_tol=1e-7)
                for axis, coordinate in zip("xyz", liquid.centroid_m):
                    assert tank["geometry"][f"{axis}_min_m"] - 1e-7 <= coordinate <= tank["geometry"][f"{axis}_max_m"] + 1e-7
                assert liquid.fsm_m4 >= 0
                inclined_checked = True
        assert inclined_checked


def test_approximate_type_b_screen_preserves_raw_failure_and_calibration():
    for ship in _load_fleet():
        record = ship["load_line"]
        assert record["confidence"] == "L" and "APPROXIMATION" in record["assumption"]
        assert "NOT conservative" in record["assumption"]
        check_numeric_provenance(record)
        hull = ship["hull"]
        length, depth, draught = hull["lpp_m"], hull["depth_m"], hull["scantling_draught_m"]
        feet = length / 0.3048
        rows = record["table"]["rows"]
        bracket = next((left, right) for left, right in zip(rows, rows[1:]) if left[0] <= feet <= right[0])
        (x0, y0), (x1, y1) = bracket
        tabular = (y0 + (y1 - y0) * (feet - x0) / (x1 - x0)) * 0.0254
        depth_correction = max(0, (depth - length / 15) * (length / 0.48 if length < 120 else 250)) / 1000
        aft = ((0.1 * feet + 10) + 3 * (0.0444 * feet + 4.44) + 3 * (0.0111 * feet + 1.11)) / 8
        fore = ((0.2 * feet + 20) + 3 * (0.0888 * feet + 8.88) + 3 * (0.0222 * feet + 2.22)) / 8
        sheer = (aft + fore) / 2 * 0.0254 * (0.75 - 0.2 / 2)
        full_deduction = 34 + 8 * (feet - 279) / 121 if feet < 400 else 42
        raw = tabular + depth_correction + sheer - full_deduction * 0.0254 * 0.1
        assert record["deck_stringer_thickness_m"] == 0.012
        assert record["method_constants"]["deck_stringer_thickness_m"]["assumption"].strip()
        assert math.isclose(record["moulded_depth_minus_draught_m"], depth - draught, abs_tol=1e-12)
        available = depth + 0.012 - draught
        assert math.isclose(record["raw_required_m"], raw, abs_tol=1e-10)
        assert math.isclose(record["available_freeboard_m"], available, abs_tol=1e-12)
        assert record["raw_screen_passed"] == (available >= raw)
        assert record["calibration_bias_m"] == 0.25
        assert math.isclose(record["observed_dina_bias_m"] + record["rounding_model_allowance_m"], 0.25, abs_tol=1e-12)
        approximate = max(0, raw - 0.25)
        assert math.isclose(record["approx_required_m"], approximate, abs_tol=1e-10)
        assert record["approx_screen_passed"] == (available >= approximate)
        assert record["approx_screen_passed"] is True
        if ship["nominal_teu"] in (500, 800):
            assert record["raw_screen_passed"] is False
        if ship["nominal_teu"] == 800:
            comparison = record["dina_assigned_comparison"]
            assert comparison["confidence"] == "L" and comparison["assumption"].strip()
            assert math.isclose(comparison["available_freeboard_m"], 2.612, abs_tol=1e-12)
            assert comparison["assigned_freeboard_m"] == 2.609
            assert math.isclose(comparison["available_minus_assigned_m"], 0.003, abs_tol=1e-12)
            assert comparison["assigned_screen_passed"] is True


@pytest.mark.parametrize("right,expected", [
    ([[2, 2], [3, 2], [3, 3], [2, 3]], 0.0),
    ([[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]], 7.0),
    ([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]], 0.36),
])
def test_section_intersection_sweep_handles_concavity_and_nesting(right, expected):
    left = np.array([[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]])
    assert math.isclose(_polygon_intersection_area(left, np.array(right)), expected, abs_tol=1e-10)


def _target_source_polygon(ship, x):
    h, f = ship["hull"], ship["hull_form"]
    original = _source_section(f["offsets_path"], f["offsets_sha256"], x * f["reference_lpp_m"] / h["lpp_m"])
    nodes = []
    reference_t = f["reference_draught_m"]
    for left, right in zip(original, np.roll(original, -1, axis=0)):
        nodes.append(left)
        if (left[1] - reference_t) * (right[1] - reference_t) < 0:
            nodes.append(left + (reference_t - left[1]) / (right[1] - left[1]) * (right - left))
    poly = np.array(nodes)
    poly[:, 0] *= h["beam_m"] / f["reference_beam_m"]
    z = poly[:, 1].copy()
    poly[:, 1] = np.where(z <= reference_t, z * h["design_draught_m"] / reference_t, h["design_draught_m"] + (z - reference_t) * (h["depth_m"] - h["design_draught_m"]) / (f["reference_depth_m"] - reference_t))
    return poly


def _minimum_boundary_distances(polygon, points):
    start = polygon
    direction = np.roll(polygon, -1, axis=0) - polygon
    length_squared = np.sum(direction * direction, axis=1)
    valid = length_squared > 1e-16
    start, direction, length_squared = start[valid], direction[valid], length_squared[valid]
    values = []
    for index in range(0, len(points), 64):
        delta = points[index:index + 64, None, :] - start[None, :, :]
        fraction = np.clip(np.sum(delta * direction, axis=2) / length_squared, 0, 1)
        distance = np.linalg.norm(delta - fraction[:, :, None] * direction, axis=2)
        values.extend(distance.min(axis=1))
    return np.asarray(values)


def test_oil_tanks_have_explicit_and_geometric_side_and_bottom_protection():
    for ship in _load_fleet():
        oil = [tank for tank in ship["tanks"] if tank["kind"] in {"fuel_oil", "diesel_oil"}]
        assert sum(tank["capacity_m3"] for tank in oil) > 600
        wing_edge = ship["tank_design"]["wing_inner_edge_m"]
        for tank in oil:
            protection = tank["oil_protection"]
            assert protection["source"]
            check_numeric_provenance(protection)
            assert protection["side_distance_m"] == 0.76
            assert math.isclose(protection["bottom_height_m"], ship["hull"]["beam_m"] / 20, abs_tol=1e-12)
            assert protection["bottom_reference"].strip()
            assert protection["individual_tank_below_500_m3"] is True and tank["capacity_m3"] < 500
            geometry = tank["geometry"]
            for x, section in zip(geometry["x_m"], geometry["sections_yz_m"]):
                points = np.vstack(section)
                source = _target_source_polygon(ship, x)
                assert np.all(_minimum_boundary_distances(source, points) >= 0.76 - 1e-6), (ship["id"], tank["id"], x, "oil side clearance")
                lowered = points - [0, protection["bottom_height_m"]]
                _points_fit_source_hull(ship, x, lowered)
                if tank["kind"] == "fuel_oil":
                    assert np.max(np.abs(points[:, 0])) <= wing_edge + 1e-7, (ship["id"], tank["id"], "HFO outboard of inner wing boundary")
