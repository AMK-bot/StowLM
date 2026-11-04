"""Cached dry moments retain the checker's exact liquid bookkeeping."""
from time import monotonic
import json

import numpy as np
import pytest

from stowage.checker import stability
from stowage.checker.stability_loading import loading_condition
from stowage.model import ShipState, VesselFieldProvenance
from stowage.model.vessel import FixedLoad
from stowage.model.fixtures import build_instance
from stowage.solver.ballast_deadline import ExactDeadline
from stowage.solver.ballast_physics import ExactContext
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.tanks import build_tanks
from stowage.vessel.hull import _field_evidence, _load_line_draught


@pytest.fixture
def context(monkeypatch):
    instance = build_instance()
    params = load_ship_params('feeder_500')
    fixture_hull = instance.vessel.hull
    vessel = load_vessel('feeder_500', with_hull=False).model_copy(deep=True)
    fields = {key: params['hull'][key] for key in type(fixture_hull).model_fields
              if key in params['hull']}
    fields.update(params['operating_limits'])
    fields['lightship_distribution'] = [tuple(row) for row in fields['lightship_distribution']]
    fields['max_draught_m'] = params['hull']['scantling_draught_m']
    vessel.hull = fixture_hull.model_copy(update=fields)
    # Synthetic rectangular tanks isolate bookkeeping and unstable-GM recovery
    # from concurrent fleet tank-layout changes. Their solids are used exactly.
    length = vessel.hull.lpp_m
    params['tanks'] = [dict(id='TEST-FUEL', kind='fuel_oil',
        content_density_kg_m3=850., capacity_m3=.1*length,
        x_aft_m=.4*length, x_fore_m=.5*length, adjustable=False,
        geometry=dict(shape='rectangular_prism', x_min_m=.4*length,
            x_max_m=.5*length, y_min_m=0., y_max_m=1., z_min_m=1., z_max_m=2.))]
    for index in range(10):
        aft, fore = (.2+.06*index)*length, (.2+.06*(index+1))*length
        for side, left, right in [('P', .5, 4.5), ('S', -4.5, -.5)]:
            params['tanks'].append(dict(id=f'TEST-{index:02d}-'+side, kind='ballast',
                content_density_kg_m3=1025., capacity_m3=(fore-aft)*4.*3.,
                x_aft_m=aft, x_fore_m=fore, adjustable=True,
                geometry=dict(shape='rectangular_prism', x_min_m=aft,
                    x_max_m=fore, y_min_m=left, y_max_m=right,
                    z_min_m=.5, z_max_m=3.5)))
    vessel.tanks = build_tanks(params)
    geometry, _ = stability._context(vessel)
    # Copy the actual synthetic production policy, including independent
    # evidence; do not manufacture broad bounds from fixture table endpoints.
    for field in ('min_trim_m', 'max_trim_m'):
        setattr(vessel.hull, field+'_provenance', _field_evidence(params, field))
    vessel.hull.min_displacement_kg = params['hull']['lightship_mass_kg']
    vessel.hull.min_displacement_kg_provenance = _field_evidence(params, 'lightship_mass_kg')
    draught = _load_line_draught(params)
    vessel.hull.max_displacement_kg = geometry.full_submerged(
        draught, draught, 0., params['hull']['water_density_kg_m3']).displacement_kg
    vessel.hull.max_displacement_kg_provenance = VesselFieldProvenance(
        source='DERIVED: production polygon displacement at declared load_line waterline; '+
               json.dumps(params['load_line'], sort_keys=True),
        assumption=params['load_line']['assumption'])
    monkeypatch.setattr(stability, '_context', lambda vessel: (geometry, params))
    instance.vessel = vessel
    ship = ShipState(vessel_id=vessel.id, tank_masses_kg={
        tank.id: .3*tank.capacity_m3*tank.content_density_kg_m3
        for tank in vessel.tanks if not tank.adjustable})
    instance.onboard = ship
    return ExactContext(instance, ship, monotonic()+10.)


