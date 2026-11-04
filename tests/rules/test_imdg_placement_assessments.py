"""Synthetic geometry/evidence fixtures for IMDG 42-24 7.1.2, SW1 and SW2."""

from dataclasses import asdict, replace
from math import sqrt
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, RootModel

from stowage.rules.imdg.placement_assessments import (
    BoxEnvelope, ExclusionRegion, HeatRegion, PlacementEvidence, PlacementGeometry,
    SunlightAssessment, assess_sw1 as _assess_sw1, assess_sw2 as _assess_sw2,
    geometry_from_facts, sunlight_from_facts, geometry_from_vessel, sunlight_from_vessel,
    covered_under_deck, any_admissible_vertical_placement,
)
from stowage.rules.imdg.scenarios import BindingContext


EVIDENCE = PlacementEvidence('synthetic vessel design', 'IMDG 42-24 7.1.2', 'geometry-safe-1')
BOX = BoxEnvelope(0, 6, 0, 2.44, 0, 2.9)
SHELTERED_CTU = SunlightAssessment(package_inside_ctu=True, precautions_required=False, provenance=EVIDENCE)
CONTEXT = BindingContext('instance-1', 'box-1', 'primary', '1361', 'III', 'vessel-1', 'voyage-1', 0, '0010102')
ADAPTER_EVIDENCE = PlacementEvidence('synthetic vessel design', '7.1.2', 'fixture-1')


def registry_for(*evidences, context=CONTEXT):
    result = {}
    for evidence in evidences:
        record = asdict(context)
        record.pop('slot')
        record.update(document=evidence.document, clause=evidence.clause, allowed_slots=[context.slot])
        result[evidence.scenario_id] = record
    return result


def assess_sw1(*args, **kwargs):
    return _assess_sw1(*args, **kwargs, context=CONTEXT, registry=registry_for(EVIDENCE, ADAPTER_EVIDENCE))


def assess_sw2(*args, **kwargs):
    return _assess_sw2(*args, **kwargs, context=CONTEXT, registry=registry_for(EVIDENCE, ADAPTER_EVIDENCE))


def living_geometry(gap=3.0, kind='accommodation'):
    return PlacementGeometry(
        living_quarters_regions=(ExclusionRegion(
            'excluded-1', kind, BoxEnvelope(6 + gap, 12 + gap, 0, 2.44, 0, 2.9)),),
        living_quarters_map_complete=True, living_quarters_map_provenance=EVIDENCE,
    )


def heat_geometry(gap=2.4, liable=True):
    return PlacementGeometry(
        heat_sources=(HeatRegion('heat-1', BoxEnvelope(6 + gap, 12 + gap, 0, 2.44, 0, 2.9),
                                 liable, EVIDENCE),),
        heat_map_complete=True, heat_map_provenance=EVIDENCE,
    )


def test_sw2_positive_at_minimum_distance():
    assert assess_sw2(BOX, living_geometry()) is True


@pytest.mark.parametrize('kind', ['accommodation', 'air_intake', 'machinery_space', 'enclosed_work_area'])
def test_sw2_negative_every_exclusion_category(kind):
    assert assess_sw2(BOX, living_geometry(2.999, kind)) is False


def test_sw2_unknown_incomplete_or_unproven_map():
    assert assess_sw2(BOX, None) is None
    assert assess_sw2(BOX, PlacementGeometry()) is None
    assert assess_sw2(BOX, replace(living_geometry(), living_quarters_map_complete=False)) is None
    assert assess_sw2(BOX, replace(living_geometry(), living_quarters_map_provenance=None)) is None


def test_sw2_uses_envelope_and_all_three_axes():
    # A source above the CTU is separated by exactly 3 m, even though footprints overlap.
    high = ExclusionRegion('intake', 'air_intake', BoxEnvelope(0, 6, 0, 2.44, 5.9, 7))
    geometry = replace(living_geometry(), living_quarters_regions=(high,))
    assert assess_sw2(BOX, geometry) is True
    assert assess_sw2(replace(BOX, z_max_m=3.0), geometry) is False


def test_complete_empty_exclusion_inventory_is_known():
    assert assess_sw2(BOX, PlacementGeometry(
        living_quarters_map_complete=True, living_quarters_map_provenance=EVIDENCE)) is True


def test_sw1_positive_at_24m_under_deck():
    assert assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=SHELTERED_CTU) is True


