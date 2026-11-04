"""Model-owner loading contracts; numerical builders are tested separately."""

import pytest

from stowage.model import Tank, Vessel
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import list_fleet, load_ship_params, load_vessel


@pytest.fixture
def assembled_ship(monkeypatch):
    """Exercise real fleet assembly without rebuilding numerical hydrostatics."""
    from stowage.checker import strength_limits
    from stowage.vessel import hull, tanks, fleet_lashing_design

    monkeypatch.setenv("STOWAGE_VESSEL_CACHE", "0")
    monkeypatch.setattr(hull, "build_hull_model", lambda params: build_instance().vessel.hull)
    monkeypatch.setattr(strength_limits, "permissible_stations", lambda params, model: model.stations)
    monkeypatch.setattr(tanks, "build_tanks", lambda params: [
        Tank.model_validate({key: value for key, value in raw.items() if key in Tank.model_fields})
        for raw in params["tanks"]
    ])
    monkeypatch.setattr(fleet_lashing_design, "populate_lashing_design", lambda *args, **kwargs: None)
    return lambda name: load_vessel(name)


@pytest.mark.parametrize("name", list_fleet())
def test_fleet_loader_exposes_explicit_heel_pair_without_prefilling(name, assembled_ship):
    vessel = assembled_ship(name)
    pairs = [pair for pair in vessel.tank_pairs if pair.role == "anti_heeling"]
    assert len(pairs) == 1
    pair = pairs[0]
    assert (pair.id, pair.port_tank, pair.starboard_tank) == ("HEEL", "HEEL-P", "HEEL-S")
    assert "APPROXIMATION:" in pair.assumption
    actual = {tank.id: tank for tank in vessel.tanks}
    assert actual[pair.port_tank].adjustable and actual[pair.starboard_tank].adjustable
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


@pytest.mark.parametrize("name", list_fleet())
def test_fleet_heat_map_binds_the_actual_e8_sources_and_heated_tanks(name, assembled_ship):
    vessel = assembled_ship(name)
    metadata = vessel.imdg_placement_geometry
    assert metadata is not None
    actual = {source.id: source for source in vessel.heat_sources}
    declared = {source.id: source for source in metadata.heat_sources}
    assert declared.keys() == actual.keys()
    assert metadata.heat_map_complete is True
    tanks = {tank.id: tank for tank in vessel.tanks}
    linked = set()
    for source_id, source in actual.items():
        row = declared[source_id]
        assert row.liable_to_exceed_55_c == source.liable_to_exceed_55_c
        assert row.liable_to_exceed_55_c is not None
        assert row.provenance is not None
        if source.kind == "heated_tank":
            assert row.tank_id == source.tank_id
            tank = tanks[source.tank_id]
            assert tank.heated
            assert (source.x_min_m, source.x_max_m) == (tank.x_aft_m, tank.x_fore_m)
            linked.add(tank.id)
    assert linked == {tank.id for tank in vessel.tanks if tank.heated}


@pytest.mark.parametrize("name", list_fleet())
def test_geometry_only_omits_unavailable_tank_references(name):
    vessel = load_vessel(name, with_hull=False)
    assert vessel.tank_pairs == []
    assert all(source.tank_id is None for source in vessel.heat_sources)
    metadata = vessel.imdg_placement_geometry
    assert metadata is None or (metadata.heat_sources == [] and metadata.heat_map_complete is None)


@pytest.mark.parametrize("name", list_fleet())
def test_synthetic_design_map_is_not_automatic_shipment_permission(name, assembled_ship):
    from stowage.rules.imdg.placement_assessments import geometry_from_vessel

    vessel = assembled_ship(name)
    assert vessel.imdg_placement_geometry is not None
    # A design record cannot invent the shipment/voyage registry binding.
    assert geometry_from_vessel(vessel, vessel.imdg_placement_geometry) is None


@pytest.mark.parametrize("name", list_fleet())
def test_named_exclusions_reuse_shared_design_sources_without_self_obstruction(name):
    vessel = load_vessel(name, with_hull=False)
    params = load_ship_params(name)
    regions = {region.id: region for region in vessel.exclusion_regions}
    assert {region.kind for region in regions.values()} == {
        "accommodation", "air_intake", "machinery_space", "enclosed_work_area",
    }
    assert all("APPROXIMATION:" in region.assumption for region in regions.values())
    # Own-wheelhouse geometry is a source catalog, not an opaque obstacle in
    # front of its own conning eye; no mass is added to the lightship either.
    assert vessel.fixed_loads == []
    deck = next(row for row in params["windage"]["projected_rectangles"] if row["id"] == "deckhouse")
    for axis in "xz":
        for side in ("min", "max"):
            field = f"{axis}_{side}_m"
            assert getattr(regions["deckhouse"].bounds, field) == deck[field]
    engine = next(source for source in vessel.heat_sources if source.kind == "engine_room")
    for axis in "xyz":
        for side in ("min", "max"):
            field = f"{axis}_{side}_m"
            assert getattr(regions[engine.id].bounds, field) == getattr(engine, field)
    assert vessel.imdg_placement_geometry.living_quarters_map_complete is True
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