@pytest.mark.parametrize('fraction', [0., .35, 1.])
def test_cached_condition_matches_full_cargo_and_tank_builder(context, fraction):
    assert len(context.tanks) == 20
    fills = np.full(len(context.tanks), fraction)
    actual = context.condition(fills)
    ship = context.ship.model_copy(update={'tank_masses_kg': {
        **context.fixed_ship.tank_masses_kg,
        **{t.id: float(m) for t, m in zip(context.tanks, fills*context.capacity)}}})
    reference = loading_condition(context.instance, ship, geometry=context.geometry,
                                  ship_params=context.params,
                                  free_surface_method=context.method,
                                  precomputed_soundings=True, small_tank_exemption=True)
    for field in ('mass_kg', 'lcg_m', 'tcg_m', 'kg_m', 'fsc_m', 'fsm_kgm'):
        assert getattr(actual, field) == pytest.approx(getattr(reference, field), abs=1e-8)
    assert sorted(actual.tanks, key=lambda t: t.id) == sorted(reference.tanks, key=lambda t: t.id)
    assert sorted(actual.actual_tanks, key=lambda t: t.id) == sorted(reference.actual_tanks, key=lambda t: t.id)
    assert sorted(actual.omitted_tanks) == sorted(reference.omitted_tanks)
    assert actual.precomputed_soundings
    assert actual.cg_cache is not context.base.cg_cache
    assert actual.upright_cache is not context.base.upright_cache


def test_probe_obeys_expired_deadline_before_equilibrium(context):
    context.deadline = monotonic()-1.
    with pytest.raises(ExactDeadline):
        context.margins(np.zeros(len(context.tanks)))
    with pytest.raises(ExactDeadline):
        context.equilibrium(np.zeros(len(context.tanks)))


def test_signed_equilibrium_uses_same_loading_solution_as_margins(context):
    fills = np.zeros(len(context.tanks))
    eq = context.equilibrium(fills)
    margins = context.margins(fills, {'stability/trim_m', 'stability/list_rad'})
    hull = context.instance.vessel.hull
    assert margins['stability/trim_m'] == pytest.approx(hull.max_trim_m-eq.trim_m)
    assert margins['stability/list_rad'] == pytest.approx(
        (hull.max_list_rad-abs(eq.heel_rad))/hull.max_list_rad)


def test_solid_memo_keys_exact_parameter_content():
    from stowage.solver.ballast_physics import _adjustable_solids
    geometry = dict(shape='rectangular_prism', x_min_m=0., x_max_m=2.,
                    y_min_m=0., y_max_m=1., z_min_m=0., z_max_m=1.)
    params = {'tanks': [{'id': 'T', 'geometry': geometry}]}
    payload = json.dumps(params, sort_keys=True)
    first, = _adjustable_solids(payload, ('T',))
    assert _adjustable_solids(payload, ('T',))[0] is first
    # Same vessel/tank names, and same serialized length, are insufficient.
    geometry['x_max_m'] = 3.
    changed = json.dumps(params, sort_keys=True)
    assert len(changed) == len(payload)
    second, = _adjustable_solids(changed, ('T',))
    assert second is not first
    assert second.capacity_m3 == 3.


def test_method_two_condition_matches_production_policy(context, monkeypatch):
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '2')
    alternative = ExactContext(context.instance, context.ship, monotonic()+10.)
    fills = np.full(len(alternative.tanks), .35)
    actual = alternative.condition(fills)
    ship = alternative.ship.model_copy(update={'tank_masses_kg': {
        **alternative.fixed_ship.tank_masses_kg,
        **{tank.id: float(mass) for tank, mass in zip(
            alternative.tanks, fills*alternative.capacity)}}})
    reference = loading_condition(alternative.instance, ship,
        geometry=alternative.geometry, ship_params=alternative.params,
        free_surface_method=2, precomputed_soundings=True, small_tank_exemption=True)
    assert actual.fsm_kgm == pytest.approx(reference.fsm_kgm)
    assert actual.kg_m == pytest.approx(reference.kg_m)
    assert sorted(actual.omitted_tanks) == sorted(reference.omitted_tanks)
    assert actual.liquid_tables is None


