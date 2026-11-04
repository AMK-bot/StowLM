"""Activated backgrounds certify berth arrival independently of departure."""
from types import SimpleNamespace

import pytest

from stowage.generate import background_acceptance as gate
from stowage.model import BallastCondition, CheckReport, PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.model.vessel import FixedCargoBox, FixedLoad
from stowage.rules.catalogue import rule_freeze_v1


def _voyage_report():
    return CheckReport(passed=True, parts={f"condition:{phase}:stability": PartReport(
        name="stability", metrics={f"condition.phase: {phase}": 1.,
            "containership_form_factor": .1,
            **{key: value/.1*1.2 for key, value in gate._B23.items()}})
        for phase in ("departure", "mid_voyage", "arrival")})


@pytest.fixture
def physics(monkeypatch, tmp_path):
    import stowage.checker
    import stowage.checker.in_port_evaluator
    import stowage.checker.in_port_liquids
    import stowage.solver.ballast
    from stowage.vessel import fleet_conditions

    gate.clear_background_cache()
    state = SimpleNamespace(arrivals=[], departures=[], finishers=[], bad_arrival=False)
    monkeypatch.setattr(gate, "_implementation_key", lambda: {"sha256": "unit-d64"})
    # Controlled checker/finisher mocks own this context explicitly. The real
    # API-unavailable regression overrides the seam and must stay UNKNOWN.
    monkeypatch.setattr(gate, "_seagoing_context_keywords", lambda *args: {})
    monkeypatch.setattr(gate, "_prepare_arrival_instance", lambda instance: instance)
    monkeypatch.setattr(gate, "_stability_table_directory", lambda: tmp_path)
    monkeypatch.setattr(fleet_conditions, "empty_background_witness_tank_masses",
        lambda vessel: {tank.id: .9*tank.capacity_m3*tank.content_density_kg_m3
                        for tank in vessel.tanks if tank.adjustable})

    def stability(instance, ship, context, *, warm_start):
        assert context is instance and warm_start is False
        call_index = instance.call_index
        state.arrivals.append((instance, ship.model_copy(deep=True)))
        violations = ([Violation(rule_id="port.stability.cargo_operations", severity="hard",
            call_index=call_index, message="forged arrival trim outside P6")] if state.bad_arrival else [])
        return PartReport(name="stability", metrics={"in_port": 1., "trim_m": 0.},
                          violations=violations)

    def finish(instance, plan, **kwargs):
        state.finishers.append(instance)
        return SimpleNamespace(status="kept", plan=plan.model_copy(update={
            "ballast": BallastCondition(source="planner",
                tank_masses_kg={tank.id: instance.onboard.tank_masses_kg.get(tank.id, 0.)
                                for tank in instance.vessel.tanks if tank.adjustable})}))

    def check(instance, plan):
        state.departures.append(instance)
        return _voyage_report()

    monkeypatch.setattr(stowage.checker.in_port_liquids, "prepare_context",
        lambda instance, departure: instance)
    monkeypatch.setattr(stowage.checker.in_port_evaluator, "check_state", stability)
    monkeypatch.setattr(stowage.checker, "check", check)
    monkeypatch.setattr(stowage.checker, "departure_condition", lambda instance, plan: instance.onboard)
    monkeypatch.setattr(stowage.solver.ballast, "finish_ballast", finish)
    yield state
    gate.clear_background_cache()


def _full_instance():
    instance = build_instance()
    return instance.model_copy(update={"size_class": "full", "vessel":
        instance.vessel.model_copy(update={"slice_of": None, "fixed_loads": []})})


def _fixed_deck(instance, height):
    """A real supported fixed column lies portward of the decision row."""
    vessel = instance.vessel
    stack = next(s for s in vessel.stacks if s.on_deck and s.bay == 1 and s.row == 2)
    cells = {cell.id: cell for cell in vessel.cells}
    occupied = stack.cell_ids[:height]
    boxes = [FixedCargoBox(cell_ids=[cid], length_ft=20, height_m=2.591,
        gross_mass_kg=5000., x_m=cells[cid].lcg_m, y_m=cells[cid].tcg_m,
        z_m=cells[cid].vcg_base_m+2.591/2, stack_id=stack.id, tier=int(cid[-2:]))
        for cid in occupied]
    fixed = [FixedLoad(kind="cargo", mass_kg=box.gross_mass_kg, x_m=box.x_m,
        y_m=box.y_m, z_m=box.z_m, cargo_boxes=[box]) for box in boxes]
    return instance.model_copy(update={"vessel": vessel.model_copy(update={
        "fixed_loads": fixed, "fixed_load_cells": [cells[cid] for cid in stack.cell_ids],
        "fixed_load_stacks": [stack],
        "cells": [cell for cell in vessel.cells if cell.stack_id != stack.id],
        "stacks": [s for s in vessel.stacks if s.id != stack.id]})}, deep=True)


