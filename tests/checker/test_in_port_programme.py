"""D74 Addendum B pure programme arithmetic and interval-path contracts."""
from importlib import import_module
import math
from types import SimpleNamespace as NS

import pytest


@pytest.fixture
def api():
    return import_module("stowage.checker.in_port_programme")


def parameters(api, **updates):
    values = dict(
        ballast_pump_m3_h=300., heel_transfer_m3_h=150., max_cranes=2,
        port_cranes=2, gross_moves_per_hour=30., pre_operation_allowance_h=.5,
        post_operation_allowance_h=.5, water_density_kg_m3=1025.)
    values.update(updates)
    return api.ProgrammeParameters(**values)


def instance():
    return NS(call_index=0, port=NS(water_density_kg_m3=1025., quay_crane=NS(
        count=2, gross_moves_per_hour=30., pre_operation_allowance_h=.5,
        post_operation_allowance_h=.5)), vessel=NS(ballast_system=NS(
        ballast_pump_m3_h=300., heel_transfer_m3_h=150., max_cranes=2),
        tanks=[NS(id=tid, adjustable=adjustable) for tid, adjustable in
               (("DB-P", True), ("DB-S", True), ("HP", True), ("HS", True), ("fuel", False))],
        tank_pairs=[NS(role="anti_heeling", port_tank="HP", starboard_tank="HS")]),
        onboard=NS(tank_masses_kg={"DB-P": 544_500., "DB-S": 544_500.,
            "HP": 75_000., "HS": 75_000., "fuel": 1e6}))


def test_adapter_uses_production_model_names_and_crane_minimum(api):
    i = instance()
    i.vessel.ballast_system.max_cranes = 3
    p = api.programme_parameters(i)
    assert p.cranes == 2
    assert p.t_lift_s == 60.
    assert p.ballast_kg_per_s == pytest.approx(1025.*300./3600.)
    assert p.heel_step_limit_kg == pytest.approx(1025.*150./60.)
    i.port.quay_crane.count = 3
    assert api.programme_parameters(i).t_lift_s == 40.
    i.port.quay_crane.count = 1
    i.port.quay_crane.gross_moves_per_hour = 25.
    assert api.programme_parameters(i).t_lift_s == 144.


def test_missing_production_rate_data_has_no_synthetic_fallback(api):
    i = instance()
    del i.vessel.ballast_system
    with pytest.raises(ValueError, match="ballast_system"):
        api.programme_parameters(i)


def test_adapter_matches_landed_ballast_system_and_quay_crane_schema(api):
    from stowage.model.port import QuayCraneSpec
    from stowage.model.vessel import BallastSystem
    i = instance()
    i.vessel.ballast_system = BallastSystem(ballast_pump_m3_h=300.,
        heel_transfer_m3_h=150., max_cranes=2,
        assumption="APPROXIMATION: synthetic programme unit case; Q23")
    i.port.quay_crane = QuayCraneSpec(count=2, max_on_deck_tiers=8,
        gross_moves_per_hour=30., pre_operation_allowance_h=.5,
        post_operation_allowance_h=.5)
    p = api.programme_parameters(i)
    assert p.t_lift_s == 60.
    assert p.port_cranes == i.port.quay_crane.count_per_berth


def test_t13_short_call_reports_delay_and_excludes_heel_and_fuel(api):
    i = instance()
    departure = NS(tank_masses_kg={"HP": 0., "HS": 150_000., "fuel": 0.})
    got = api.changeover_programme(i, departure, k=0, lift_count=5)
    assert got.passed and not got.violations
    assert got.metrics["pumped_mass_t"] == 1089.
    assert got.metrics["abs_change_t"] == 1089.
    assert got.metrics["pump_time_required_h"] == pytest.approx(1089./307.5)
    assert got.metrics["pump_time_available_h"] == .5
    assert got.metrics["lifts_after_k"] == 6
    assert got.metrics["ballast_delay_h"] == pytest.approx(1089./307.5-.5)
    # A paused pre-lift changeover still needs both s0 endpoint certificates.
    assert got.window_indices == (0,)
    assert got.window_checks == ((0, "arrival", "departure"), (0, "departure", "departure"))


