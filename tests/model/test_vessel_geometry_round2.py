"""Reviewed D59 contracts: preserve actual shape and identify named SW2 solids."""

import copy

import pytest
from pydantic import ValidationError

from stowage.model import HeatSource, Vessel


def _source():
    bounds = dict(x_min_m=0, x_max_m=10, y_min_m=0, y_max_m=6, z_min_m=0, z_max_m=2)
    # Recorded Gauss stations intentionally have no implied continuous panels.
    geometry = dict(shape="hull_sections", x_m=[2, 8], integration_weights_m=[5, 5],
                    sections_yz_m=[[[[0, 0], [6, 0], [6, 2], [0, 2]]],
                                   [[[0, 0], [6, 0], [6, 2], [0, 2]]]], **bounds)
    return dict(id="heated", kind="heated_tank", liable_to_exceed_55_c=True,
                actual_geometry=geometry, **bounds)


def test_heat_source_preserves_actual_section_data_without_box_substitution():
    from stowage.model import HullSectionsSourceGeometry

    source = HeatSource(**_source())
    assert isinstance(source.actual_geometry, HullSectionsSourceGeometry)
    assert source.actual_geometry.x_m == [2, 8]
    assert source.actual_geometry.integration_weights_m == [5, 5]
    assert len(source.actual_geometry.sections_yz_m) == 2
    assert HeatSource.model_validate_json(source.model_dump_json()) == source


@pytest.mark.parametrize("mutation", [
    lambda p: p["actual_geometry"].update(x_max_m=11),
    lambda p: p["actual_geometry"].update(integration_weights_m=[5]),
    lambda p: p["actual_geometry"].update(integration_weights_m=[5, -5]),
    lambda p: p["actual_geometry"].update(x_m=[8, 2]),
    lambda p: p["actual_geometry"]["sections_yz_m"][0][0].__setitem__(0, [7, 0]),
    lambda p: p["actual_geometry"]["sections_yz_m"].__setitem__(0, []),
    lambda p: p["actual_geometry"]["sections_yz_m"][0].__setitem__(0, [[0, 0], [1, 0]]),
])
def test_section_source_rejects_unbound_or_malformed_geometry(mutation):
    payload = _source()
    mutation(payload)
    with pytest.raises(ValidationError):
        HeatSource(**payload)


def test_box_geometry_is_explicit_and_legacy_missing_shape_stays_unknown():
    from stowage.model import BoxSourceGeometry

    payload = _source()
    bounds = {k: v for k, v in payload.items() if k.endswith("_m")}
    payload["actual_geometry"] = dict(shape="rectangular_prism", **bounds)
    assert isinstance(HeatSource(**payload).actual_geometry, BoxSourceGeometry)
    payload.pop("actual_geometry")
    assert HeatSource(**payload).actual_geometry is None


def _vessel():
    bounds = dict(x_min_m=20, x_max_m=30, y_min_m=-3, y_max_m=3, z_min_m=4, z_max_m=8)
    evidence = dict(document="synthetic", clause="D59 named source inventory", scenario_id="design")
    return dict(id="synthetic", name="Synthetic", nominal_teu=0, cells=[], stacks=[], hatch_covers=[],
                exclusion_regions=[dict(id="forecastle", kind="enclosed_work_area", source_kind="forecastle",
                                        source_id="forecastle", bounds=bounds, assumption="APPROXIMATION: synthetic")],
                imdg_placement_geometry=dict(living_quarters_sources=[dict(
                    id="forecastle", kind="enclosed_work_area", region_id="forecastle")],
                    living_quarters_map_complete=True, living_quarters_map_provenance=evidence))


def test_named_sources_are_nonempty_bound_and_complete_without_opaque_fixed_loads():
    from stowage.model import ExclusionRegionSource

    vessel = Vessel(**_vessel())
    declared = vessel.imdg_placement_geometry.living_quarters_sources[0]
    assert isinstance(declared, ExclusionRegionSource)
    assert declared.region_id == vessel.exclusion_regions[0].id
    assert declared.fixed_load_index is None
    assert "fixed_load_index" not in declared.model_dump()
    assert vessel.fixed_loads == []
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


@pytest.mark.parametrize("mutation", [
    lambda p: p["imdg_placement_geometry"]["living_quarters_sources"][0].update(region_id="foreign"),
    lambda p: p["imdg_placement_geometry"]["living_quarters_sources"][0].update(kind="accommodation"),
    lambda p: p["imdg_placement_geometry"]["living_quarters_sources"][0].update(fixed_load_index=0),
    lambda p: p["imdg_placement_geometry"]["living_quarters_sources"].clear(),
    lambda p: p["exclusion_regions"].append(dict(copy.deepcopy(p["exclusion_regions"][0]), id="omitted")),
])
def test_complete_named_inventory_rejects_foreign_or_omitted_sources(mutation):
    payload = _vessel()
    mutation(payload)
    with pytest.raises(ValidationError):
        Vessel(**payload)