def test_loading_margins_cover_active_keys_only(context):
    # A physical upright reference at design displacement, with low fixed cargo.
    # This checks the direct equilibrium reference, so use the same polygon
    # geometry for the reference displacement and the repair probe. Qualified
    # KN tables have their own interpolation-error contract.
    submerged = context.direct_geometry.full_submerged(5.5, 5.5)
    hull = context.instance.vessel.hull
    mass = submerged.displacement_kg-hull.lightship_mass_kg
    x = (submerged.displacement_kg*submerged.lcb_m-hull.lightship_mass_kg*hull.lightship_lcg_m)/mass
    context.instance.vessel.fixed_loads = [FixedLoad(x_m=x, y_m=0., z_m=4.5, mass_kg=mass)]
    ship = ShipState(vessel_id=context.instance.vessel.id)
    balanced = ExactContext(context.instance, ship, monotonic()+10.)
    balanced.geometry = balanced.direct_geometry
    margins = balanced.margins(np.zeros(len(balanced.tanks)),
                               {'stability/trim_m', 'stability/list_rad'})
    assert set(margins) == {'stability/trim_m', 'stability/list_rad'}
    assert margins['stability/trim_m'] == pytest.approx(hull.max_trim_m, abs=1e-4)
    assert margins['stability/list_rad'] == pytest.approx(1., abs=1e-4)


def test_negative_gm_remains_available_when_list_equilibrium_fails(context, monkeypatch):
    # A synthetic top-heavy cargo stow has negative initial stiffness. Its
    # upright GM remains measurable even if the heeled solver cannot converge.
    submerged = context.geometry.full_submerged(5.5, 5.5)
    hull = context.instance.vessel.hull
    mass = submerged.displacement_kg-hull.lightship_mass_kg
    x = (submerged.displacement_kg*submerged.lcb_m-hull.lightship_mass_kg*hull.lightship_lcg_m)/mass
    context.instance.vessel.fixed_loads = [FixedLoad(x_m=x, y_m=0., z_m=30., mass_kg=mass)]
    unstable = ExactContext(context.instance, ShipState(vessel_id=context.instance.vessel.id),
                            monotonic()+10.)

    def failed_equilibrium(*args, **kwargs):
        raise ValueError('unstable heeled equilibrium')

    monkeypatch.setattr(unstable.geometry, 'equilibrium', failed_equilibrium)
    empty = unstable.margins(np.zeros(len(unstable.tanks)))
    full = unstable.margins(np.ones(len(unstable.tanks)))
    assert empty['stability/gm_m'] < 0.
    assert full['stability/gm_m'] > empty['stability/gm_m']
    assert set(empty) == {'stability/gm_m', 'stability/displacement_minimum_kg',
                          'stability/displacement_maximum_kg'}
    assert not any('draught' in key or 'list' in key for key in full)


def test_nonnegative_gm_does_not_hide_unavailable_equilibrium(context, monkeypatch):
    def failed_equilibrium(*args, **kwargs):
        raise ValueError('no heeled solution')

    # Force the direct reference before injecting a failed solve: a qualified
    # cache hit must not bypass the failure this test intends to exercise.
    context.geometry = context.direct_geometry
    monkeypatch.setattr(context.direct_geometry, 'equilibrium', failed_equilibrium)
    result = context.margins(np.zeros(len(context.tanks)))
    assert result['stability/gm_m'] >= 0.
    assert result['stability/equilibrium_unavailable'] < 0.