@pytest.mark.slow
def test_t13_production_feeder_500_short_call_reports_ballast_delay(api, record_property):
    """Use production capacities/rates only; no equilibrium or stability solve.

    Loading the production vessel can build its cache on a cold machine, so
    this arithmetic regression belongs to the explicitly admitted slow set.
    """
    from stowage.ports.profiles import load_port
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.vessel.fleet import load_vessel

    with rule_freeze_v1(enabled=True):
        vessel = load_vessel("feeder_500")
        port = load_port("laem_chabang_like")
        heel_ids = {tid for pair in vessel.tank_pairs if pair.role == "anti_heeling"
                    for tid in (pair.port_tank, pair.starboard_tank)}
        double_bottoms = [tank for tank in vessel.tanks
                          if tank.adjustable and tank.id.startswith("DB-")]
        assert len(heel_ids) == 2 and len(double_bottoms) == 6
        arrival = {tank.id: (.5 if tank.id in heel_ids else .99)
                   * tank.capacity_m3 * tank.content_density_kg_m3
                   for tank in vessel.tanks}
        departure = dict(arrival)
        for tank in double_bottoms:
            departure[tank.id] = 0.
        i = NS(call_index=0, vessel=vessel, port=port,
               onboard=NS(tank_masses_kg=arrival))
        got = api.changeover_programme(i, NS(tank_masses_kg=departure),
                                      k=0, lift_count=5)
        p = api.programme_parameters(i)

    expected_t = sum(.99*tank.capacity_m3*tank.content_density_kg_m3
                     for tank in double_bottoms)/1000.
    # Production capacities, rather than the design's illustrative ~1100t,
    # determine the pumped mass; the amended D74 profile determines the rate.
    assert expected_t == pytest.approx(940.049432, abs=.001)
    assert got.metrics["pumped_mass_t"] == pytest.approx(expected_t)
    assert got.metrics["abs_change_t"] == pytest.approx(expected_t)
    assert got.metrics["pump_time_required_h"] == pytest.approx(
        expected_t*1000./(port.water_density_kg_m3*p.ballast_pump_m3_h))
    assert got.metrics["pump_time_available_h"] == 1.
    assert got.metrics["pump_time_required_h"] > 1.
    assert got.metrics["changeover_k"] == 0
    assert got.metrics["lifts_after_k"] == 6
    assert got.passed and not got.violations
    assert got.metrics["ballast_delay_h"] == pytest.approx(
        got.metrics["pump_time_required_h"]-got.metrics["pump_time_available_h"])
    assert got.window_indices == (0,)
    record_property("pumped_mass_t", got.metrics["pumped_mass_t"])
    record_property("pump_time_required_h", got.metrics["pump_time_required_h"])
    record_property("pump_time_available_h", got.metrics["pump_time_available_h"])
    record_property("ballast_delay_h", got.metrics["ballast_delay_h"])


def test_t14_long_call_reports_exact_ceiling_and_both_window_endpoints(api):
    i = instance()
    i.onboard.tank_masses_kg = {"DB-P": 600_000.}
    got = api.changeover_programme(i, NS(tank_masses_kg={}), k=120, lift_count=450)
    assert got.passed and not got.violations
    assert got.metrics["ballast_delay_h"] == 0.
    assert got.metrics["pump_time_available_h"] == 2.5
    assert got.metrics["pumped_mass_t"] <= .8*1025.*300.*2.5/1000.
    # ceil((600/307.5 - .5)*60) = 88, not the worked example's rounded 87.
    assert got.metrics["pump_window_lifts"] == 88
    assert got.window_indices == tuple(range(32, 120))
    assert got.window_checks == tuple(request for index in range(32, 120)
        for request in ((index, "arrival", "departure"), (index, "departure", "departure")))


def test_t15_window_endpoint_requests_require_worked_fsm_on_both_fills(api):
    i = instance()
    i.onboard.tank_masses_kg = {"DB-P": 200_000.}
    got = api.changeover_programme(i, NS(tank_masses_kg={}), k=10, lift_count=300,
        parameters=parameters(api))
    assert got.passed
    assert got.window_checks
    for index, ballast_phase, fsm_phase in got.window_checks:
        assert index < 10
        assert ballast_phase in {"arrival", "departure"}
        assert fsm_phase == "departure"
    # Root's exact callback must reject a failing departure endpoint. This
    # pure contract does not substitute for the physical T15 integration test.
    assert any(phase == "departure" for _, phase, _ in got.window_checks)


def test_t17_internal_transfer_is_counted_once(api):
    i = instance()
    i.onboard.tank_masses_kg = {"DB-P": 200_000.}
    got = api.changeover_programme(i, NS(tank_masses_kg={"DB-S": 200_000.}),
                                 k=100, lift_count=300)
    assert got.metrics["pumped_mass_t"] == 200.
    assert got.metrics["abs_change_t"] == 400.


def test_post_operation_allowance_is_only_available_at_sentinel(api):
    i = instance()
    i.onboard.tank_masses_kg = {"DB-P": .9*1025.*300.}
    departure = NS(tank_masses_kg={})
    before = api.changeover_programme(i, departure, k=5, lift_count=5)
    after = api.changeover_programme(i, departure, k=6, lift_count=5)
    assert before.passed and after.passed
    assert before.violations == after.violations == ()
    assert before.metrics["ballast_delay_h"] == pytest.approx(.9-(.5+5/60.))
    assert after.metrics["ballast_delay_h"] == 0.
    assert before.metrics["pump_time_available_h"] == pytest.approx(.5+5/60.)
    assert after.metrics["pump_time_available_h"] == pytest.approx(.5+6/60.+.5)
    assert after.metrics["lifts_after_k"] == 0
    assert after.metrics["pump_window_lifts"] == 24
    assert all(0 <= index <= 5 for index in after.window_indices)


