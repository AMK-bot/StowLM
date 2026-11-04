"""D64 rev 3 and D67 synthetic fleet declarations, independent of builders."""

from pathlib import Path
import math
import tomllib

import pytest


FLEET = Path(__file__).resolve().parents[2] / "params" / "fleet"
SHIPS = ("feeder_500", "feeder_800", "feeder_1700")

# Measured loaded C1/C2/C3 at HEEL50%, before the review redesign. Production
# in-port equilibrium, unchanged limits; retained in the vessel fix log.
HEEL_REFERENCE_STIFFNESS_KGM = {
    "feeder_500": (9769253.450418798 * 2.527098442398455,
                   9376721.37580159 * 2.2939389858532246,
                   9438469.176289726 * 2.882277041435511),
    "feeder_800": (15810063.036717197 * 1.6140585283630136,
                   13750663.535150085 * 3.523860412429312,
                   15813781.34565383 * 3.2284343557045037),
    "feeder_1700": (27389226.346044008 * 2.8089937344025415,
                    25957684.52966855 * 4.677573804332248,
                    27633194.784304924 * 4.7663759982278044),
}


@pytest.mark.parametrize("name", SHIPS)
def test_heel_design_has_three_degree_authority_for_loaded_reference_conditions(name):
    params = tomllib.loads((FLEET / f"{name}.toml").read_text())
    tank = next(tank for tank in params["tanks"] if tank["id"] == "HEEL-P")
    authority = tank["capacity_m3"] * tank["content_density_kg_m3"] * tank["table"][-1]["tcg_m"]
    assert all(math.degrees(math.atan(authority / stiffness)) >= 3.0
               for stiffness in HEEL_REFERENCE_STIFFNESS_KGM[name])


@pytest.mark.parametrize("name", SHIPS)
def test_d64_anti_heeling_pair_explicitly_identifies_existing_tanks(name):
    params = tomllib.loads((FLEET / f"{name}.toml").read_text())
    pairs = params.get("tank_pairs", [])
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["id"] == "HEEL"
    assert pair["role"] == "anti_heeling"
    assert pair["port_tank"] == "HEEL-P"
    assert pair["starboard_tank"] == "HEEL-S"
    assert pair["assumption"].startswith("APPROXIMATION:")
    tanks = {tank["id"]: tank for tank in params["tanks"]}
    for tank_id, sign in ((pair["port_tank"], 1), (pair["starboard_tank"], -1)):
        tank = tanks[tank_id]
        assert tank["adjustable"] and tank["kind"] == "ballast"
        assert all(sign * row["tcg_m"] > 0 for row in tank["table"])
    # Arrival fill belongs to the generator, not a new vessel-model default.
    assert "arrival_fill_fraction" not in pair


def test_d67_feeder_500_raises_only_hold_vents_and_keeps_unprotected_closures():
    params = tomllib.loads((FLEET / "feeder_500.toml").read_text())
    depth = params["hull"]["depth_m"]
    for opening in params["envelope"]["openings"]:
        height = 7.5 if opening["kind"] == "hold_ventilator" else 6.0
        assert opening["z_m"] == pytest.approx(depth + height, rel=0, abs=1e-12)
        assert opening["closure"] == "not_weathertight_closable"
        assert opening["progressive_flooding"] is True
        assert opening["assumption"].startswith("APPROXIMATION:")
        assert "D67" in opening["assumption"]


def test_fleet_readme_labels_intermediate_hatchway_convention():
    text = (FLEET / "README.md").read_text()
    assert (
        "hatchway credited to the coaming top; cover volume not credited: "
        "an intermediate convention pending Q20b"
    ) in text