@pytest.mark.parametrize("offset,expected", [(0.0, 0.0), (1.0, 1.0), (3.0, 3.0)])
def test_explicit_rectangular_source_exposes_exact_box_distance(offset, expected):
    from stowage.model import FixedLoadSilhouette

    payload = _source()
    payload["actual_geometry"] = dict(shape="rectangular_prism", **{
        k: v for k, v in payload.items() if k.endswith("_m")})
    source = HeatSource(**payload)
    box = FixedLoadSilhouette(x_min_m=10 + offset, x_max_m=11 + offset,
                              y_min_m=1, y_max_m=2, z_min_m=0, z_max_m=1)
    assert source.distance_to_box_m(box) == expected


def test_section_samples_and_missing_actual_geometry_do_not_claim_continuous_clearance():
    from stowage.model import FixedLoadSilhouette

    payload = _source()
    box = FixedLoadSilhouette(x_min_m=20, x_max_m=21, y_min_m=1, y_max_m=2, z_min_m=0, z_max_m=1)
    assert HeatSource(**payload).distance_to_box_m(box) is None
    payload.pop("actual_geometry")
    assert HeatSource(**payload).distance_to_box_m(box) is None


def _room_vessel():
    core = dict(id="ER", kind="engine_room", x_min_m=0, x_max_m=10,
                y_min_m=-1, y_max_m=1, z_min_m=0, z_max_m=2)
    room = dict(id="machinery-space", kind="machinery_space", source_kind="machinery_space",
                source_id="machinery_space", bounds=dict(x_min_m=0, x_max_m=10,
                    y_min_m=-5, y_max_m=5, z_min_m=0, z_max_m=3),
                assumption="APPROXIMATION: synthetic room may contain integral service tanks; hot core is unchanged")
    return dict(id="synthetic", name="Synthetic", nominal_teu=0, cells=[], stacks=[],
                hatch_covers=[], heat_sources=[core], exclusion_regions=[room])


def test_full_machinery_room_references_and_contains_actual_hot_core():
    payload = _room_vessel()
    vessel = Vessel(**payload)
    assert vessel.exclusion_regions[0].bounds.y_max_m == 5
    assert vessel.heat_sources[0].y_max_m == 1
    assert len(vessel.heat_sources) == 1
    assert vessel.fixed_loads == []
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


@pytest.mark.parametrize("mutation", [
    lambda p: p["heat_sources"].clear(),
    lambda p: p["exclusion_regions"][0]["bounds"].update(y_min_m=-0.5),
    lambda p: p["exclusion_regions"][0].update(source_id="foreign"),
])
def test_machinery_room_rejects_unbound_or_unenclosed_hot_core(mutation):
    payload = _room_vessel()
    mutation(payload)
    with pytest.raises(ValidationError):
        Vessel(**payload)


@pytest.mark.parametrize("extra", [
    dict(x_m=0, y_m=0, z_m=1, mass_kg=10, kind="non_cargo"),
    (0, 0, 1, 10),
    dict(x_m=0, y_m=0, z_m=1, mass_kg=10, kind="non_cargo", silhouette=dict(
        x_min_m=-1, x_max_m=1, y_min_m=-1, y_max_m=1, z_min_m=0, z_max_m=2)),
])
def test_external_unclassified_fixed_solids_remain_readable_with_incomplete_sw2(extra):
    """An augmented inventory cannot inherit the base fleet's affirmative map."""
    payload = _vessel()
    payload["fixed_loads"] = [extra]
    vessel = Vessel(**payload)
    assert vessel.imdg_placement_geometry.living_quarters_map_complete is False
    assert payload["imdg_placement_geometry"]["living_quarters_map_complete"] is True
    assert len(vessel.fixed_loads) == 1
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel


def test_unclassified_solid_never_gets_affirmative_sw2_clearance():
    from dataclasses import asdict
    from stowage.rules.imdg.placement_assessments import (
        BoxEnvelope, PlacementEvidence, PlacementGeometry, assess_sw2,
    )
    from stowage.rules.imdg.scenarios import BindingContext

    payload = _vessel()
    payload["fixed_loads"] = [(0, 0, 1, 10)]
    vessel = Vessel(**payload)
    metadata = vessel.imdg_placement_geometry
    evidence = PlacementEvidence(**metadata.living_quarters_map_provenance.model_dump())
    context = BindingContext("i", "box", "primary", "1203", "II", vessel.id, "v", 0, "0010182")
    record = {key: value for key, value in asdict(context).items() if key != "slot"}
    registry = {evidence.scenario_id: dict(document=evidence.document, clause=evidence.clause,
                                          allowed_slots=[context.slot], **record)}
    geometry = PlacementGeometry(living_quarters_map_complete=metadata.living_quarters_map_complete,
                                 living_quarters_map_provenance=evidence)
    box = BoxEnvelope(x_min_m=100, x_max_m=101, y_min_m=100, y_max_m=101, z_min_m=100, z_max_m=101)
    assert assess_sw2(box, geometry, context=context, registry=registry) is None