def test_sw1_underdeck_ctu_sunlight_applicability_still_needs_evidence():
    # Underdeck is not proof of shelter: a hatchless hold can remain exposed.
    assert assess_sw1(BOX, heat_geometry(), on_deck=False) is None
    assert assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=SHELTERED_CTU) is True
    exposed = replace(SHELTERED_CTU, precautions_required=True, exposure_reduced=False)
    assert assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=exposed) is False


def test_sw1_underdeck_uncontained_packages_have_no_mandatory_shading():
    packages = SunlightAssessment(package_inside_ctu=False, provenance=EVIDENCE)
    assert assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=packages) is True
    assert assess_sw1(BOX, heat_geometry(), on_deck=True, sunlight=packages) is None


def test_sw1_negative_below_24m_even_with_sunlight_protection():
    sun = SunlightAssessment(package_inside_ctu=True, precautions_required=False, provenance=EVIDENCE)
    assert assess_sw1(BOX, heat_geometry(2.399), on_deck=True, sunlight=sun) is False


def test_sw1_unknown_absent_map_completeness_or_source_temperature_evidence():
    assert assess_sw1(BOX, None, on_deck=False) is None
    assert assess_sw1(BOX, PlacementGeometry(), on_deck=False) is None
    assert assess_sw1(BOX, replace(heat_geometry(), heat_map_provenance=None), on_deck=False) is None
    assert assess_sw1(BOX, heat_geometry(0, None), on_deck=False) is None
    source = replace(heat_geometry(0).heat_sources[0], provenance=None)
    assert assess_sw1(BOX, replace(heat_geometry(), heat_sources=(source,)), on_deck=False) is None


def test_sw1_55c_classification_is_not_e8_clearance():
    assert assess_sw1(BOX, heat_geometry(0, False), on_deck=False, sunlight=SHELTERED_CTU) is True
    # Unknown source temperature cannot matter when even a hot source clears 2.4 m.
    assert assess_sw1(BOX, heat_geometry(10, None), on_deck=False, sunlight=SHELTERED_CTU) is True


def test_sw1_actual_high_cube_envelope_changes_distance():
    source = HeatRegion('heated-deck', BoxEnvelope(0, 6, 0, 2.44, 5.3, 7), True, EVIDENCE)
    geometry = replace(heat_geometry(), heat_sources=(source,))
    assert assess_sw1(BOX, geometry, on_deck=False, sunlight=SHELTERED_CTU) is True
    assert assess_sw1(replace(BOX, z_max_m=3.2), geometry, on_deck=False) is False


@pytest.mark.parametrize(('sunlight', 'expected'), [
    (None, None),
    (SunlightAssessment(package_inside_ctu=True, precautions_required=False, provenance=EVIDENCE), True),
    (SunlightAssessment(package_inside_ctu=True, precautions_required=True, exposure_reduced=True, provenance=EVIDENCE), True),
    (SunlightAssessment(package_inside_ctu=True, precautions_required=True, exposure_reduced=False, provenance=EVIDENCE), False),
    (SunlightAssessment(package_inside_ctu=True, precautions_required=True, provenance=EVIDENCE), None),
    (SunlightAssessment(package_inside_ctu=False, shaded=True, provenance=EVIDENCE), True),
    (SunlightAssessment(package_inside_ctu=False, shaded=False, provenance=EVIDENCE), False),
    (SunlightAssessment(package_inside_ctu=False, shaded=True), None),
])
def test_sw1_sunlight_separate_from_structure_clearance(sunlight, expected):
    assert assess_sw1(BOX, heat_geometry(), on_deck=True, sunlight=sunlight) is expected


def test_mapping_adapters_retain_evidence_and_unknowns():
    provenance = dict(document='synthetic vessel design', clause='7.1.2', scenario_id='fixture-1')
    bounds = dict(x_min_m=9, x_max_m=15, y_min_m=0, y_max_m=2.44, z_min_m=0, z_max_m=2.9)
    geometry = geometry_from_facts(dict(
        living_quarters_regions=[dict(id='intake', kind='air_intake', bounds=bounds)],
        living_quarters_map_complete=True, living_quarters_map_provenance=provenance,
        heat_sources=[dict(id='tank', bounds=bounds, liable_to_exceed_55_c=True, provenance=provenance)],
        heat_map_complete=True, heat_map_provenance=provenance,
    ))
    assert assess_sw2(BOX, geometry) is True
    sun = sunlight_from_facts(dict(package_inside_ctu=True, precautions_required=False, provenance=provenance))
    assert assess_sw1(BOX, geometry, on_deck=True, sunlight=sun) is True
    assert geometry_from_facts(None) is None
    assert geometry_from_facts({'heat_sources': 'not a region list'}) is None
    assert geometry_from_facts({'heat_map_complete': 'true'}) is None
    assert sunlight_from_facts({'package_inside_ctu': 'true'}) is None
    assert sunlight_from_facts({'provenance': {'document': 'source only'}}) is None


