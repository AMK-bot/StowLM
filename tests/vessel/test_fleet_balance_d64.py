"""D64 rev3 reserves real HEEL water and preserves its dedicated authority."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel import fleet_balance as balance
from stowage.vessel.fleet import load_ship_params


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_stability_ballast_reserves_explicit_half_full_heel_pair(name):
    params = load_ship_params(name)
    before = deepcopy(params)
    pair = next(row for row in params['tank_pairs'] if row['role'] == 'anti_heeling')
    ids = {pair['port_tank'], pair['starboard_tank']}
    with rule_freeze_v1(enabled=False):
        legacy = balance.stability_ballast_masses(params)
    with rule_freeze_v1(enabled=True):
        active = balance.stability_ballast_masses(params)
    for tank in params['tanks']:
        if tank['id'] in ids:
            assert active[tank['id']] == .5*tank['capacity_m3']*tank['content_density_kg_m3']
            assert legacy[tank['id']] == 0.
        elif tank['adjustable']:
            assert active[tank['id']] == legacy[tank['id']]
    assert sum(active.values()) > sum(legacy.values())
    assert params == before


def _synthetic_fit(monkeypatch):
    geometry = SimpleNamespace(density_kg_m3=1025., submerged=lambda aft, fwd: SimpleNamespace(
        displacement_kg=aft*1e6, lcb_m=50., tcb_m=0.))
    monkeypatch.setattr(balance, '_hull', lambda params: geometry)
    tanks = []
    for name, x, y in [('F-P', 80., 2.), ('F-S', 80., -2.),
                       ('A-P', 20., 2.), ('A-S', 20., -2.),
                       ('STABILIZER-L', 50., 5.), ('STABILIZER-R', 50., -5.)]:
        tanks.append(dict(id=name, adjustable=True, kind='ballast', capacity_m3=100.,
                          content_density_kg_m3=1000.,
                          table=[dict(volume_m3=v, lcg_m=x, tcg_m=y, vcg_m=1., fsm_m4=0.)
                                 for v in (0., 100.)]))
    return dict(hull=dict(design_draught_m=7.), tanks=tanks,
                tank_pairs=[dict(id='declared-authority', role='anti_heeling',
                                 port_tank='STABILIZER-L', starboard_tank='STABILIZER-R')])


def test_fixed_half_heel_bounds_follow_pair_metadata_not_tank_names(monkeypatch):
    params = _synthetic_fit(monkeypatch)
    with rule_freeze_v1(enabled=True):
        recipe, audit = balance._fit_symmetric_ballast(params, [(50., 0., 4., 1e6)], 1.25e6,
            keep_double_bottoms_full=False, balance_transverse=True)
    assert audit['status'] == 'sized'
    assert recipe['STABILIZER-L'] == 50_000.
    assert recipe['STABILIZER-R'] == 50_000.
    assert recipe['F-P'] == recipe['F-S']
    assert recipe['A-P'] == recipe['A-S']
    assert sum(recipe.values()) == pytest.approx(250_000., abs=.05)


def test_nonheel_asymmetry_cannot_fabricate_witness_beyond_heel_authority(monkeypatch):
    params = _synthetic_fit(monkeypatch)
    points = [(50., .65, 4., 1e6)]
    with rule_freeze_v1(enabled=False):
        legacy, old_audit = balance._fit_symmetric_ballast(params, points, 1.25e6,
            keep_double_bottoms_full=False, balance_transverse=True)
    assert legacy is not None and old_audit['status'] == 'sized'
    with rule_freeze_v1(enabled=True):
        recipe, audit = balance._fit_symmetric_ballast(params, points, 1.25e6,
            keep_double_bottoms_full=False, balance_transverse=True)
    assert recipe is None
    assert audit['status'] != 'sized'


def test_missing_explicit_heel_pair_fails_closed_when_activated():
    params = load_ship_params('feeder_500')
    params['tank_pairs'] = []
    with rule_freeze_v1(enabled=True), pytest.raises(ValueError, match='anti-heeling'):
        balance.stability_ballast_masses(params)


def test_off_center_booking_uses_heel_split_while_preserving_pair_total(monkeypatch):
    params = _synthetic_fit(monkeypatch)
    points = [(50., .01, 4., 1e6)]
    with rule_freeze_v1(enabled=True):
        recipe, audit = balance._fit_symmetric_ballast(params, points, 1.25e6,
            keep_double_bottoms_full=False, balance_transverse=True)
    assert recipe is not None, audit
    assert audit['status'] == 'sized'
    assert recipe['STABILIZER-L'] < 50_000.
    assert recipe['STABILIZER-R'] > 50_000.
    assert recipe['STABILIZER-L'] + recipe['STABILIZER-R'] == pytest.approx(100_000., abs=1e-8)
    assert recipe['F-P'] == recipe['F-S']
    assert recipe['A-P'] == recipe['A-S']
    transverse = 10_000. + 5*(recipe['STABILIZER-L']-recipe['STABILIZER-R'])
    assert transverse == pytest.approx(0., abs=5.)


def test_seagoing_witness_uses_heel_as_ordinary_ballast_without_port_total(monkeypatch):
    """D64 rev3.1: sea ballast capacity cannot inherit the berth pair total."""
    params = _synthetic_fit(monkeypatch)
    points = [(50., 0., 4., 1e6)]
    with rule_freeze_v1(enabled=True):
        port_recipe, _ = balance._fit_symmetric_ballast(params, points, 1.55e6,
            keep_double_bottoms_full=False)
        sea_recipe, audit = balance._fit_symmetric_ballast(params, points, 1.55e6,
            keep_double_bottoms_full=False, ballast_context='seagoing_witness')
    assert port_recipe is None  # 400 t ordinary + 100 t fixed HEEL is insufficient.
    assert sea_recipe is not None, audit
    assert sum(sea_recipe.values()) == pytest.approx(550_000., abs=.05)
    assert sea_recipe['STABILIZER-L']+sea_recipe['STABILIZER-R'] > 100_000.
    assert audit['ballast_context'] == 'seagoing_witness'
    assert not audit['d64_heel_pair_total_enforced']
    assert sea_recipe['F-P'] == sea_recipe['F-S']
    assert sea_recipe['A-P'] == sea_recipe['A-S']


def test_empty_background_witness_requests_explicit_seagoing_context(monkeypatch):
    params = _synthetic_fit(monkeypatch)
    params['hull'].update(lightship_mass_kg=1e6, lightship_lcg_m=50., lightship_vcg_m=4., beam_m=4.)
    contexts = []
    def fit(params, points, target, **kwargs):
        contexts.append(kwargs.get('ballast_context'))
        return {'STABILIZER-L': 75_000., 'STABILIZER-R': 75_000.}, {'status': 'sized'}
    monkeypatch.setattr(balance, '_fit_symmetric_ballast', fit)
    full = SimpleNamespace(hull=SimpleNamespace(max_displacement_kg=2e6), voyage_policy=None)
    with rule_freeze_v1(enabled=True):
        _, audit = balance.empty_background_ballast_witness(full, params, [], {})
    assert contexts == ['seagoing_witness']
    assert audit['witness_is_booking_initializer'] is False


def test_sea_witness_tanks_never_replace_actual_half_full_arrival_or_booking_budget():
    import json
    from stowage.model.fixtures import build_instance
    from stowage.model.vessel import TankPair
    from stowage.vessel.fleet_conditions import (
        CONDITION_PREFIX, CONDITION_VERSION, departure_tank_masses,
        empty_background_witness_tank_masses, slice_condition,
    )
    vessel = build_instance().vessel
    vessel.tank_pairs = [TankPair(id='explicit-berth-authority', port_tank='WB-P', starboard_tank='WB-S')]
    capacity = {tank.id: tank.capacity_m3*tank.content_density_kg_m3 for tank in vessel.tanks}
    booking_budget = {'remaining_displacement_headroom_kg': 123_456., 'empty_witness_ballast_included': False}
    audit = dict(version=CONDITION_VERSION, departure_ballast_masses_kg={'WB-P': capacity['WB-P']/2,
                  'WB-S': capacity['WB-S']/2}, empty_background_witness_ballast_masses_kg={
                  'WB-P': capacity['WB-P'], 'WB-S': capacity['WB-S']}, booking_departure_budget=booking_budget)
    vessel.sources.append(CONDITION_PREFIX+json.dumps(audit))
    before = vessel.model_dump_json()
    with rule_freeze_v1(enabled=True):
        actual = departure_tank_masses(vessel)
        witness = empty_background_witness_tank_masses(vessel)
        assert departure_tank_masses(vessel) == actual
    for tid in ('WB-P', 'WB-S'):
        assert actual[tid] == capacity[tid]/2
        assert witness[tid] == capacity[tid]
    assert witness['FO-1'] == actual['FO-1']
    assert slice_condition(vessel)['booking_departure_budget'] == booking_budget
    assert vessel.model_dump_json() == before