def _height(instance, height):
    return instance.model_copy(update={"port": instance.port.model_copy(update={
        "max_crane_height_tiers": height,
        "quay_crane": instance.port.quay_crane.model_copy(update={"max_on_deck_tiers": height})})})


def test_active_slice_checks_actual_s0_before_departure_witness(physics):
    instance = build_instance()
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(instance)
    assert result.accepted
    assert len(physics.arrivals) == 1
    assert physics.arrivals[0][1].tank_masses_kg == instance.onboard.tank_masses_kg
    assert physics.departures[0].onboard.tank_masses_kg != instance.onboard.tank_masses_kg
    assert result.in_port["phase"] == "arrival_s0"
    assert result.in_port["tank_masses_kg"] == instance.onboard.tank_masses_kg
    assert {"departure", "mid_voyage", "arrival"} == result.states.keys()
    assert instance.model_dump_json() == before


def test_bad_s0_rejects_even_when_departure_voyage_passes(physics):
    physics.bad_arrival = True
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(build_instance())
    assert not result.accepted
    assert any("port.stability.cargo_operations" in reason for reason in result.reasons)


def test_full_empty_stage_a_checks_s0_without_seagoing_empty_witness(physics):
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(_full_instance())
    assert result.accepted and len(physics.arrivals) == 1
    assert not physics.finishers and not physics.departures
    assert result.states == {} and result.in_port["phase"] == "arrival_s0"