def test_heat_geometry_adapter_binds_actual_vessel_inventory():
    provenance = dict(document='synthetic vessel design', clause='7.1.2', scenario_id='fixture-1')
    bounds = dict(x_min_m=9, x_max_m=15, y_min_m=0, y_max_m=2.44, z_min_m=0, z_max_m=2.9)
    sources = [SimpleNamespace(id='tank', **bounds)]
    facts = dict(heat_sources=[dict(id='tank', liable_to_exceed_55_c=True, provenance=provenance)],
                 heat_map_complete=True, heat_map_provenance=provenance)
    geometry = geometry_from_facts(facts, heat_sources=sources)
    assert assess_sw1(BOX, geometry, on_deck=False, sunlight=SHELTERED_CTU) is True
    assert geometry_from_facts({**facts, 'heat_sources': []}, heat_sources=sources) is None
    assert geometry_from_facts(facts, heat_sources=[]) is None
    moved = {**facts, 'heat_sources': [{**facts['heat_sources'][0], 'bounds': {**bounds, 'x_min_m': 10}}]}
    assert geometry_from_facts(moved, heat_sources=sources) is None
    assert geometry_from_facts(geometry, heat_sources=[]) is None
    # An independent SW2 map remains usable without a heat assessment.
    living = dict(living_quarters_regions=[], living_quarters_map_complete=True,
                  living_quarters_map_provenance=provenance)
    geometry = geometry_from_facts(living, heat_sources=sources)
    assert assess_sw2(BOX, geometry) is True
    assert assess_sw1(BOX, geometry, on_deck=False) is None


def test_sw2_diagonal_clearance_is_shortest_3d_distance():
    # Two 2 m component gaps produce sqrt(8) m and therefore still violate 3 m.
    diagonal = ExclusionRegion('workshop', 'enclosed_work_area', BoxEnvelope(8, 9, 4.44, 5, 0, 2.9))
    geometry = replace(living_geometry(), living_quarters_regions=(diagonal,))
    assert assess_sw2(BOX, geometry) is False
    diagonal = replace(diagonal, bounds=replace(diagonal.bounds, z_min_m=4.9, z_max_m=5))
    assert assess_sw2(BOX, replace(geometry, living_quarters_regions=(diagonal,))) is True


def test_pydantic_geometry_and_sunlight_adapters():
    class GeometryModel(BaseModel):
        living_quarters_regions: list[dict] = []
        living_quarters_map_complete: bool = True
        living_quarters_map_provenance: PlacementEvidence = EVIDENCE

    class SunlightModel(BaseModel):
        package_inside_ctu: bool = True
        precautions_required: bool = False
        provenance: PlacementEvidence = EVIDENCE

    geometry = geometry_from_facts(GeometryModel())
    assert assess_sw2(BOX, geometry) is True
    sunlight = sunlight_from_facts(SunlightModel())
    assert assess_sw1(BOX, heat_geometry(), on_deck=True, sunlight=sunlight) is True
    # Dumping a model does not permit invalid facts or non-object payloads.
    assert geometry_from_facts(RootModel[dict]({'heat_map_complete': 'true'})) is None
    assert sunlight_from_facts(RootModel[dict]({'shaded': 'true'})) is None
    assert geometry_from_facts(RootModel[list]([])) is None
    assert sunlight_from_facts(RootModel[list]([])) is None


def vessel_fixture():
    return SimpleNamespace(
        id='vessel-1', heat_sources=[], tanks=[],
        cells=[SimpleNamespace(id='0010102', stack_id='00101H')],
        stacks=[SimpleNamespace(id='00101H', on_deck=False, hatch_cover_id='HC1')],
        hatch_covers=[SimpleNamespace(id='HC1', hold_stack_ids=['00101H'])],
        fixed_loads=[SimpleNamespace(kind='non_cargo', silhouette=SimpleNamespace(
            x_min_m=20, x_max_m=26, y_min_m=-4, y_max_m=4, z_min_m=0, z_max_m=16))],
    )


