"""Real-hull regressions for the two reviewed D64 S0 false accepts."""
from math import sin

import pytest

from stowage.generate import background_acceptance as gate
from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(scope="module")
def physical_arrival():
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_vessel
    from stowage.vessel.fleet_berth import prepare_arrival

    with rule_freeze_v1(enabled=True):
        vessel = load_vessel("feeder_500")
        template = build_instance()
        booked = template.model_copy(update={"size_class": "full", "vessel": vessel,
            "containers": [], "load_list": [], "discharge_list": [],
            "onboard": template.onboard.model_copy(update={"vessel_id": vessel.id,
                "placements": [], "covers_off": {}, "tank_masses_kg": {}})})
        return prepare_arrival(booked)


def _primitive(instance):
    from stowage.checker.stability import check_stability
    return check_stability(instance, instance.onboard, call_index=instance.call_index, in_port=True)


def _pass_one(instance):
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.in_port_liquids import prepare_context
    return check_state(instance, instance.onboard,
                       prepare_context(instance, instance.onboard), warm_start=False)


def _assert_computed(part):
    assert part.ran and part.error is None, part.error


def test_actual_s0_charges_worked_fsm_at_the_gm_floor(physical_arrival):
    """The reviewed primitive GM .152 passed while pass 1 was .147838 m.

    Set the synthetic lightship VCG for the same .152 m boundary on the current
    physical vessel; keep real tank solids, liquids, hull and the .15 m floor.
    This avoids encoding a VCG valid only for one tank-parameter epoch.
    """
    with rule_freeze_v1(enabled=True):
        baseline = _primitive(physical_arrival)
        _assert_computed(baseline)
        hull = physical_arrival.vessel.hull

        def at(vcg):
            return physical_arrival.model_copy(update={"vessel":
                physical_arrival.vessel.model_copy(update={"hull":
                    hull.model_copy(update={"lightship_vcg_m": vcg})})})
        # GM is only nearly linear in lightship VCG (equilibrium liquids and
        # free-surface treatment respond), so secant-iterate onto the boundary.
        points = [(hull.lightship_vcg_m, baseline.metrics["gm_m"])]
        vcg = hull.lightship_vcg_m + ((baseline.metrics["gm_m"]-.152)
            * baseline.metrics["displacement_kg"] / hull.lightship_mass_kg)
        for _ in range(6):
            primitive = _primitive(at(vcg))
            _assert_computed(primitive)
            gm = primitive.metrics["gm_m"]
            if abs(gm-.152) < 1e-7:
                break
            (v0, g0), points = points[-1], points + [(vcg, gm)]
            vcg = vcg + (gm-.152)*(vcg-v0)/(g0-gm)
        instance = at(vcg)
        primitive = _primitive(instance)
        corrected = _pass_one(instance)
        result = gate.evaluate_background(instance, arrival_instance=instance, use_cache=False)
    _assert_computed(primitive)
    _assert_computed(corrected)
    assert primitive.metrics["gm_m"] == pytest.approx(.152, abs=1e-6)
    assert not primitive.violations
    assert corrected.metrics["gm_m"] < .15
    assert any("gm" in violation.message.lower() for violation in corrected.violations), [(v.rule_id, v.message) for v in corrected.violations]
    assert not result.accepted and result.status == "rejected"
    assert "arrival_in_port/port.stability.cargo_operations" in result.reasons
    assert result.in_port["metrics"]["gm_m"] == pytest.approx(corrected.metrics["gm_m"], abs=1e-9)


def test_actual_s0_berth_limit_includes_low_side_heel(physical_arrival):
    with rule_freeze_v1(enabled=True):
        hull = physical_arrival.vessel.hull.model_copy(update={"lightship_tcg_m": .02})
        instance = physical_arrival.model_copy(update={"vessel":
            physical_arrival.vessel.model_copy(update={"hull": hull})})
        primitive = _primitive(instance)
        _assert_computed(primitive)
        keel = max(primitive.metrics["draught_aft_m"], primitive.metrics["draught_fwd_m"])
        low_side = keel + hull.beam_m/2*sin(abs(primitive.metrics["list_rad"]))
        assert low_side > keel
        limit = (keel+low_side)/2
        instance = instance.model_copy(update={"port":
            instance.port.model_copy(update={"max_draught_m": limit})})
        primitive = _primitive(instance)
        corrected = _pass_one(instance)
        result = gate.evaluate_background(instance, arrival_instance=instance, use_cache=False)
    _assert_computed(primitive)
    _assert_computed(corrected)
    assert not primitive.violations
    assert corrected.metrics["berth_draught_m"] > limit
    assert any("port_draught_m" in violation.message for violation in corrected.violations)
    assert not result.accepted and result.status == "rejected"
    assert "arrival_in_port/port.stability.cargo_operations" in result.reasons
    assert result.in_port["metrics"]["berth_draught_m"] == pytest.approx(
        corrected.metrics["berth_draught_m"], abs=1e-9)
