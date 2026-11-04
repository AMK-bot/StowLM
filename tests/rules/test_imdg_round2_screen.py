"""A necessary screen cannot turn a missing continuous surface into a solid."""
from stowage.model.vessel import HullSectionsSourceGeometry
from stowage.rules.imdg.placement_assessments import (
    BoxEnvelope, HeatRegion, PlacementGeometry, any_admissible_vertical_placement,
)
from tests.rules.test_imdg_placement_assessments import CONTEXT, EVIDENCE, SHELTERED_CTU, registry_for


def test_unknown_between_station_heat_surface_does_not_prune_a_possible_height():
    shape = HullSectionsSourceGeometry(
        x_min_m=0, x_max_m=10, y_min_m=0, y_max_m=10, z_min_m=0, z_max_m=10,
        x_m=[0, 10], integration_weights_m=[1, 1],
        sections_yz_m=[[[ (0, 0), (1, 0), (1, 1), (0, 1) ]],
                       [[ (0, 0), (1, 0), (1, 1), (0, 1) ]]])
    region = HeatRegion('actual-tank', BoxEnvelope(0, 10, 0, 10, 0, 10), True, EVIDENCE,
                        actual_geometry=shape, source_kind='heated_tank')
    geometry = PlacementGeometry(heat_sources=(region,), heat_map_complete=True, heat_map_provenance=EVIDENCE)
    box = BoxEnvelope(4.9, 5.1, 8, 9, 8, 9)
    assert any_admissible_vertical_placement(box, geometry, 8, 8, provision='SW1',
        on_deck=False, sunlight=SHELTERED_CTU, context=CONTEXT,
        registry=registry_for(EVIDENCE)) is True


def test_detached_actual_shape_cannot_hide_behind_a_far_declared_bound():
    from stowage.model.vessel import BoxSourceGeometry
    from stowage.rules.imdg.placement_assessments import assess_sw1
    shape = BoxSourceGeometry(x_min_m=0, x_max_m=10, y_min_m=0, y_max_m=10, z_min_m=0, z_max_m=10)
    region = HeatRegion('detached-shape', BoxEnvelope(20, 30, 0, 10, 0, 10), True, EVIDENCE,
                        actual_geometry=shape, source_kind='heated_tank')
    geometry = PlacementGeometry(heat_sources=(region,), heat_map_complete=True, heat_map_provenance=EVIDENCE)
    assert assess_sw1(BoxEnvelope(1, 2, 1, 2, 1, 2), geometry, on_deck=False,
        sunlight=SHELTERED_CTU, context=CONTEXT, registry=registry_for(EVIDENCE)) is None