def test_full_empty_stage_a_bad_s0_is_rejected_by_candidate_gate(physics, monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    physics.bad_arrival = True
    instance = _full_instance()
    audit = RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        assert gate.accept_background_candidate(instance, audit) is None
    assert audit.background_checks == 1
    assert audit.rejections["background.acceptance"] == 1


def test_active_cache_binds_original_arrival_ballast_and_decision_demand(physics):
    instance = build_instance()
    with rule_freeze_v1(enabled=True):
        first = gate.evaluate_background(instance)
        assert first.accepted and gate.evaluate_background(instance).cache_hit
        changed = instance.model_copy(deep=True)
        changed.onboard.tank_masses_kg["WB-P"] += 1.
        assert not gate.evaluate_background(changed).cache_hit
        changed.load_list = changed.load_list[:1]
        assert not gate.evaluate_background(changed).cache_hit
    assert len(physics.arrivals) == 3


def test_fixed_background_at_h_is_rejected_and_counted(physics):
    from stowage.generate.random_yard import RandomGenerationReport
    instance = _height(_fixed_deck(build_instance(), 2), 2)
    audit = RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(instance)
        assert not result.accepted
        assert gate.accept_background_candidate(instance, audit) is None
    assert any("d69" in reason for reason in result.reasons)
    assert audit.background_rejections and audit.rejections["background.acceptance"] == 1


def test_call_height_change_cannot_reuse_fixed_background_acceptance(physics):
    instance = _height(_fixed_deck(build_instance(), 2), 3)
    with rule_freeze_v1(enabled=True):
        assert gate.evaluate_background(instance).accepted
        changed = gate.evaluate_background(_height(instance, 2))
    assert not changed.accepted and not changed.cache_hit


def test_switch_off_keeps_legacy_full_bypass_and_three_voyage_states(physics):
    from stowage.generate.random_yard import RandomGenerationReport
    with rule_freeze_v1(enabled=False):
        instance = _full_instance()
        audit = RandomGenerationReport()
        assert gate.accept_background_candidate(instance, audit) is instance
        result = gate.evaluate_background(build_instance())
    assert not physics.arrivals
    assert result.accepted and set(result.states) == {"departure", "mid_voyage", "arrival"}
    assert audit.background_checks == 0


def test_candidate_returns_prepared_arrival_and_retains_booking_audit(physics, monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    instance = build_instance()
    original = instance.model_dump_json()
    arrived = instance.model_copy(deep=True)
    arrived.onboard.tank_masses_kg["WB-P"] += 1000.
    monkeypatch.setattr(gate, "_prepare_arrival_instance", lambda candidate: candidate)
    audit = RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        accepted = gate.accept_background_candidate(arrived, audit)
    assert accepted is arrived
    assert instance.model_dump_json() == original
    assert physics.arrivals[0][1].tank_masses_kg == arrived.onboard.tank_masses_kg
    assert audit.background_last["booking_tank_masses_kg"] == arrived.onboard.tank_masses_kg
    assert audit.background_last["arrival_tank_masses_kg"] == arrived.onboard.tank_masses_kg
    assert audit.background_last["in_port"]["phase"] == "arrival_s0"


def test_unprepared_original_returns_certified_arrival_copy(physics, monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    instance = build_instance()
    original = instance.model_dump_json()
    arrived = instance.model_copy(deep=True)
    arrived.onboard.tank_masses_kg["WB-P"] += 1000.
    monkeypatch.setattr(gate, "_prepare_arrival_instance", lambda candidate: arrived)
    audit = RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        accepted = gate.accept_background_candidate(instance, audit)
    assert accepted is arrived
    assert instance.model_dump_json() == original
    assert not audit.background_rejections and not audit.rejections
    assert audit.background_last["booking_tank_masses_kg"] == instance.onboard.tank_masses_kg
    assert audit.background_last["arrival_tank_masses_kg"] == arrived.onboard.tank_masses_kg
    assert physics.arrivals and physics.finishers


def test_s0_uses_replay_pass_one_context_and_revised_criteria(physics, monkeypatch):
    import stowage.checker.in_port_evaluator as evaluator
    import stowage.checker.in_port_liquids as liquids
    context = object()
    calls = []
    monkeypatch.setattr(liquids, "prepare_context", lambda instance, departure: (
        calls.append((instance, departure)) or context))
    def pass_one(instance, ship, supplied, *, warm_start):
        assert supplied is context and warm_start is False
        assert calls[0] == (instance, instance.onboard)
        return PartReport(name="in_port", metrics={"gm_m": .147838027994}, violations=[
            Violation(rule_id="port.stability.cargo_operations", severity="hard",
                call_index=0, message="P2: GM below 0.15 m")])
    monkeypatch.setattr(evaluator, "check_state", pass_one)
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(_full_instance(), use_cache=False)
    assert not result.accepted
    assert calls and result.in_port["metrics"]["gm_m"] == .147838027994


def test_checker_timeout_raises_unknown_without_redraw_rejection(physics, monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    import stowage.checker
    def timed_check(*args):
        return CheckReport(passed=False, parts={"stability": PartReport(name="stability",
            ran=False, error="time budget exceeded: direct tank-transfer envelope refinement")})
    monkeypatch.setattr(stowage.checker, "check", timed_check)
    audit = RandomGenerationReport()
    instance = build_instance()
    with rule_freeze_v1(enabled=True), pytest.raises(gate.BackgroundAcceptanceUnknown) as raised:
        gate.accept_background_candidate(instance, audit)
    assert raised.value.result.status == "unknown"
    assert audit.background_checks == 1 and not audit.background_rejections and not audit.rejections
    assert audit.background_last["status"] == "unknown"
    assert audit.background_unknowns and not gate._MEMO


def test_unresolved_finisher_not_found_is_unknown_and_never_cached(physics, monkeypatch):
    import stowage.solver.ballast
    monkeypatch.setattr(stowage.solver.ballast, "finish_ballast", lambda *a, **kw:
        SimpleNamespace(status="not_found", plan=None, not_found_cause="unknown_witness"))
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(build_instance())
    assert result.status == "unknown" and not result.accepted and not gate._MEMO


def test_propagated_finisher_deadline_keeps_attempt_and_raises_unknown(physics, monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    import stowage.solver.ballast
    def deadline(*args, **kwargs):
        raise TimeoutError("finisher deadline exhausted")
    monkeypatch.setattr(stowage.solver.ballast, "finish_ballast", deadline)
    audit = RandomGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(gate.BackgroundAcceptanceUnknown):
        gate.accept_background_candidate(build_instance(), audit)
    assert audit.background_checks == 1 and not audit.rejections
    assert audit.background_unknowns == {"background.compute.deadline": 1}


def _with_in_port(report, *violations, error=None):
    parts = dict(report.parts)
    parts["in_port"] = PartReport(name="in_port", violations=list(violations),
                                  ran=error is None, error=error)
    return CheckReport(passed=False, parts=parts)


def _heel_total_violation():
    return Violation(rule_id="port.stability.cargo_operations", severity="hard", call_index=0,
                     message="departure HEEL pair total must equal arrival pair total")


def test_seagoing_witness_without_context_api_excludes_only_in_port_replay(physics, monkeypatch):
    """D64 rev3.1 Correction: HEEL is ordinary ballast in the sea witness.

    The in-port replay of the witness (a fictitious berth state carrying the
    port HEEL-total rule) must not decide it; every D57 part still does.
    """
    import stowage.checker
    monkeypatch.setattr(gate, "_seagoing_context_keywords", lambda *args: None, raising=False)
    monkeypatch.setattr(stowage.checker, "check",
        lambda instance, plan: _with_in_port(_voyage_report(), _heel_total_violation()))
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(build_instance(), use_cache=False)
    assert result.status == "accepted", result.reasons
    assert set(result.states) == {"departure", "mid_voyage", "arrival"}
    assert result.in_port["witness_excluded_parts"] == ["in_port"]
    assert physics.arrivals, "actual s0 pass 1 must still run"


def test_seagoing_witness_without_context_api_still_rejects_sea_failures(physics, monkeypatch):
    import stowage.checker
    monkeypatch.setattr(gate, "_seagoing_context_keywords", lambda *args: None, raising=False)

    def check(instance, plan):
        report = _with_in_port(_voyage_report())
        report.parts["condition:arrival:stability"].violations.append(Violation(
            rule_id="iscode.b23.weather", severity="hard", call_index=0, message="forged weather"))
        return report
    monkeypatch.setattr(stowage.checker, "check", check)
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(build_instance(), use_cache=False)
    assert result.status == "rejected"
    assert "condition:arrival:stability/iscode.b23.weather" in result.reasons
    assert not any(reason.startswith("in_port/") for reason in result.reasons)


def test_seagoing_witness_timeout_is_unknown_only_in_judged_parts(physics, monkeypatch):
    import stowage.checker
    monkeypatch.setattr(gate, "_seagoing_context_keywords", lambda *args: None, raising=False)
    monkeypatch.setattr(stowage.checker, "check",
        lambda instance, plan: _with_in_port(_voyage_report(), error="time budget exceeded"))
    with rule_freeze_v1(enabled=True):
        assert gate.evaluate_background(build_instance(), use_cache=False).status == "accepted"

    def timed(instance, plan):
        report = _with_in_port(_voyage_report())
        report.parts["condition:departure:stability"].ran = False
        report.parts["condition:departure:stability"].error = "time budget exceeded"
        return report
    monkeypatch.setattr(stowage.checker, "check", timed)
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(build_instance(), use_cache=False)
    assert result.status == "unknown" and result.reasons == ("background.checker.unknown",)


def test_needed_discharge_path_uses_public_crossing_geometry(physics, monkeypatch):
    import stowage.moves
    actual = stowage.moves.crossing_paths
    queries = []
    def crossing(instance, move, **kwargs):
        result = actual(instance, move, **kwargs)
        queries.append((move.kind, result.ship_stack_ids))
        return result
    monkeypatch.setattr(stowage.moves, "crossing_paths", crossing)
    instance = _height(_fixed_deck(build_instance(layover=True), 2), 2)
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(instance)
    assert "background.d69.fixed_blocks_needed_row" in result.reasons
    assert any(kind == "discharge" and "00102D" in path for kind, path in queries)


def test_optional_blocked_target_is_not_claimed_as_needed_row(physics):
    instance = _height(_fixed_deck(build_instance(), 2), 2)
    with rule_freeze_v1(enabled=True):
        result = gate.evaluate_background(instance)
    assert not result.accepted  # The global fixed-height policy still applies.
    assert "background.d69.fixed_blocks_needed_row" not in result.reasons


def test_arrival_preparation_failure_rejects_and_counts_without_departure_search(physics, monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    def unavailable(_):
        raise ValueError("no forward ballast capacity")
    monkeypatch.setattr(gate, "_prepare_arrival_instance", unavailable)
    audit = RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        assert gate.accept_background_candidate(_full_instance(), audit) is None
    assert audit.background_rejections == {"background.arrival_preparation.unavailable": 1}
    assert not physics.arrivals and not physics.finishers and not physics.departures
