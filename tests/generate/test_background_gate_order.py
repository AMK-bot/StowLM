"""Cheap rejection precedes D58; no surviving candidate may bypass D58.

These are orchestration tests with explicit geometry-only fixtures and gate
sentinels. They make no claim about hydrostatics or physical feasibility; the
real full-checker acceptance tests retain that responsibility.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from stowage.generate import instances, random_yard
from stowage.vessel.fleet_conditions import departure_tank_masses
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.fleet_slices import load_fleet_preset
from stowage.vessel.fleet_voyage import build_voyage_policy
from stowage.vessel.tanks import build_tanks


@pytest.fixture(scope="module")
def geometry():
    vessel = load_fleet_preset("feeder_500", "full", with_hull=False)
    # Retain declared live tanks while deliberately omitting hull/GZ assembly.
    tanks = build_tanks(load_ship_params("feeder_500"))
    policy = build_voyage_policy(tanks, parameter_source="gate-order diagnostic",
                                vessel_id=vessel.id)
    return vessel.model_copy(update={"tanks": tanks, "voyage_policy": policy})


@pytest.fixture(params=["random", "simulated"])
def pipeline(request, monkeypatch, geometry):
    module = random_yard if request.param == "random" else instances
    state = SimpleNamespace(module=module, conditioned=[], screened=[], completed=[], gates=[])
    monkeypatch.setattr(module, "_full_vessel", lambda _: geometry)

    def condition(vessel, **kwargs):
        # Each candidate carries a visible identity without building hull tables.
        result = vessel.model_copy(update={"sources": [*vessel.sources,
            f"gate-order-candidate:{len(state.conditioned)}"]}, deep=True)
        state.conditioned.append(result)
        return result

    monkeypatch.setattr(module, "recondition_slice", condition)
    state.booking_tanks = departure_tank_masses(geometry)
    assert state.booking_tanks  # Use valid declared live-tank identities.
    monkeypatch.setattr(module, "booking_tank_masses",
                        lambda *args: dict(state.booking_tanks))
    if module is instances:
        monkeypatch.setattr(module, "departure_tank_masses",
                            lambda _: dict(state.booking_tanks))

    # Explicit non-DG service avoids incidental unsupported-UN redraws. Keep
    # the real cargo provider, geometry and yard/simulator snapshot construction.
    actual_params = module.load_param_file
    def params(kind, name):
        result = deepcopy(actual_params(kind, name))
        if kind == "cargo":
            result["dg"]["share_of_eligible_loaded_boxes"]["value"] = 0.0
            result["dg"]["implied_share_of_all_boxes"]["value"] = 0.0
        return result
    monkeypatch.setattr(module, "load_param_file", params)
    monkeypatch.setattr(module, "export_rejections", lambda *args, **kwargs: [])

    def screen(vessel, boxes, **kwargs):
        state.screened.append(vessel)
        return []
    def complete(candidate):
        state.completed.append(candidate)
        return ()
    monkeypatch.setattr(module, "screen_load_list", screen)
    monkeypatch.setattr(module, "completion_failures", complete)

    def generate(*, max_attempts=3, **kwargs):
        if module is random_yard:
            audit = random_yard.RandomGenerationReport()
            result = module.generate_random_instance("tiny", seed=23, report=audit,
                max_attempts=max_attempts, **kwargs)
        else:
            audit = instances.GenerationReport()
            result = module.generate_simulated_instance("tiny", seed=23, report=audit,
                max_attempts=max_attempts, without_dg=True, dense_20ft=True,
                n_boxes=3, **kwargs)
        return result, audit
    state.generate = generate
    return state


def _assert_probe(probe, state):
    assert state.completed, "the background gate must follow the completion screens"
    candidate = state.completed[-1]
    assert probe.vessel == candidate.vessel == state.conditioned[-1]
    assert probe.onboard.tank_masses_kg == candidate.onboard.tank_masses_kg == state.booking_tanks
    assert probe.port == candidate.port and probe.rotation == candidate.rotation
    assert probe.seed == candidate.seed == 23
    assert probe.call_index == candidate.call_index == 0
    assert probe.id.startswith(("random-background.", "sim-background."))
    assert not probe.containers and not probe.load_list
    assert not probe.yard.stacks and not probe.onboard.placements
    assert probe.voyage is not None
    state.gates.append(probe)


@pytest.mark.parametrize("rejection_stage", ["cheap", "c1"])
def test_rejected_candidate_skips_background_gate(pipeline, monkeypatch, rejection_stage):
    state = pipeline
    if rejection_stage == "cheap":
        def screen(vessel, boxes, **kwargs):
            state.screened.append(vessel)
            return ["expert.physical.slot_occupancy"] if len(state.screened) == 1 else []
        monkeypatch.setattr(state.module, "screen_load_list", screen)
    else:
        def complete(candidate):
            state.completed.append(candidate)
            return ("c1f",) if len(state.completed) == 1 else ()
        monkeypatch.setattr(state.module, "completion_failures", complete)

    def gate(probe, audit):
        _assert_probe(probe, state)
        return probe
    monkeypatch.setattr(state.module, "accept_background_candidate", gate)
    result, audit = state.generate()
    assert audit.attempts == 2
    assert len(state.gates) == 1
    assert state.gates[0].vessel == state.conditioned[1] == result.vessel
    assert state.conditioned[0].sources != result.vessel.sources
    assert result.onboard.tank_masses_kg == state.booking_tanks


def test_gate_rejection_redraws_and_witness_does_not_replace_booking(pipeline, monkeypatch):
    state = pipeline
    def gate(probe, audit):
        _assert_probe(probe, state)
        if len(state.gates) == 1:
            return None
        # A separate acceptance witness is not the booking initialization.
        witness = probe.model_copy(deep=True)
        witness.onboard.tank_masses_kg = {}
        return witness
    monkeypatch.setattr(state.module, "accept_background_candidate", gate)
    result, audit = state.generate()
    assert audit.attempts == 2
    assert len(state.completed) == len(state.gates) == 2
    assert result.vessel == state.gates[1].vessel
    assert result.vessel.sources != state.gates[0].vessel.sources
    assert result.onboard.tank_masses_kg == state.booking_tanks
    assert result.load_list and result.yard.stacks


def test_no_eligible_candidate_emits_without_gate_acceptance(pipeline, monkeypatch):
    state = pipeline
    def gate(probe, audit):
        _assert_probe(probe, state)
        return None
    monkeypatch.setattr(state.module, "accept_background_candidate", gate)
    with pytest.raises(ValueError, match="screening exhausted 2 attempts"):
        state.generate(max_attempts=2)
    assert len(state.completed) == len(state.gates) == 2


def test_random_c2_rejection_skips_background_gate(pipeline, monkeypatch):
    state = pipeline
    if state.module is not random_yard:
        pytest.skip("the simulated path has no optional C2 screen")
    from stowage.generate import random_screen
    c2_candidates = []
    def necessary(candidate, **kwargs):
        c2_candidates.append(candidate)
        return ("c2.infeasible",) if len(c2_candidates) == 1 else ()
    monkeypatch.setattr(random_screen, "necessary_completion_failures", necessary)
    def gate(probe, audit):
        assert len(c2_candidates) == 2
        _assert_probe(probe, state)
        return probe
    monkeypatch.setattr(state.module, "accept_background_candidate", gate)
    result, audit = state.generate(c2_screen=True)
    assert audit.attempts == 2 and audit.c2_checks == 2
    assert len(state.gates) == 1
    assert result.vessel == c2_candidates[1].vessel