@pytest.mark.parametrize("name", SHIPS)
def test_d59_heat_map_binds_actual_sources_and_heated_tanks(name):
    params = tomllib.loads((FLEET / f"{name}.toml").read_text())
    metadata = params["imdg_placement_geometry"]
    actual = {source["id"]: source for source in params["heat_sources"]}
    linked = {source["id"]: source for source in metadata["heat_sources"]}
    assert actual.keys() == linked.keys()
    assert metadata["heat_map_complete"] is True
    provenance = metadata["heat_map_provenance"]
    assert provenance["document"] == f"params/fleet/{name}.toml"
    assert "synthetic" in provenance["clause"]
    assert provenance["scenario_id"] == f"{name}-synthetic-design"
    heated_tanks = {tank["id"] for tank in params["tanks"] if tank["heated"]}
    assert {source["tank_id"] for source in linked.values() if "tank_id" in source} == heated_tanks
    for source_id, source in actual.items():
        assert source["liable_to_exceed_55_c"] is True
        assert source["assumption"].startswith("APPROXIMATION:")
        assert "55" in source["assumption"]
        assert linked[source_id]["liable_to_exceed_55_c"] is True
        assert source.get("tank_id") == linked[source_id].get("tank_id")
        assert linked[source_id]["provenance"] == provenance


@pytest.mark.parametrize("name", SHIPS)
def test_d59_exclusion_regions_keep_shared_physical_source_coordinates(name):
    params = tomllib.loads((FLEET / f"{name}.toml").read_text())
    regions = {region["id"]: region for region in params["exclusion_regions"]}
    assert set(regions) == {
        "deckhouse", "bridge-work-area", "ER", "ER-AIR-P", "ER-AIR-S",
        "forecastle", "steering_gear", "machinery-space-room",
    }
    assert {region["kind"] for region in regions.values()} == {
        "accommodation", "enclosed_work_area", "machinery_space", "air_intake"
    }
    for region in regions.values():
        assert region["assumption"].startswith("APPROXIMATION:")
        assert "mass" in region["assumption"]
        for axis in "xyz":
            assert region["bounds"][f"{axis}_min_m"] < region["bounds"][f"{axis}_max_m"]
    deckhouse = regions["deckhouse"]["bounds"]
    assert any(all(deckhouse[key] == rectangle[key] for key in (
        "x_min_m", "x_max_m", "z_min_m", "z_max_m"
    )) for rectangle in params["windage"]["projected_rectangles"])
    assert deckhouse["y_max_m"] == pytest.approx(0.425 * params["hull"]["beam_m"], rel=0, abs=1e-12)
    engine = next(source for source in params["heat_sources"] if source["id"] == "ER")
    assert regions["ER"]["bounds"] == {key: engine[key] for key in deckhouse}
    openings = {opening["id"]: opening for opening in params["envelope"]["openings"]}
    for source_id in ("ER-AIR-P", "ER-AIR-S"):
        bounds = regions[source_id]["bounds"]
        assert bounds["z_min_m"] == openings[source_id]["z_m"]
        for axis in "xy":
            assert bounds[f"{axis}_min_m"] == openings[source_id][f"{axis}_m"] - 0.25
            assert bounds[f"{axis}_max_m"] == openings[source_id][f"{axis}_m"] + 0.25
    for structure_id in ("forecastle", "steering_gear", "machinery_space"):
        region_id = "machinery-space-room" if structure_id == "machinery_space" else structure_id
        assert regions[region_id]["bounds"] == {
            key: params["structures"][structure_id][key] for key in deckhouse
        }
    broad_room = regions["machinery-space-room"]["bounds"]
    assert broad_room["y_max_m"] == pytest.approx(0.425 * params["hull"]["beam_m"], rel=0, abs=1e-12)
    assert broad_room["y_max_m"] > engine["y_max_m"]
    metadata = params["imdg_placement_geometry"]
    assert metadata["living_quarters_map_complete"] is True
    assert {source["region_id"] for source in metadata["living_quarters_sources"]} == regions.keys()
    assert all(source["kind"] == regions[source["region_id"]]["kind"]
               for source in metadata["living_quarters_sources"])
    provenance = params["imdg_placement_geometry"]["living_quarters_map_provenance"]
    assert provenance == {
        "document": f"params/fleet/{name}.toml",
        "clause": "D59 complete synthetic SW2 named design catalog",
        "scenario_id": f"{name}-synthetic-sw2-design",
    }
    assert provenance["scenario_id"] != params["imdg_placement_geometry"]["heat_map_provenance"]["scenario_id"]
    assert not params.get("fixed_loads")