def test_zero_change_needs_no_window_and_exact_time_boundary_passes(api):
    i = instance()
    got = api.changeover_programme(i, i.onboard, k=0, lift_count=0)
    assert got.passed and got.metrics["pump_window_lifts"] == 0
    assert got.metrics["ballast_delay_h"] == 0.
    assert got.window_checks == ()
    i.onboard.tank_masses_kg = {"DB-P": .5*1025.*300.}
    got = api.changeover_programme(i, NS(tank_masses_kg={}), k=0, lift_count=5)
    assert got.passed
    assert got.metrics["ballast_delay_h"] == 0.


@pytest.mark.parametrize("k,n", [(-1, 5), (7, 5), (0, -1), (True, 5), (1.5, 5)])
def test_invalid_state_index_contract_rejected(api, k, n):
    with pytest.raises(ValueError):
        api.changeover_programme(instance(), NS(tank_masses_kg={}), k=k, lift_count=n)


def test_t16_anticipation_can_make_large_ideal_steps_feasible(api):
    p = parameters(api)
    delta = p.heel_step_limit_kg
    got = api.heel_path([(0., 2*delta), (2*delta, 3*delta), (3*delta, 4*delta)],
        [0., 2*delta, 4*delta], arrival_split_kg=0., parameters=p, call_index=0)
    assert got.feasible and not got.violations
    assert got.path_kg == pytest.approx((delta, 2*delta, 3*delta))
    assert got.changed_indices == (0, 2)
    assert got.metrics["heel_max_ideal_step_t"] > got.metrics["heel_step_limit_t"]
    assert got.metrics["heel_max_step_t"] <= got.metrics["heel_step_limit_t"]+1e-10
    assert got.metrics["heel_path_linearised_miss"] == 0.


def test_t16_sudden_reversal_with_no_room_is_rate_failure(api):
    p = parameters(api)
    d = p.heel_step_limit_kg
    got = api.heel_path([(0., 0.), (2*d, 2*d)], [0., 2*d],
        arrival_split_kg=0., parameters=p, call_index=4)
    assert not got.feasible and got.failure_index == 1 and got.path_kg == ()
    assert got.metrics["heel_path_linearised_miss"] == 1.
    violation = got.violations[0]
    assert violation.rule_id == "port.ballast.transfer_rate"
    assert violation.call_index == 4 and violation.move_index == 0
    assert "heel_step" in violation.message


def test_initial_split_is_limited_by_pre_operation_allowance(api):
    p = parameters(api, pre_operation_allowance_h=0.)
    got = api.heel_path([(1., 2.)], [1.], arrival_split_kg=0., parameters=p, call_index=0)
    assert not got.feasible and got.failure_index == 0
    p = parameters(api, pre_operation_allowance_h=1./3600.)
    capacity = p.water_density_kg_m3*p.heel_transfer_m3_h/3600.
    got = api.heel_path([(capacity, 2*capacity)], [capacity],
        arrival_split_kg=0., parameters=p, call_index=0)
    assert got.feasible and got.path_kg == (capacity,)


@pytest.mark.parametrize("band", [None, (3., 2.), (math.nan, 2.)])
def test_missing_or_invalid_band_never_false_accepts(api, band):
    got = api.heel_path([(0., 1.), band], [0., 1.], arrival_split_kg=0.,
                       parameters=parameters(api), call_index=0)
    assert not got.feasible and got.failure_index == 1
    assert got.metrics["heel_path_linearised_miss"] == 1.
    assert got.metrics["heel_max_step_t"] is None
    assert "heel_step" in got.violations[0].message


def test_backward_band_path_stays_in_original_intervals_and_step_limits(api):
    p = parameters(api)
    d = p.heel_step_limit_kg
    bands = [(i*d, (i+2)*d) for i in range(1000)]
    ideals = [lo if index%2 else hi for index, (lo, hi) in enumerate(bands)]
    got = api.heel_path(bands, ideals, arrival_split_kg=0., parameters=p, call_index=0)
    assert got.feasible and len(got.path_kg) == 1000
    assert all(lo <= value <= hi for value, (lo, hi) in zip(got.path_kg, bands, strict=True))
    assert all(abs(b-a) <= d for a, b in zip(got.path_kg, got.path_kg[1:]))


