"""Synthetic test defaults must not manufacture known-fleet manual evidence."""
import pytest

from stowage.model import VesselFieldProvenance
from stowage.model.fixtures import build_instance
from tests.checker.test_stability_policy_fixtures import supply_loading_policy


@pytest.mark.parametrize('identity', ['known_id', 'known_root', 'prefixed_slice', 'fleet_source',
                                      'unknown_fleet_source'])
def test_synthetic_policy_helper_never_fills_known_fleet_data(identity):
    instance = build_instance()
    vessel, hull = instance.vessel, instance.vessel.hull
    if identity == 'known_id':
        vessel.id, vessel.slice_of = 'feeder_500', None
    elif identity == 'known_root':
        vessel.id, vessel.slice_of = 'custom_nested_slice', 'feeder_500'
    elif identity == 'prefixed_slice':
        vessel.id, vessel.slice_of = 'feeder_500.slice_b01-b03', None
    else:
        root = 'feeder_500' if identity == 'fleet_source' else 'unavailable_fleet'
        vessel.sources = [f'params/fleet/{root}.toml sha256:'+'0'*64]
    for field in ('min_trim_m', 'max_trim_m', 'min_displacement_kg',
                  'max_displacement_kg', 'bilge_keel_area_m2'):
        setattr(hull, field, None)
        setattr(hull, field+'_provenance', None)
    before = hull.model_dump(mode='python')
    assert supply_loading_policy(instance) is instance
    assert hull.model_dump(mode='python') == before
    assert hull.bilge_keel_area_m2 is None
    assert hull.bilge_keel_area_m2_provenance is None


def test_complete_known_fleet_policy_is_returned_unchanged():
    instance = build_instance()
    instance.vessel.id, instance.vessel.slice_of = 'feeder_500', None
    hull = instance.vessel.hull
    values = {'min_trim_m': -1., 'max_trim_m': 1., 'min_displacement_kg': 4e6,
              'max_displacement_kg': 7e6, 'bilge_keel_area_m2': 0.}
    for field, value in values.items():
        setattr(hull, field, value)
        setattr(hull, field+'_provenance', VesselFieldProvenance(
            assumption=f'Independent declared test baseline for {field}'))
    before = instance.model_dump(mode='python')
    assert supply_loading_policy(instance) is instance
    assert instance.model_dump(mode='python') == before


def test_foreign_synthetic_policy_retains_explicit_fallback_and_existing_values():
    instance = build_instance()
    hull = instance.vessel.hull
    hull.max_trim_m = .7
    evidence = VesselFieldProvenance(assumption='Existing explicit synthetic upper bound')
    hull.max_trim_m_provenance = evidence
    assert supply_loading_policy(instance) is instance
    assert hull.max_trim_m == .7
    assert hull.max_trim_m_provenance is evidence
    assert hull.min_trim_m == -10.
    assert hull.min_displacement_kg == 1.
    assert hull.max_displacement_kg == 1e9
    assert hull.bilge_keel_area_m2 == 0.
    assert hull.bilge_keel_area_m2_provenance.assumption