def test_vessel_adapter_derives_accommodation_from_shared_visibility_silhouette():
    vessel = vessel_fixture()
    metadata = dict(living_quarters_sources=[dict(id='deckhouse', kind='accommodation', fixed_load_index=0)],
                    living_quarters_map_complete=True, living_quarters_map_provenance=asdict(EVIDENCE))
    geometry = geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE))
    assert geometry.living_quarters_regions[0].bounds == BoxEnvelope(20, 26, -4, 4, 0, 16)
    assert assess_sw2(BOX, geometry) is True
    vessel.fixed_loads[0].kind = 'cargo'
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is None


def test_vessel_adapter_rejects_unbound_maps_and_independent_bounds():
    metadata = dict(living_quarters_sources=[], living_quarters_map_complete=True,
                    living_quarters_map_provenance=asdict(EVIDENCE))
    assert geometry_from_vessel(vessel_fixture(), metadata, context=CONTEXT, registry={}) is None
    metadata['living_quarters_regions'] = [dict(id='foreign', kind='accommodation', bounds=asdict(BOX))]
    assert geometry_from_vessel(vessel_fixture(), metadata, context=CONTEXT,
                                registry=registry_for(EVIDENCE)) is None


def test_complete_living_map_cannot_omit_existing_non_cargo_structure():
    metadata = dict(living_quarters_sources=[], living_quarters_map_complete=True,
                    living_quarters_map_provenance=asdict(EVIDENCE))
    assert geometry_from_vessel(vessel_fixture(), metadata, context=CONTEXT,
                               registry=registry_for(EVIDENCE)) is None


def test_complete_living_map_needs_shared_3d_extent_for_known_bridge():
    vessel = vessel_fixture()
    vessel.bridge = SimpleNamespace(x_m=22, eye_z_m=15)
    structure = vessel.fixed_loads[0]
    vessel.fixed_loads = []
    metadata = dict(living_quarters_sources=[], living_quarters_map_complete=True,
                    living_quarters_map_provenance=asdict(EVIDENCE))
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is None
    vessel.fixed_loads = [structure]
    metadata['living_quarters_sources'] = [dict(id='deckhouse', kind='accommodation', fixed_load_index=0)]
    geometry = geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE))
    assert geometry is not None and assess_sw2(BOX, geometry) is True
    # The bridge front may coincide with a solid boundary; containment is inclusive.
    vessel.bridge.x_m = structure.silhouette.x_max_m
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is not None
    vessel.bridge.eye_z_m = structure.silhouette.z_max_m + .1
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is None


def test_vessel_adapter_uses_engine_room_and_checks_heated_tank_geometry():
    vessel = vessel_fixture()
    bounds = dict(x_min_m=20, x_max_m=26, y_min_m=-4, y_max_m=4, z_min_m=0, z_max_m=16)
    vessel.heat_sources = [SimpleNamespace(id='ER', kind='engine_room', **bounds)]
    metadata = dict(living_quarters_sources=[dict(id='deckhouse', kind='accommodation', fixed_load_index=0)],
                    living_quarters_map_complete=True,
                    living_quarters_map_provenance=asdict(EVIDENCE), heat_sources=[
                        dict(id='ER', liable_to_exceed_55_c=True, provenance=asdict(EVIDENCE))],
                    heat_map_complete=True, heat_map_provenance=asdict(EVIDENCE))
    geometry = geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE))
    machinery = next(region for region in geometry.living_quarters_regions if region.id == 'ER')
    assert machinery.kind == 'machinery_space'
    assert machinery.bounds == geometry.heat_sources[0].bounds
    vessel.tanks = [SimpleNamespace(id='heated-fuel', heated=True, x_aft_m=5, x_fore_m=10)]
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is None


