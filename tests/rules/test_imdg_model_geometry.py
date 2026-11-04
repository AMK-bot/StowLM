"""Named fleet design solids feed IMDG assessments without redrawn bounds."""
from dataclasses import asdict
from functools import lru_cache

import pytest

from stowage.model.vessel import ExclusionRegionSource, HeatMapSource, ImdgPlacementGeometry, PlacementMapEvidence
from stowage.rules.imdg.placement_assessments import (
    BoxEnvelope, SunlightAssessment, assess_sw1, assess_sw2,
    geometry_from_vessel,
)
from stowage.rules.imdg.scenarios import BindingContext
from stowage.vessel.fleet import load_vessel


FLEETS = ('feeder_500', 'feeder_800', 'feeder_1700')


@lru_cache(maxsize=3)
def _loaded(name):
    return load_vessel(name, with_hull=False)


def fleet_case(name, *, complete=True):
    """Explicit hypothetical design declarations; source geometry is unchanged."""
    vessel = _loaded(name).model_copy(deep=True)
    if complete:
        evidence = PlacementMapEvidence(document=f'synthetic-test-design:{name}',
            clause='IMDG 42-24 7.1.2 SW1/SW2', scenario_id=f'{name}-test-design')
        vessel.imdg_placement_geometry = ImdgPlacementGeometry(
            living_quarters_map_complete=True, living_quarters_map_provenance=evidence.model_copy(
                update={'scenario_id': f'{name}-test-living-design'}),
            living_quarters_sources=[ExclusionRegionSource(id=region.id, kind=region.kind,
                region_id=region.id) for region in vessel.exclusion_regions],
            heat_sources=[HeatMapSource(id=source.id, tank_id=source.tank_id,
                liable_to_exceed_55_c=source.liable_to_exceed_55_c, provenance=evidence)
                for source in vessel.heat_sources],
            heat_map_complete=True, heat_map_provenance=evidence)
    else:
        # The present catalog is complete; this branch explicitly constructs an
        # incomplete declaration to preserve the unknown-evidence regression.
        vessel.imdg_placement_geometry = vessel.imdg_placement_geometry.model_copy(
            update={'living_quarters_map_complete': False})
    return vessel


def register_metadata(context, metadata):
    """Register exact fixture scope; no checker can manufacture this registry."""
    result = {}
    def visit(value):
        if hasattr(value, 'model_dump'):
            value = value.model_dump(mode='python')
        if isinstance(value, dict):
            if {'document', 'clause', 'scenario_id'} <= value.keys():
                row = asdict(context)
                row.pop('slot')
                result[value['scenario_id']] = row | {
                    'document': value['document'], 'clause': value['clause'],
                    'allowed_slots': [context.slot]}
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit(metadata)
    return result


def _context(vessel):
    return BindingContext('named-design-test', 'QZXU0000020', 'primary', '2810', 'III',
                          vessel.id, 'declared-design-voyage', 0, '0010102')


def _outside(bounds, gap):
    return BoxEnvelope(bounds.x_max_m + gap, bounds.x_max_m + gap + 6.058,
        bounds.y_min_m, bounds.y_min_m + 2.438, bounds.z_min_m, bounds.z_min_m + 2.591)


@pytest.mark.parametrize('name', FLEETS)
def test_named_catalog_is_consumed_with_every_actual_source_bound(name):
    vessel = fleet_case(name)
    context = _context(vessel)
    geometry = geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=register_metadata(context, vessel.imdg_placement_geometry), provision='SW2')
    assert geometry is not None
    assert {r.id for r in geometry.living_quarters_regions} == {r.id for r in vessel.exclusion_regions}
    for actual in vessel.exclusion_regions:
        parsed = next(r for r in geometry.living_quarters_regions if r.id == actual.id)
        assert parsed.kind == actual.kind
        assert parsed.bounds == BoxEnvelope(**actual.bounds.model_dump())