@pytest.mark.parametrize("source_kind", ["deckhouse", "opening"])
def test_loader_rejects_exclusion_geometry_detached_from_shared_source(source_kind):
    from stowage.vessel.fleet import _exclusion_records

    params = load_ship_params("feeder_500")
    region = next(row for row in params["exclusion_regions"] if row["source_kind"] == source_kind)
    region["bounds"]["x_min_m"] += 100.0
    region["bounds"]["x_max_m"] += 100.0
    with pytest.raises(ValueError, match="shared deckhouse|actual air intake"):
        _exclusion_records(params)


@pytest.mark.parametrize("name", list_fleet())
def test_registered_typed_fleet_map_exposes_both_actual_design_inventories(name, assembled_ship):
    from dataclasses import asdict

    from stowage.rules.imdg.placement_assessments import BoxEnvelope, assess_sw2, geometry_from_vessel
    from stowage.rules.imdg.scenarios import BindingContext

    vessel = assembled_ship(name)
    metadata = vessel.imdg_placement_geometry
    context = BindingContext(
        instance_id="fleet-contract", container_id="DG001", entry_path="primary",
        un_number="1361", packing_group="III", vessel_id=vessel.id,
        voyage_id="synthetic-contract-voyage", call_index=0, slot=vessel.cells[0].id,
    )
    registry = {}
    provenance = [metadata.heat_map_provenance, metadata.living_quarters_map_provenance,
                  *(source.provenance for source in metadata.heat_sources)]
    for evidence in provenance:
        if evidence is None:
            continue
        record = {**asdict(context), "document": evidence.document, "clause": evidence.clause,
                  "allowed_slots": [context.slot]}
        record.pop("slot")
        registry[evidence.scenario_id] = record
    # Physical design evidence still needs this exact externally supplied scope.
    assert geometry_from_vessel(vessel, metadata, context=context, registry={}) is None
    geometry = geometry_from_vessel(vessel, metadata, context=context, registry=registry)
    assert geometry is not None
    assert geometry.heat_map_complete is True
    assert {source.id for source in geometry.heat_sources} == {source.id for source in vessel.heat_sources}
    assert geometry.living_quarters_map_complete is True
    far_envelope = BoxEnvelope(1000.0, 1001.0, 0.0, 1.0, 0.0, 1.0)
    assert assess_sw2(far_envelope, geometry, context=context, registry=registry) is True


@pytest.mark.parametrize("name", list_fleet())
def test_heated_sources_retain_the_actual_section_tank_shape(name, assembled_ship):
    vessel = assembled_ship(name)
    tank_records = {tank["id"]: tank for tank in load_ship_params(name)["tanks"]}
    for source in vessel.heat_sources:
        if source.kind != "heated_tank":
            continue
        assert source.actual_geometry is not None
        assert source.actual_geometry.model_dump(mode="json") == tank_records[source.tank_id]["geometry"]
        assert source.actual_geometry.shape == "hull_sections"
        # A Gauss section inventory is not a continuous clearance surface.
        # Retaining the real shape must not turn an unsupported distance into
        # either zero or an invented safe placement.
        from stowage.model.vessel import FixedLoadSilhouette
        assert source.distance_to_box_m(FixedLoadSilhouette(
            x_min_m=1000.0, x_max_m=1001.0, y_min_m=0.0,
            y_max_m=1.0, z_min_m=0.0, z_max_m=1.0,
        )) is None
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


@pytest.mark.parametrize("name", list_fleet())
def test_sw2_catalog_covers_wider_machinery_and_enclosed_end_workspaces(name):
    vessel = load_vessel(name, with_hull=False)
    params = load_ship_params(name)
    beam = params["hull"]["beam_m"]
    regions = {region.id: region for region in vessel.exclusion_regions}
    for component in (regions["deckhouse"], regions["machinery-space-room"]):
        assert component.bounds.y_max_m - component.bounds.y_min_m == pytest.approx(0.85 * beam)
    engine = next(source for source in vessel.heat_sources if source.id == "ER")
    assert engine.y_max_m - engine.y_min_m == pytest.approx(0.16 * beam)
    room = regions["machinery-space-room"].bounds
    # A location beyond the hot core's 3m band can still be inside the actual
    # declared machinery room. SW2 must receive the room, not only the hot core.
    assert room.y_min_m < engine.y_max_m + 3.5 < room.y_max_m
    assert regions["forecastle"].kind == regions["steering_gear"].kind == "enclosed_work_area"
    assert vessel.imdg_placement_geometry.living_quarters_map_complete is True
    assert {row.region_id for row in vessel.imdg_placement_geometry.living_quarters_sources} == set(regions)


def test_loader_refuses_a_heat_shape_independent_of_its_actual_tank():
    from copy import deepcopy
    from stowage.vessel.fleet import _heat_source_records

    params = load_ship_params("feeder_500")
    source = next(raw for raw in params["heat_sources"] if raw["kind"] == "heated_tank")
    tank = next(raw for raw in params["tanks"] if raw["id"] == source["tank_id"])
    source["actual_geometry"] = deepcopy(tank["geometry"])
    source["actual_geometry"]["sections_yz_m"][0][0][0][0] += 0.1
    with pytest.raises(ValueError, match="geometry disagrees with its actual tank"):
        _heat_source_records(params)
