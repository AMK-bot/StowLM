"""D64 pair identity and D59 shared vessel source contracts (synthetic solids)."""

import copy

import pytest
from pydantic import ValidationError

from stowage.model import Vessel


def _tank(tank_id, side):
    return dict(id=tank_id, kind="ballast", capacity_m3=100,
                content_density_kg_m3=1025, heated=False, adjustable=True,
                x_aft_m=0, x_fore_m=10,
                table=[dict(volume_m3=v, lcg_m=5, tcg_m=side * 3,
                            vcg_m=1, fsm_m4=0 if v in (0, 100) else 10)
                       for v in (0, 50, 100)])


def _payload():
    return dict(id="synthetic", name="Synthetic", nominal_teu=0,
                cells=[], stacks=[], hatch_covers=[],
                tanks=[_tank("HEEL-P", 1), _tank("HEEL-S", -1)],
                tank_pairs=[dict(id="HEEL", port_tank="HEEL-P",
                                 starboard_tank="HEEL-S", role="anti_heeling")])


def test_explicit_pair_and_legacy_vessel_round_trip():
    from stowage.model import TankPair

    vessel = Vessel(**_payload())
    assert isinstance(vessel.tank_pairs[0], TankPair)
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel
    assert [row.volume_m3 for row in vessel.tanks[0].table] == [0, 50, 100]
    legacy = _payload()
    legacy.pop("tank_pairs")
    assert Vessel(**legacy).tank_pairs == []


@pytest.mark.parametrize("mutation", [
    lambda p: p["tank_pairs"][0].update(port_tank="missing"),
    lambda p: p["tank_pairs"][0].update(starboard_tank="HEEL-P"),
    lambda p: p["tank_pairs"].append(copy.deepcopy(p["tank_pairs"][0])),
    lambda p: p["tank_pairs"].append(dict(id="second", port_tank="HEEL-P", starboard_tank="HEEL-S")),
    lambda p: p["tank_pairs"][0].update(role="trim"),
    lambda p: p["tanks"][0].update(kind="fuel_oil"),
    lambda p: p["tanks"][0].update(adjustable=False),
    lambda p: p["tanks"][0]["table"][1].update(tcg_m=-3),
    lambda p: p["tanks"][1]["table"][1].update(tcg_m=-4),
    lambda p: p["tanks"][1].update(content_density_kg_m3=1000),
])
def test_pair_rejects_incompatible_references_roles_or_levers(mutation):
    payload = _payload()
    mutation(payload)
    with pytest.raises(ValidationError):
        Vessel(**payload)


def _geometry_payload():
    payload = _payload()
    payload["tanks"].append(dict(_tank("HOT", 1), heated=True, kind="fuel_oil", adjustable=False))
    source = dict(id="heat-HOT", kind="heated_tank", tank_id="HOT",
                  liable_to_exceed_55_c=True, x_min_m=0, x_max_m=10,
                  y_min_m=0, y_max_m=6, z_min_m=0, z_max_m=2)
    payload["heat_sources"] = [source]
    payload["fixed_loads"] = [dict(mass_kg=0, x_m=5, y_m=0, z_m=5, kind="non_cargo",
                                    silhouette=dict(x_min_m=0, x_max_m=10, y_min_m=-3,
                                                    y_max_m=3, z_min_m=2, z_max_m=9))]
    evidence = dict(document="synthetic fleet", clause="D59 shared geometry", scenario_id="synthetic-design")
    payload["imdg_placement_geometry"] = dict(
        living_quarters_sources=[dict(id="deckhouse", kind="accommodation", fixed_load_index=0)],
        living_quarters_map_complete=True, living_quarters_map_provenance=evidence,
        heat_sources=[dict(id="heat-HOT", tank_id="HOT", liable_to_exceed_55_c=True, provenance=evidence)],
        heat_map_complete=True, heat_map_provenance=evidence)
    return payload


