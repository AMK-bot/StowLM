"""Prepare a reusable linear response before generating any bookings.

A vessel's hull/tank response does not depend on its cargo manifest or fixed
background point weights. This hook constructs an empty planning context from
the shared fixture builder, then warms the same production family calibration
used by ``build_linear_model``. It does not approve a departure. Method-1 liquid
transfer tables are prepared through the central stability dependency builder
when they are not already independently qualified.

Source: weight/buoyancy superposition, Biran & López-Pulido (2014), chapters 2–4.
"""
from __future__ import annotations

import time


def _empty_context(vessel):
    from stowage.model import ShipState, Vessel
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_vessel
    if isinstance(vessel, str):
        vessel = load_vessel(vessel, with_hull=True)
    if not isinstance(vessel, Vessel):
        raise TypeError('vessel must be a Vessel or fleet name')
    if vessel.hull is None:
        parent = load_vessel(vessel.slice_of or vessel.id, with_hull=True)
        vessel = vessel.model_copy(update={'hull': parent.hull})
    if not vessel.hull.stations:
        raise ValueError('linear cache requires permissible strength stations')
    # Fixed backgrounds and all booking data are deliberately absent. The
    # remaining layout is retained; coefficient keys choose its physical inputs.
    vessel = vessel.model_copy(update={'fixed_loads': []})
    base = build_instance()
    data = base.model_dump(mode='python')
    data.update(id='linear-cache-warm', vessel=vessel, containers=[], load_list=[],
                discharge_list=[], forecasts=[], plan_calls=[], plan_constraints=[],
                rule_overrides={}, onboard=ShipState(vessel_id=vessel.id),
                yard=base.yard.model_copy(update={'stacks': [], 'quay_buffer': []}))
    # The vessel-only API uses standard seawater. A caller with a different dock
    # density needs that density's distinct family through build_linear_model.
    data['port'] = base.port.model_copy(update={'water_density_kg_m3': 1025.})
    from stowage.model import Instance
    return Instance.model_validate(data)


def build_cache(vessel, *, workers=1):
    """Warm production coefficients/calibration, independently of bookings.

    ``vessel`` accepts a loaded full/slice Vessel or a fleet name. Missing hulls
    are loaded from its declared parent. Results describe cache work, not a
    checker-approved loading. ``workers`` is a positive concurrency budget; this
    deterministic per-vessel hook uses one worker. A central warm-up command may
    call it for distinct vessels in parallel within its admitted global budget.
    A known production profile whose measured policy bank is stale is reported
    incomplete through a qualification error; warm-up never silently runs a
    fresh unqualified 192-state calibration for that profile.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError('workers must be a positive integer')
    started = time.perf_counter()
    instance = _empty_context(vessel)
    from .physics import _params_for
    from .reusable import coefficient_family, family_band
    from .family_calibration import (calibrated_family, domain_for,
                                     reference_inputs, reference_cache_identity)
    from .cache import (coefficient_family_key, family_calibration_key,
                        load_model, load_payload)
    name, parameters = _params_for(instance)
    reference = reference_inputs(parameters)
    dependency_reports=[]
    if (reference.get('method') == 1 and not reference.get('direct')
            and not reference.get('qualified', False)):
        # Explicit warm-up is allowed to build dependencies; normal model builds
        # never invoke this adapter. The central command currently visits the
        # linear hook before its stability phase, so this serial owner adapter
        # makes a cold invocation complete without extra worker processes.
        from stowage.cache_warm import _warm_stability
        dependency_reports=_warm_stability(name,parameters)
        reference=reference_inputs(parameters)
        if not reference.get('qualified',False):
            raise ValueError('stability warm-up did not qualify method-1 liquid tables')
    band = family_band(instance, parameters)
    coefficient_key = coefficient_family_key(instance, parameters, band)
    coefficient_reused = load_model(coefficient_key) is not None
    family = coefficient_family(instance, parameters, band)
    # Gold-plan engines normally start with the declared consumable departure
    # fills. Build their exact upright weight integrals here, once per actual
    # solid/fill/station content key, before thousands of instance constants.
    from stowage.vessel import departure_tank_masses
    from .reusable import _fixed_tank_integrals
    departure = departure_tank_masses(instance.vessel)
    fixed_tanks = []
    for tank in instance.vessel.tanks:
        mass = departure.get(tank.id, 0.)
        if tank.adjustable or mass <= 0:
            continue
        _fixed_tank_integrals(parameters, tank.id, mass/tank.content_density_kg_m3,
                              family.template.station_x_m)
        fixed_tanks.append(tank.id)
    domain = domain_for(family, instance)
    calibration_key = family_calibration_key(family, reference_cache_identity(reference), domain,
                                             samples=192)
    calibration_reused = load_payload(calibration_key, namespace='payload') is not None
    _, diagnostics = calibrated_family(family, instance, samples=192)
    policy = diagnostics.get('margin_policy', {})
    return {'status': 'reused' if coefficient_reused and calibration_reused else 'built',
            'vessel_id': instance.vessel.id, 'workers_requested': workers, 'workers_used': 1,
            'water_density_kg_m3': instance.port.water_density_kg_m3,
            'coefficient_key': coefficient_key, 'coefficient_reused': coefficient_reused,
            'fixed_tank_integrals_prepared': fixed_tanks,
            'calibration_key': calibration_key, 'calibration_reused': calibration_reused,
            'calibration_samples': diagnostics['samples'], 'calibration_domain': domain,
            'reference': reference_cache_identity(reference),
            'dependency_builders':dependency_reports,
            'margin_policy_status': policy.get('status'),
            'margin_policy_sha256': policy.get('sha256'),
            'mandatory_exact_checker': True, 'elapsed_seconds': time.perf_counter()-started}


# Central D30 orchestration uses this descriptive spelling.
warm_cache = build_cache