def test_initial_gm_code_override_is_included_in_repair_margin(context):
    fills = np.zeros(len(context.tanks))
    original = context.margins(fills, {'stability/gm_m'})['stability/gm_m']
    original_floor = max(context.instance.vessel.hull.min_gm_m, .15)
    context.instance.rule_overrides['iscode.a2_2.initial_gm'] = {'minimum': 2.5}
    updated = context.margins(fills, {'stability/gm_m'})['stability/gm_m']
    assert updated == pytest.approx(original+original_floor-2.5)


def test_twenty_slack_tank_gm_probe_warm_cost(context, record_property):
    assert len(context.tanks) == 20
    fills = np.full(20, .35)
    # The same exact liquid/FSM calculation used for repair, with a real hull
    # and twenty independently adjustable slack tanks, should be a cheap probe.
    context.margins(fills, {'stability/gm_m'})
    started = monotonic()
    result = context.margins(fills, {'stability/gm_m'})
    elapsed = monotonic()-started
    record_property('warm_twenty_tank_gm_probe_s', elapsed)
    assert np.isfinite(result['stability/gm_m'])
    assert elapsed < 1.


def test_weather_applicability_probes_only_requested_continuous_keys(context):
    keys = {'stability/weather_beam_draught', 'stability/weather_kg_lower',
            'stability/weather_kg_upper', 'stability/weather_period',
            'stability/weather_r'}
    result = context.margins(np.zeros(len(context.tanks)), keys)
    assert set(result) == keys
    assert all(np.isfinite(value) for value in result.values())
    # The two KG/d eligibility clearances span exactly the Code's .8 band.
    assert result['stability/weather_kg_lower']+result['stability/weather_kg_upper'] == pytest.approx(.8)


def test_fast_probe_applies_signed_lower_trim_and_tightened_upper_policy(context, monkeypatch):
    from types import SimpleNamespace
    context.instance.rule_overrides['iscode.loading.trim'] = {'minimum': -.25, 'maximum': .5}
    monkeypatch.setattr(context, '_condition_equilibrium', lambda condition: SimpleNamespace(
        trim_m=-.6, heel_rad=0., t_aft_m=4., t_fwd_m=4.6))
    margins = context.margins(np.zeros(len(context.tanks)),
                             {'stability/trim_m', 'stability/trim_min_m'})
    assert margins == {'stability/trim_m': pytest.approx(1.1),
                       'stability/trim_min_m': pytest.approx(-.35)}


def test_fast_probe_applies_displacement_policy_without_equilibrium(context, monkeypatch):
    fills = np.zeros(len(context.tanks))
    mass = context.condition(fills).mass_kg
    context.instance.rule_overrides['iscode.loading.displacement'] = {
        'minimum': mass+1000., 'maximum': mass+2000.}
    def forbidden(*args, **kwargs):
        pytest.fail('mass balance does not require equilibrium')
    monkeypatch.setattr(context, '_condition_equilibrium', forbidden)
    margins = context.margins(fills, {'stability/displacement_minimum_kg',
                                     'stability/displacement_maximum_kg'})
    assert margins == {'stability/displacement_minimum_kg': pytest.approx(-1000./mass),
                       'stability/displacement_maximum_kg': pytest.approx(2000./mass)}


@pytest.mark.parametrize('failure', ['missing-evidence', 'missing-endpoint', 'weakening'])
def test_fast_probe_rejects_unknown_or_weakened_loading_policy(context, failure):
    if failure == 'missing-evidence':
        context.instance.vessel.hull.min_trim_m_provenance = None
    elif failure == 'missing-endpoint':
        context.instance.vessel.hull.max_displacement_kg = None
    else:
        context.instance.rule_overrides['iscode.loading.trim'] = {
            'minimum': context.instance.vessel.hull.min_trim_m-1.}
    with pytest.raises(ValueError):
        context.margins(np.zeros(len(context.tanks)), {'stability/trim_m'})