def test_heated_tank_explicit_link_binds_actual_source_id_and_longitudinal_extent():
    vessel = vessel_fixture()
    vessel.heat_sources = [SimpleNamespace(id='heat-FO', kind='heated_tank', x_min_m=5, x_max_m=10,
                                         y_min_m=0, y_max_m=4, z_min_m=0, z_max_m=5)]
    vessel.tanks = [SimpleNamespace(id='FO', heated=True, x_aft_m=5, x_fore_m=10)]
    metadata = dict(heat_map_complete=True, heat_map_provenance=asdict(EVIDENCE), heat_sources=[
        dict(id='heat-FO', tank_id='FO', liable_to_exceed_55_c=True, provenance=asdict(EVIDENCE))])
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is not None
    metadata['heat_sources'][0]['tank_id'] = 'unregistered-tank'
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is None
    metadata['heat_sources'][0]['tank_id'] = 'FO'
    vessel.heat_sources[0].x_max_m = 9.5
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is None


def test_sw2_only_map_does_not_require_irrelevant_heated_tank_metadata():
    vessel = vessel_fixture()
    bounds = dict(x_min_m=20, x_max_m=26, y_min_m=-4, y_max_m=4, z_min_m=0, z_max_m=16)
    vessel.heat_sources = [
        SimpleNamespace(id='ER', kind='engine_room', **bounds),
        SimpleNamespace(id='heat-FO', kind='heated_tank', **bounds),
    ]
    vessel.tanks = [SimpleNamespace(id='FO', heated=True, x_aft_m=20, x_fore_m=26)]
    metadata = dict(living_quarters_sources=[dict(id='deckhouse', kind='accommodation', fixed_load_index=0)],
                    living_quarters_map_complete=True, living_quarters_map_provenance=asdict(EVIDENCE))
    geometry = geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE))
    assert geometry is not None
    assert assess_sw2(BOX, geometry) is True
    assert {region.id for region in geometry.living_quarters_regions} == {'ER', 'deckhouse'}
    assert assess_sw1(BOX, geometry, on_deck=False, sunlight=SHELTERED_CTU) is None
    # A present heat map still must bind its inventory and tank links.
    metadata.update(heat_map_complete=True, heat_map_provenance=asdict(EVIDENCE), heat_sources=[])
    assert geometry_from_vessel(vessel, metadata, context=CONTEXT, registry=registry_for(EVIDENCE)) is None


def test_sunlight_comes_from_actual_reciprocal_hatch_geometry():
    vessel = vessel_fixture()
    declaration = dict(package_inside_ctu=True, precautions_required=True, provenance=asdict(EVIDENCE))
    sunlight = sunlight_from_vessel(vessel, CONTEXT.slot, declaration, context=CONTEXT,
                                    registry=registry_for(EVIDENCE))
    assert sunlight.shaded is True and sunlight.exposure_reduced is True
    assert covered_under_deck(vessel, CONTEXT.slot) is True
    assert covered_under_deck(vessel, CONTEXT.slot, covers_off=['HC1']) is False
    exposed = sunlight_from_vessel(vessel, CONTEXT.slot, declaration, covers_off=['HC1'],
                                   context=CONTEXT, registry=registry_for(EVIDENCE))
    assert exposed.shaded is False and exposed.exposure_reduced is False
    vessel.hatch_covers[0].hold_stack_ids = []
    assert covered_under_deck(vessel, CONTEXT.slot) is None
    assert sunlight_from_vessel(vessel, CONTEXT.slot, declaration, context=CONTEXT,
                               registry=registry_for(EVIDENCE)) is None


def test_inapplicable_sunlight_condition_needs_no_hatch_shelter_evidence():
    vessel = vessel_fixture()
    vessel.hatch_covers = []
    declaration = dict(package_inside_ctu=True, precautions_required=False, provenance=asdict(EVIDENCE))
    sunlight = sunlight_from_vessel(vessel, CONTEXT.slot, declaration, context=CONTEXT,
                                    registry=registry_for(EVIDENCE))
    assert sunlight is not None
    assert sunlight.shaded is None and sunlight.exposure_reduced is None
    assert assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=sunlight) is True
    declaration['precautions_required'] = True
    assert sunlight_from_vessel(vessel, CONTEXT.slot, declaration, context=CONTEXT,
                               registry=registry_for(EVIDENCE)) is None
    # The mandatory uncontained-package shading condition applies on deck only.
    declaration = dict(package_inside_ctu=False, provenance=asdict(EVIDENCE))
    sunlight = sunlight_from_vessel(vessel, CONTEXT.slot, declaration, context=CONTEXT,
                                    registry=registry_for(EVIDENCE))
    assert sunlight is not None and sunlight.shaded is None
    assert assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=sunlight) is True


