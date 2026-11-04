"""Declared synthetic feeder solids, including the completed eight-role catalog."""
from functools import lru_cache

import pytest

from stowage.rules.imdg.placement_assessments import BoxEnvelope, geometry_from_vessel
from stowage.rules.imdg.heat_geometry import heat_source_distance, heat_source_clearance
from stowage.rules.imdg.scenarios import BindingContext
from stowage.vessel.fleet import load_vessel
from tests.rules.test_imdg_model_geometry import FLEETS, register_metadata


@lru_cache(maxsize=3)
def round2_vessel(name):
    return load_vessel(name)


def round2_context(vessel, slot='0010102'):
    return BindingContext('round2-synthetic-design', 'QZXU0000020', 'primary', '2810', 'III',
        vessel.id, 'round2-design-leg', 0, slot)


@pytest.mark.parametrize('name', FLEETS)
def test_complete_eight_region_catalog_is_consumed_via_named_region_references(name):
    vessel = round2_vessel(name)
    context = round2_context(vessel)
    metadata = vessel.imdg_placement_geometry
    assert metadata.living_quarters_map_complete is True
    assert len(vessel.exclusion_regions) == 8
    assert {row.region_id for row in metadata.living_quarters_sources} == {r.id for r in vessel.exclusion_regions}
    geometry = geometry_from_vessel(vessel, metadata, context=context,
        registry=register_metadata(context, metadata), provision='SW2')
    assert geometry is not None
    assert geometry.living_quarters_map_complete is True
    assert {r.id for r in geometry.living_quarters_regions} == {r.id for r in vessel.exclusion_regions}
    for source in vessel.exclusion_regions:
        region = next(r for r in geometry.living_quarters_regions if r.id == source.id)
        assert region.kind == source.kind
        assert region.bounds == BoxEnvelope(**source.bounds.model_dump())


@pytest.mark.parametrize('name', FLEETS)
def test_actual_heated_tank_sections_survive_the_assessment_adapter(name):
    vessel = round2_vessel(name)
    context = round2_context(vessel)
    metadata = vessel.imdg_placement_geometry
    geometry = geometry_from_vessel(vessel, metadata, context=context,
        registry=register_metadata(context, metadata), provision='SW1')
    assert geometry is not None
    for actual in vessel.heat_sources:
        if actual.kind == 'heated_tank':
            region = next(r for r in geometry.heat_sources if r.id == actual.id)
            assert region.actual_geometry is not None
            assert region.actual_geometry == actual.actual_geometry
            assert region.actual_geometry.shape == 'hull_sections'


@pytest.mark.parametrize('name', FLEETS)
def test_real_section_samples_cannot_certify_continuous_tank_distance(name):
    vessel = round2_vessel(name)
    source = next(s for s in vessel.heat_sources if s.kind == 'heated_tank')
    geometry = source.actual_geometry
    x = (geometry.x_m[0] + geometry.x_m[1])/2
    box = BoxEnvelope(x-.01, x+.01, geometry.y_min_m, geometry.y_max_m,
                      geometry.z_min_m, geometry.z_max_m)
    bounds = BoxEnvelope(**{f'{a}_{side}_m': getattr(source, f'{a}_{side}_m')
                           for a in 'xyz' for side in ('min', 'max')})
    # Recorded Gauss sections do not define connecting panels at this x.
    assert heat_source_distance(box, geometry, source_bounds=bounds) is None
    far = BoxEnvelope(bounds.x_max_m+2.4, bounds.x_max_m+8.458,
                      bounds.y_min_m, bounds.y_min_m+2.438,
                      bounds.z_min_m, bounds.z_min_m+2.591)
    assert heat_source_clearance(far, geometry, source_bounds=bounds) is True


def test_sections_near_bounding_box_without_near_sample_witness_remain_unknown():
    from stowage.model.vessel import HullSectionsSourceGeometry
    shape = HullSectionsSourceGeometry(
        x_min_m=0, x_max_m=10, y_min_m=0, y_max_m=10, z_min_m=0, z_max_m=10,
        x_m=[0, 10], integration_weights_m=[1, 1],
        sections_yz_m=[[[ (0, 0), (1, 0), (1, 1), (0, 1) ]],
                       [[ (0, 0), (1, 0), (1, 1), (0, 1) ]]])
    box = BoxEnvelope(4.9, 5.1, 8, 9, 8, 9)
    assert heat_source_distance(box, shape) is None
    assert heat_source_clearance(box, shape) is None


def test_explicit_rectangular_solid_has_exact_surface_distance():
    from stowage.model.vessel import BoxSourceGeometry
    shape = BoxSourceGeometry(x_min_m=0, x_max_m=6, y_min_m=0, y_max_m=2, z_min_m=0, z_max_m=3)
    box = BoxEnvelope(8.4, 14.458, 0, 2.438, 0, 2.591)
    assert heat_source_distance(box, shape) == pytest.approx(2.4)
    assert heat_source_clearance(box, shape) is True
    near = BoxEnvelope(8.399, 14.457, 0, 2.438, 0, 2.591)
    assert heat_source_clearance(near, shape) is False
    assert heat_source_clearance(box, None) is None