@pytest.mark.parametrize('name', FLEETS)
@pytest.mark.parametrize('gap,expected', [(3.0, True), (2.999, False)])
def test_named_sw2_uses_three_metre_surface_clearance(name, gap, expected):
    vessel = fleet_case(name)
    context = _context(vessel)
    geometry = geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=register_metadata(context, vessel.imdg_placement_geometry), provision='SW2')
    outermost = max(vessel.exclusion_regions, key=lambda r: r.bounds.x_max_m)
    envelope = _outside(outermost.bounds, gap)
    assert assess_sw2(envelope, geometry, context=context,
                     registry=register_metadata(context, vessel.imdg_placement_geometry)) is expected
    # Centre separation exceeds 3 m even in the negative surface-clearance case.
    assert (envelope.x_min_m + envelope.x_max_m)/2 - outermost.bounds.x_max_m > 3


@pytest.mark.parametrize('name', FLEETS)
def test_explicit_incomplete_fleet_catalog_and_unregistered_maps_stay_unknown(name):
    vessel = fleet_case(name, complete=False)
    context = _context(vessel)
    registry = register_metadata(context, vessel.imdg_placement_geometry)
    geometry = geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry, provision='SW2')
    bounds = max(vessel.exclusion_regions, key=lambda r: r.bounds.x_max_m).bounds
    assert assess_sw2(_outside(bounds, 20), geometry, context=context, registry=registry) is None
    assert geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry={}, provision='SW2') is None


@pytest.mark.parametrize('name', FLEETS)
@pytest.mark.parametrize('gap,expected', [(2.4, True), (2.399, False)])
def test_explicit_rectangular_source_uses_imdg_two_point_four_metre_threshold(name, gap, expected):
    """Hypothetical rectangular source; actual fleet tank sections stay distinct."""
    from stowage.model.vessel import BoxSourceGeometry
    vessel = fleet_case(name)
    source = max(vessel.heat_sources, key=lambda s: s.x_max_m)
    source.actual_geometry = BoxSourceGeometry(**{f'{axis}_{side}_m':getattr(source,f'{axis}_{side}_m')
        for axis in 'xyz' for side in ('min','max')})
    context = _context(vessel)
    registry = register_metadata(context, vessel.imdg_placement_geometry)
    geometry = geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry, provision='SW1')
    evidence = geometry.heat_map_provenance
    sunlight = SunlightAssessment(package_inside_ctu=True, precautions_required=False, provenance=evidence)
    assert assess_sw1(_outside(source, gap), geometry, on_deck=False, sunlight=sunlight,
                      context=context, registry=registry) is expected


@pytest.mark.parametrize('name', FLEETS)
def test_shared_heat_temperature_liability_unknown_and_mismatch_fail_closed(name):
    vessel = fleet_case(name)
    context = _context(vessel)
    registry = register_metadata(context, vessel.imdg_placement_geometry)
    source = max(vessel.heat_sources, key=lambda s: s.x_max_m)
    row = next(s for s in vessel.imdg_placement_geometry.heat_sources if s.id == source.id)
    row.liable_to_exceed_55_c = False
    assert geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry, provision='SW1') is None
    for actual in vessel.heat_sources:
        actual.liable_to_exceed_55_c = None
    for declared in vessel.imdg_placement_geometry.heat_sources:
        declared.liable_to_exceed_55_c = None
    vessel.imdg_placement_geometry.heat_map_complete = None
    geometry = geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry, provision='SW1')
    sunlight = SunlightAssessment(package_inside_ctu=True, precautions_required=False,
                                 provenance=geometry.heat_map_provenance)
    assert assess_sw1(_outside(source, 2), geometry, on_deck=False, sunlight=sunlight,
                      context=context, registry=registry) is None


@pytest.mark.parametrize('name', FLEETS)
def test_each_provision_ignores_unrelated_map_provenance(name):
    vessel = fleet_case(name)
    context = _context(vessel)
    registry = register_metadata(context, vessel.imdg_placement_geometry)
    vessel.imdg_placement_geometry.heat_map_provenance = None
    vessel.imdg_placement_geometry.heat_map_complete = None
    vessel.imdg_placement_geometry.heat_sources = []
    assert geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry, provision='SW2') is not None
    vessel = fleet_case(name)
    vessel.imdg_placement_geometry.living_quarters_map_provenance = None
    assert geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry, provision='SW1') is not None


