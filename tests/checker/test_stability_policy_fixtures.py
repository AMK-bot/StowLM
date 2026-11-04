"""Explicit synthetic loading policy for isolated stability tests.

These broad test-only intervals isolate numerical/criterion behavior. They are
independent supplied policy, not values inferred from hydrostatic endpoints or
approved limits for the generated fleet. D40 supplies them on the hull;
existing populated production policy is retained. Missing-policy tests use the
original model fixture directly.
"""
from stowage.model.fixtures import build_instance as _build_instance
from stowage.model import VesselFieldProvenance


def supply_loading_policy(instance):
    from stowage.vessel import fleet
    vessel = instance.vessel
    identities = [value for value in (getattr(vessel, 'id', None),
                                      getattr(vessel, 'slice_of', None)) if isinstance(value, str)]
    sources = getattr(vessel, 'sources', ())
    sources = (sources,) if isinstance(sources, str) else sources
    fleet_claim = (isinstance(sources, (list, tuple)) and any(
        isinstance(source, str) and source.startswith('params/fleet/') for source in sources))
    if fleet_claim or any(identity == name or identity.startswith(name+'.')
                          for name in fleet.list_fleet() for identity in identities):
        # S5: missing fleet data/evidence must reach the public canonical gate
        # unchanged. Synthetic defaults cannot stand in for its public builder.
        return instance
    # ASSUMPTION: these isolated synthetic test conditions permit signed trim
    # down to -10 m, displacement from 1 kg through 1e9 kg, and no bilge/bar
    # keels. These declarations fill unknown fixture data only; actual built
    # vessels retain their own D40 values and evidence.
    hull = instance.vessel.hull
    for field, fallback in [('min_trim_m', -10.), ('max_trim_m', 2.),
                            ('min_displacement_kg', 1.), ('max_displacement_kg', 1e9),
                            ('bilge_keel_area_m2', 0.)]:
        if getattr(hull, field, None) is None:
            setattr(hull, field, fallback)
        if getattr(hull, field+'_provenance', None) is None:
            setattr(hull, field+'_provenance', VesselFieldProvenance(
                assumption='Explicit isolated synthetic stability-test policy; not an approved fleet loading manual'))
    return instance


def build_instance(**kwargs):
    return supply_loading_policy(_build_instance(**kwargs))