def test_non_binary_step_limit_is_never_rounded_outward(api):
    p = parameters(api, port_cranes=3, max_cranes=3)
    d = p.heel_step_limit_kg
    bands = [(1e6+i*d, 1e6+(i+2)*d) for i in range(256)]
    got = api.heel_path(bands, [hi for _, hi in bands], arrival_split_kg=1e6,
                       parameters=p, call_index=0)
    assert got.feasible
    assert all(abs(b-a) <= d for a, b in zip(got.path_kg, got.path_kg[1:]))


def test_b3_reporting_names_and_units_are_complete(api):
    i = instance()
    r1 = api.changeover_programme(i, i.onboard, k=6, lift_count=5)
    r2 = api.heel_path([(0., 10.)]*6, [5.]*6, arrival_split_kg=5.,
                       parameters=parameters(api), call_index=0)
    metrics = {**r1.metrics, **r2.metrics}
    required = {"changeover_k", "lifts_after_k", "pumped_mass_t", "abs_change_t",
        "pump_time_required_h", "pump_time_available_h", "ballast_delay_h", "pump_window_lifts",
        "ballast_pump_m3_h", "t_lift_s", "cranes", "gross_moves_per_hour",
        "heel_step_limit_t", "heel_max_step_t", "heel_max_ideal_step_t",
        "heel_path_feasible", "heel_path_linearised_miss", "rate_check_rule"}
    assert required <= metrics.keys()
    assert metrics["rate_check_rule"] == "port.ballast.transfer_rate"


@pytest.mark.parametrize("updates", [
    {"water_density_kg_m3": 1e308, "ballast_pump_m3_h": 1e308},
    {"heel_transfer_m3_h": 1e308},
    {"gross_moves_per_hour": 1e-308},
    {"gross_moves_per_hour": 1e308},
    {"pre_operation_allowance_h": 1e308},
    {"max_cranes": 10**1000, "port_cranes": 10**1000},
])
def test_overflowing_derived_rate_or_allowance_is_not_unlimited(api, updates):
    with pytest.raises(ValueError):
        parameters(api, **updates)


@pytest.mark.parametrize("arrival,departure,field", [
    ({"DB-P": 1e308, "DB-S": 1e308}, {}, "pumped_mass_kg"),
    ({"DB-P": 1e308}, {"DB-S": 1e308}, "abs_change_kg"),
])
def test_overflowing_total_pumped_or_absolute_mass_raises_value_error(api, arrival, departure, field):
    i = instance()
    i.onboard.tank_masses_kg = arrival
    with pytest.raises(ValueError, match=field):
        api.changeover_programme(i, NS(tank_masses_kg=departure), k=0, lift_count=5)


def test_required_hours_overflow_is_an_input_error_not_a_rate_verdict(api):
    i = instance()
    i.onboard.tank_masses_kg = {"DB-P": 1e308}
    p = parameters(api, ballast_pump_m3_h=1e-300, water_density_kg_m3=1.)
    with pytest.raises(ValueError, match="pump_time_required_h"):
        api.changeover_programme(i, NS(tank_masses_kg={}), k=0, lift_count=5, parameters=p)


def test_required_seconds_must_be_finite_even_if_hours_fit(api):
    i = instance()
    i.onboard.tank_masses_kg = {"DB-P": 1e308}
    p = parameters(api, ballast_pump_m3_h=10., water_density_kg_m3=1.)
    with pytest.raises(ValueError, match="pump_time_required_s"):
        api.changeover_programme(i, NS(tank_masses_kg={}), k=0, lift_count=5, parameters=p)


@pytest.mark.parametrize("allowance,field", [(1e308, "pump_time_available_h"),
                                            (1e306, "pump_time_available_s")])
def test_available_time_overflow_does_not_grant_unlimited_pumping(api, allowance, field):
    i = instance()
    p = parameters(api, water_density_kg_m3=1., heel_transfer_m3_h=1e-300,
        pre_operation_allowance_h=allowance, post_operation_allowance_h=allowance)
    with pytest.raises(ValueError, match=field):
        api.changeover_programme(i, i.onboard, k=1, lift_count=0, parameters=p)


def test_pump_window_count_cannot_overflow_report_fields(api):
    i = instance()
    i.onboard.tank_masses_kg = {"DB-P": 1e30}
    p = parameters(api, gross_moves_per_hour=1e308, port_cranes=1, max_cranes=1)
    with pytest.raises(ValueError, match="pump_window_lifts"):
        api.changeover_programme(i, NS(tank_masses_kg={}), k=0, lift_count=5, parameters=p)


def test_reachable_band_overflow_is_not_unlimited_heel_authority(api):
    p = parameters(api, pre_operation_allowance_h=1e302)
    with pytest.raises(ValueError, match="HEEL reachable"):
        api.heel_path([(0., 1.)], [0.], arrival_split_kg=1.79e308,
                      parameters=p, call_index=0)