@pytest.mark.parametrize('name', FLEETS)
@pytest.mark.parametrize('unbound', ['heat', 'living_quarters'])
def test_default_adapter_preserves_bound_map_without_clearing_unbound_map(name, unbound):
    vessel = fleet_case(name)
    context = _context(vessel)
    registry = register_metadata(context, vessel.imdg_placement_geometry)
    original = getattr(vessel.imdg_placement_geometry, unbound+'_map_provenance')
    setattr(vessel.imdg_placement_geometry, unbound+'_map_provenance',
            original.model_copy(update={'scenario_id': 'unregistered-foreign-map'}))
    geometry = geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry)
    assert geometry is not None
    if unbound == 'heat':
        outermost = max(vessel.exclusion_regions, key=lambda r: r.bounds.x_max_m)
        assert assess_sw2(_outside(outermost.bounds, 10), geometry,
                          context=context, registry=registry) is True
        assert geometry.heat_map_complete is not True
    else:
        source = max(vessel.heat_sources, key=lambda s: s.x_max_m)
        sunlight = SunlightAssessment(package_inside_ctu=True, precautions_required=False,
                                     provenance=geometry.heat_map_provenance)
        assert assess_sw1(_outside(source, 10), geometry, on_deck=False,
                          sunlight=sunlight, context=context, registry=registry) is True
        assert geometry.living_quarters_map_complete is not True


@pytest.mark.parametrize('name', FLEETS)
def test_named_engine_region_cannot_disagree_with_shared_heat_solid(name):
    vessel = fleet_case(name)
    region = next(r for r in vessel.exclusion_regions if r.source_kind == 'heat_source')
    region.bounds.x_min_m += 0.1
    context = _context(vessel)
    assert geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=register_metadata(context, vessel.imdg_placement_geometry), provision='SW2') is None


@pytest.mark.parametrize('name', FLEETS)
def test_full_fleet_thermal_map_binds_actual_heated_tank_links(name):
    vessel = load_vessel(name)
    context = _context(vessel)
    metadata = vessel.imdg_placement_geometry
    assert metadata.heat_map_complete is True
    assert any(tank.heated for tank in vessel.tanks)
    geometry = geometry_from_vessel(vessel, metadata, context=context,
        registry=register_metadata(context, metadata), provision='SW1')
    assert geometry is not None
    assert {row.id for row in geometry.heat_sources} == {row.id for row in vessel.heat_sources}
    for tank in vessel.tanks:
        if tank.heated:
            actual = next(source for source in vessel.heat_sources if source.tank_id == tank.id)
            declared = next(source for source in metadata.heat_sources if source.id == actual.id)
            assert declared.tank_id == tank.id
            assert (actual.x_min_m, actual.x_max_m) == (tank.x_aft_m, tank.x_fore_m)
    corrupted = vessel.model_copy(deep=True)
    linked = next(row for row in corrupted.imdg_placement_geometry.heat_sources if row.tank_id is not None)
    linked.tank_id = 'unrelated-tank'
    assert geometry_from_vessel(corrupted, corrupted.imdg_placement_geometry,
        context=context, registry=register_metadata(context, metadata), provision='SW1') is None


@pytest.mark.parametrize('name', FLEETS)
def test_known_non_hot_sources_do_not_use_the_hot_surface_clearance(name):
    vessel = fleet_case(name)
    for actual, declared in zip(vessel.heat_sources, vessel.imdg_placement_geometry.heat_sources):
        assert actual.id == declared.id
        actual.liable_to_exceed_55_c = declared.liable_to_exceed_55_c = False
    context = _context(vessel)
    registry = register_metadata(context, vessel.imdg_placement_geometry)
    geometry = geometry_from_vessel(vessel, vessel.imdg_placement_geometry,
        context=context, registry=registry, provision='SW1')
    sunlight = SunlightAssessment(package_inside_ctu=True, precautions_required=False,
                                 provenance=geometry.heat_map_provenance)
    assert assess_sw1(_outside(vessel.heat_sources[0], 0), geometry, on_deck=False,
                      sunlight=sunlight, context=context, registry=registry) is True