def test_declared_slot_outcome_flags_cannot_replace_geometry():
    declaration = dict(package_inside_ctu=True, precautions_required=True,
                       exposure_reduced=True, shaded=True, provenance=asdict(EVIDENCE))
    assert sunlight_from_vessel(vessel_fixture(), CONTEXT.slot, declaration, context=CONTEXT,
                               registry=registry_for(EVIDENCE)) is None


def test_deck_sunlight_policy_rejects_exposed_required_precautions():
    context = replace(CONTEXT, slot='0010182')
    registry = registry_for(EVIDENCE, context=context)
    declaration = dict(package_inside_ctu=True, precautions_required=True, provenance=asdict(EVIDENCE))
    sunlight = sunlight_from_vessel(vessel_fixture(), context.slot, declaration, context=context, registry=registry)
    assert sunlight.exposure_reduced is False
    assert _assess_sw1(BOX, heat_geometry(), on_deck=True, sunlight=sunlight,
                      context=context, registry=registry) is False
    declaration['precautions_required'] = False
    sunlight = sunlight_from_vessel(vessel_fixture(), context.slot, declaration, context=context, registry=registry)
    assert _assess_sw1(BOX, heat_geometry(), on_deck=True, sunlight=sunlight,
                      context=context, registry=registry) is True


def test_geometry_binding_rejects_unrelated_source_and_sunlight_scopes():
    foreign = replace(CONTEXT, container_id='another-box')
    wrong_registry = registry_for(EVIDENCE, context=foreign)
    assert _assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=SHELTERED_CTU,
                      context=CONTEXT, registry=wrong_registry) is None
    declaration = dict(package_inside_ctu=True, precautions_required=True, provenance=asdict(EVIDENCE))
    assert sunlight_from_vessel(vessel_fixture(), CONTEXT.slot, declaration,
                               context=CONTEXT, registry=wrong_registry) is None


def test_geometry_assessors_reject_unbound_or_foreign_scenario_evidence():
    assert _assess_sw2(BOX, living_geometry()) is None
    assert _assess_sw1(BOX, heat_geometry(), on_deck=False, sunlight=SHELTERED_CTU) is None
    foreign = replace(CONTEXT, vessel_id='other-vessel')
    assert _assess_sw2(BOX, living_geometry(), context=foreign, registry=registry_for(EVIDENCE)) is None


def test_vertical_screen_accepts_slot_that_is_safe_only_at_higher_admissible_floor():
    region = ExclusionRegion('lower-workroom', 'enclosed_work_area', BOX)
    geometry = replace(living_geometry(), living_quarters_regions=(region,))
    kwargs = dict(provision='SW2', context=CONTEXT, registry=registry_for(EVIDENCE))
    assert any_admissible_vertical_placement(BOX, geometry, 0, 6, **kwargs) is True
    assert any_admissible_vertical_placement(BOX, geometry, 0, 2, **kwargs) is False
    assert any_admissible_vertical_placement(BOX, None, 0, 2, **kwargs) is True


def test_vertical_screen_preserves_narrow_safe_gap_at_checker_tolerance():
    regions = (
        ExclusionRegion('lower', 'machinery_space', BoxEnvelope(0, 6, 0, 2.44, 0, 0)),
        ExclusionRegion('upper', 'air_intake', BoxEnvelope(
            6 + sqrt(9 - .3 ** 2), 7 + sqrt(9 - .3 ** 2), 0, 2.44,
            6.2 - 1.05e-8, 7.2 - 1.05e-8)),
    )
    geometry = replace(living_geometry(), living_quarters_regions=regions)
    actual = replace(BOX, z_min_m=3 - .9e-9, z_max_m=5.9 - .9e-9)
    assert assess_sw2(actual, geometry) is True
    assert any_admissible_vertical_placement(
        BOX, geometry, 3 - 2e-8, 3 + 1e-8, provision='SW2',
        context=CONTEXT, registry=registry_for(EVIDENCE)) is True


@pytest.mark.parametrize('bounds', [
    (2, 1, 0, 1, 0, 1), (0, float('nan'), 0, 1, 0, 1), (0, 1, 0, float('inf'), 0, 1),
])
def test_invalid_bounds_rejected(bounds):
    with pytest.raises(ValueError):
        BoxEnvelope(*bounds)