def test_geometry_sources_share_solids_and_round_trip():
    from stowage.model import ExclusionRegionSource, HeatMapSource, ImdgPlacementGeometry

    vessel = Vessel(**_geometry_payload())
    metadata = vessel.imdg_placement_geometry
    assert isinstance(metadata, ImdgPlacementGeometry)
    assert isinstance(metadata.living_quarters_sources[0], ExclusionRegionSource)
    assert isinstance(metadata.heat_sources[0], HeatMapSource)
    assert metadata.living_quarters_sources[0].fixed_load_index == 0
    assert vessel.heat_sources[0].tank_id == "HOT"
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel
    # The evidence records identify actual shared solids; independent bounds are forbidden.
    assert "bounds" not in metadata.model_dump()["living_quarters_sources"][0]


@pytest.mark.parametrize("mutation", [
    lambda p: p["heat_sources"][0].update(tank_id="absent"),
    lambda p: p["heat_sources"][0].update(tank_id="HEEL-P"),
    lambda p: p["heat_sources"][0].update(x_max_m=11),
    lambda p: p["imdg_placement_geometry"]["heat_sources"][0].update(id="foreign"),
    lambda p: p["imdg_placement_geometry"]["heat_sources"][0].update(liable_to_exceed_55_c=False),
    lambda p: p["imdg_placement_geometry"]["heat_sources"].clear(),
    lambda p: p["imdg_placement_geometry"]["living_quarters_sources"][0].update(fixed_load_index=4),
    lambda p: p["fixed_loads"][0].update(kind="cargo"),
    lambda p: p["fixed_loads"][0].update(silhouette=None),
    lambda p: p["imdg_placement_geometry"]["living_quarters_sources"][0].update(bounds={}),
])
def test_geometry_rejects_unbound_or_inconsistent_evidence(mutation):
    payload = _geometry_payload()
    mutation(payload)
    with pytest.raises(ValidationError):
        Vessel(**payload)


def test_legacy_indexed_map_missing_fixed_load_classification_becomes_incomplete():
    payload = _geometry_payload()
    payload["imdg_placement_geometry"]["living_quarters_sources"].clear()
    vessel = Vessel(**payload)
    assert vessel.imdg_placement_geometry.living_quarters_map_complete is False
    assert vessel.imdg_placement_geometry.heat_map_complete is True


def test_heat_map_omits_nonexistent_tank_link_for_engine_room():
    from stowage.model import HeatMapSource

    source = HeatMapSource(id="engine", liable_to_exceed_55_c=True)
    assert "tank_id" not in source.model_dump(mode="python")


def test_named_exclusion_regions_keep_wheelhouse_separate_from_fixed_mass():
    from stowage.model import ExclusionRegion

    payload = _geometry_payload()
    payload["imdg_placement_geometry"] = None
    payload["heat_sources"][0]["kind"] = "engine_room"
    payload["heat_sources"][0].pop("tank_id")
    payload["exclusion_regions"] = [dict(
        id="engine", kind="machinery_space", source_kind="heat_source", source_id="heat-HOT",
        bounds={key: value for key, value in payload["heat_sources"][0].items()
                if key.endswith("_m")},
        assumption="APPROXIMATION: synthetic shared engine-room source")]
    vessel = Vessel(**payload)
    assert isinstance(vessel.exclusion_regions[0], ExclusionRegion)
    assert len(vessel.fixed_loads) == len(payload["fixed_loads"])
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


@pytest.mark.parametrize("mutation", [
    lambda p: p["exclusion_regions"][0].update(source_id="absent"),
    lambda p: p["exclusion_regions"][0]["bounds"].update(x_max_m=11),
    lambda p: p["exclusion_regions"].append(copy.deepcopy(p["exclusion_regions"][0])),
])
def test_named_exclusion_regions_reject_independent_heat_bounds(mutation):
    payload = _geometry_payload()
    payload["imdg_placement_geometry"] = None
    payload["heat_sources"][0]["kind"] = "engine_room"
    payload["heat_sources"][0].pop("tank_id")
    payload["exclusion_regions"] = [dict(
        id="engine", kind="machinery_space", source_kind="heat_source", source_id="heat-HOT",
        bounds={key: value for key, value in payload["heat_sources"][0].items()
                if key.endswith("_m")},
        assumption="APPROXIMATION: synthetic shared engine-room source")]
    mutation(payload)
    with pytest.raises(ValidationError):
        Vessel(**payload)
