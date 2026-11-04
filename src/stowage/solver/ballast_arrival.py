"""D57 voyage samples for ballast search, derived solely from departure."""
from __future__ import annotations

import math

from stowage.model.voyage import (arrival_remaining_fraction,
                                 derive_arrival_liquid_state,
                                 derive_voyage_liquid_state)


def arrival_condition(instance, departure):
    """Return an isolated arrival physics context and derived ship state.

    A voyage's stable environment/density identities remain on ``voyage``.
    The schema declares water density, but has no next-port berth profile.
    As in the full D50 checker, arrival applies vessel seagoing limits;
    current-port berth/channel limits do not describe another LOCODE.
    No missing voyage evidence can authorize departure-only acceptance.
    """
    spec = instance.voyage
    if spec is None:
        raise ValueError('D50 arrival requires an explicit voyage specification')
    if (spec.departure.call_index != instance.call_index
            or spec.departure.locode != instance.port.locode
            or not 0 <= spec.departure.call_index < len(instance.rotation.calls)
            or instance.rotation.calls[spec.departure.call_index].locode != spec.departure.locode
            or not math.isclose(spec.departure.water_density_kg_m3,
                                instance.port.water_density_kg_m3, rel_tol=0., abs_tol=1e-9)):
        raise ValueError('D50 departure identity does not match the current call/profile')
    if (spec.arrival.call_index != instance.call_index+1
            or spec.arrival.call_index >= len(instance.rotation.calls)
            or instance.rotation.calls[spec.arrival.call_index].locode != spec.arrival.locode):
        raise ValueError('D50 arrival identity does not match the immediate next call')
    if spec.arrival.id == f'{spec.departure.id}:mid_voyage':
        raise ValueError('D57 midpoint condition ID collides with arrival.id')
    # IS Code Part B 3.4.1.2-.3, data/raw/is-code/msc267-85.pdf, PDF p41:
    # arrival "with 10 % stores and fuel remaining". The model helper applies
    # D50's declared per-group default/consumption and forbidden-ballast rule.
    liquids = derive_arrival_liquid_state(departure, spec, vessel=instance.vessel)
    ship = departure.model_copy(update={'tank_masses_kg': liquids.tank_masses_kg})
    port = instance.port.model_copy(update={'locode': liquids.condition.locode,
        'water_density_kg_m3': liquids.condition.water_density_kg_m3,
        'max_draught_m': None, 'port_rules': []})
    return instance.model_copy(update={'port': port, 'call_index': liquids.condition.call_index,
                                       'onboard': ship}), ship


def mid_voyage_condition(instance, departure):
    """Derive the same proportional midpoint and environment as the full checker.

    APPROXIMATION: D57 samples the consumable path at departure, midpoint and
    arrival; it does not search all continuous-voyage states. The midpoint
    retains the declared departure density and has no current-berth limits.
    Source: checker.full._voyage; IACS UR S11A 2.2.2 requires considering an
    intermediate voyage stage that may be more severe than the endpoints.
    """
    # Validate the identical endpoint evidence before deriving another sample.
    arrival_condition(instance, departure)
    spec = instance.voyage
    fraction = (1. + arrival_remaining_fraction(departure, spec, vessel=instance.vessel))/2.
    liquids = derive_voyage_liquid_state(departure, spec, fraction, vessel=instance.vessel)
    ship = departure.model_copy(update={'tank_masses_kg': dict(liquids.tank_masses_kg)})
    port = instance.port.model_copy(update={'max_draught_m': None, 'port_rules': []})
    return instance.model_copy(update={'port': port, 'onboard': ship}), ship
