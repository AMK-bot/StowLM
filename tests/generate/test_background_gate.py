"""The D58 gate cannot accept incomplete or failing full-checker evidence."""
from types import SimpleNamespace

import pytest

from stowage.generate import background_acceptance as gate
from stowage.model import BallastCondition, CheckReport, PartReport
from stowage.model.fixtures import build_instance


@pytest.fixture(autouse=True)
def unit_numerical_artifact_directory(monkeypatch, tmp_path):
    """Controlled checker fixtures use explicit isolated physical inputs.

    Source binding has dedicated real retained-background and dependency
    regressions. This unit module tests cache protocol against its controlled
    checker, without coupling it to unrelated concurrent package edits.
    """
    monkeypatch.setattr(gate, "_implementation_key", lambda: {"sha256": "unit-physics"})
    monkeypatch.setattr(gate, "_stability_table_directory", lambda: tmp_path)


def _report(*, failing=False, missing=False, ratio=1.10):
    parts = {}
    for phase in ("departure", "mid_voyage", "arrival"):
        if missing and phase == "arrival":
            continue
        metrics = {f"condition.phase: {phase}": 1., "containership_form_factor": .1,
                   **{k: v/.1*ratio for k, v in gate._B23.items()}}
        parts[f"condition:{phase}:stability"] = PartReport(name="stability", metrics=metrics)
    if failing:
        # Failure of any other part must reject even with adequate GZ margin.
        parts["rules"] = PartReport(name="rules", ran=False, error="forged background stack overweight")
    return CheckReport(passed=not failing, parts=parts)


@pytest.fixture
def physics(monkeypatch):
    import stowage.checker
    import stowage.solver.ballast
    gate.clear_background_cache()
    calls = []
    report = [_report()]
    def finish(instance, plan, **kwargs):
        calls.append(instance)
        return SimpleNamespace(status="kept", plan=plan.model_copy(update={
            "ballast": BallastCondition(tank_masses_kg={}, source="planner")}))
    monkeypatch.setattr(stowage.solver.ballast, "finish_ballast", finish)
    monkeypatch.setattr(stowage.checker, "check", lambda *args: report[0])
    monkeypatch.setattr(stowage.checker, "departure_condition", lambda instance, plan: instance.onboard)
    yield calls, report
    gate.clear_background_cache()


def test_gate_rejects_failing_background(physics):
    calls, report = physics
    report[0] = _report(failing=True)
    result = gate.evaluate_background(build_instance(), use_cache=False)
    assert not result.accepted
    assert "rules/checker.unavailable" in result.reasons
    assert not calls[0].load_list
    assert not calls[0].yard.stacks
    assert not calls[0].containers


@pytest.mark.parametrize("report", [_report(missing=True), _report(ratio=1.049)])
def test_gate_requires_all_states_and_arrival_margin(physics, report):
    physics[1][0] = report
    assert not gate.evaluate_background(build_instance(), use_cache=False).accepted


def test_background_cache_reuses_content_and_isolates_mutation(physics):
    instance = build_instance()
    original = instance.model_dump_json()
    first = gate.evaluate_background(instance)
    assert first.accepted and not first.cache_hit
    first.states["arrival"]["b23_ratio"] = 0.
    second = gate.evaluate_background(instance.model_copy(update={"id": "another-id", "seed": 9}))
    assert second.accepted and second.cache_hit
    assert second.states["arrival"]["b23_ratio"] > 1.05
    assert len(physics[0]) == 1
    assert instance.model_dump_json() == original


def test_background_cache_invalidates_changed_rule_inputs(physics):
    instance = build_instance()
    gate.evaluate_background(instance)
    changed = instance.model_copy(update={"rule_overrides": {"iscode.loading.gm": {"minimum": 3.}}})
    assert not gate.evaluate_background(changed).cache_hit
    assert len(physics[0]) == 2


def test_production_gate_rejects_missing_hull(monkeypatch):
    import stowage.solver.ballast
    instance = build_instance()
    instance = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"hull": None})})
    def kept(instance, plan, **kwargs):
        return SimpleNamespace(status="kept", plan=plan.model_copy(update={
            "ballast": BallastCondition(tank_masses_kg={}, source="planner")}))
    monkeypatch.setattr(stowage.solver.ballast, "finish_ballast", kept)
    result = gate.evaluate_background(instance, use_cache=False)
    assert not result.accepted
    assert any("checker" in reason or "missing_states" in reason for reason in result.reasons)


def test_background_cache_invalidates_implementation(physics, monkeypatch):
    active = ["first implementation"]
    monkeypatch.setattr(gate, "_implementation_key", lambda: {"source": active[0]})
    instance = build_instance()
    gate.evaluate_background(instance)
    active[0] = "changed implementation"
    assert not gate.evaluate_background(instance).cache_hit
    assert len(physics[0]) == 2


@pytest.mark.parametrize("use_cache", [False, True])
def test_changed_physics_during_evaluation_cannot_certify_background(physics, monkeypatch, use_cache):
    active = ["before checking"]
    monkeypatch.setattr(gate, "_implementation_key", lambda: {"sha256": active[0]})
    import stowage.checker
    def changed_check(*args):
        active[0] = "after checking"
        return _report()
    monkeypatch.setattr(stowage.checker, "check", changed_check)
    result = gate.evaluate_background(build_instance(), use_cache=use_cache)
    assert not result.accepted
    assert result.reasons == ("background.physics_input.changed",)
    assert not gate._MEMO


def test_accepted_background_audit_binds_current_physics(physics, monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    identity = {"sha256": "a" * 64}
    monkeypatch.setattr(gate, "_implementation_key", lambda: identity)
    instance = build_instance()
    instance.vessel.slice_of = "parent"
    audit = RandomGenerationReport()
    assert gate.accept_background_candidate(instance, audit) is instance
    assert audit.background_last["physics_sha256"] == identity["sha256"]


@pytest.mark.parametrize("input_kind", ["ballast", "water_density", "vessel_evidence"])
def test_background_cache_invalidates_other_physical_inputs(physics, input_kind):
    instance = build_instance()
    gate.evaluate_background(instance)
    changed = instance.model_copy(deep=True)
    if input_kind == "ballast":
        changed.onboard.tank_masses_kg["test-ballast"] = 1.
    elif input_kind == "water_density":
        changed.port.water_density_kg_m3 += 1.
    else:
        changed.vessel.sources.append("Changed physical source evidence")
    assert not gate.evaluate_background(changed).cache_hit
    assert len(physics[0]) == 2


def test_candidate_gate_rejects_and_records_reason_counts(monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    instance = build_instance()
    instance = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"slice_of": "parent"})})
    failed = gate.BackgroundAcceptance(False, ("rules/expert.physical.stack_weight",),
                                       "ok", {}, {}, .25)
    monkeypatch.setattr(gate, "evaluate_background", lambda _: failed)
    audit = RandomGenerationReport()
    assert gate.accept_background_candidate(instance, audit) is None
    assert audit.background_checks == 1
    assert audit.background_rejections == {"rules/expert.physical.stack_weight": 1}
    assert audit.rejections == {"background.acceptance": 1}
    assert audit.background_seconds == .25


def test_declared_recipe_failure_triggers_search_and_requires_full_recheck(monkeypatch):
    import stowage.checker
    import stowage.solver.ballast
    plans = []
    def finish(instance, plan, **kwargs):
        plans.append(plan)
        return SimpleNamespace(status="kept" if plan.ballast else "ok",
            plan=plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")}))
    monkeypatch.setattr(stowage.solver.ballast, "finish_ballast", finish)
    monkeypatch.setattr(stowage.checker, "check", lambda *args: _report(failing=len(plans) == 1))
    monkeypatch.setattr(stowage.checker, "departure_condition", lambda instance, plan: instance.onboard)
    result = gate.evaluate_background(build_instance(), use_cache=False)
    assert result.accepted and result.finisher_status == "ok"
    assert len(plans) == 2
    assert plans[0].ballast is not None and plans[1].ballast is None


def test_whole_ship_recondition_audit_preserves_export_tanks_without_background(monkeypatch):
    from stowage.generate.random_yard import RandomGenerationReport
    instance = build_instance()
    instance = instance.model_copy(update={"size_class": "full", "vessel":
        instance.vessel.model_copy(update={"slice_of": "parent", "fixed_loads": []})})
    def unexpected(_):
        pytest.fail("whole-ship export without fixed cargo has no slice background")
    monkeypatch.setattr(gate, "evaluate_background", unexpected)
    audit = RandomGenerationReport()
    assert gate.accept_background_candidate(instance, audit) is instance
    assert audit.background_checks == 0


def _separate_witness(instance, monkeypatch):
    from stowage.vessel import fleet_conditions
    adjustable = [tank for tank in instance.vessel.tanks if tank.adjustable]
    assert adjustable
    recipe = dict(instance.onboard.tank_masses_kg)
    for tank in adjustable:
        recipe[tank.id] = .9 * tank.capacity_m3 * tank.content_density_kg_m3
    monkeypatch.setattr(fleet_conditions, "empty_background_witness_tank_masses", lambda _: dict(recipe))
    return recipe, adjustable


def test_heavy_witness_is_used_only_on_stripped_copy(physics, monkeypatch):
    instance = build_instance()
    original = instance.model_dump_json()
    recipe, adjustable = _separate_witness(instance, monkeypatch)
    for tank in instance.vessel.tanks:
        if not tank.adjustable:
            # A helper's default consumable fill must not overwrite the actual
            # fixed declaration supplied to the checker's three voyage states.
            recipe[tank.id] = .9 * instance.onboard.tank_masses_kg.get(tank.id, 0.)
    result = gate.evaluate_background(instance, use_cache=False)
    from dataclasses import asdict
    assert "witness_tank_masses_kg" in asdict(result)
    assert "tank_masses_kg" not in asdict(result)
    assert result.accepted
    for tank in adjustable:
        assert physics[0][0].onboard.tank_masses_kg[tank.id] == recipe[tank.id]
        assert result.witness_tank_masses_kg[tank.id] == recipe[tank.id]
    for tank in instance.vessel.tanks:
        if not tank.adjustable:
            assert result.witness_tank_masses_kg.get(tank.id, 0.) == instance.onboard.tank_masses_kg.get(tank.id, 0.)
    assert result.tank_masses_kg == result.witness_tank_masses_kg
    assert instance.model_dump_json() == original


def test_witness_preserves_booking_headroom_and_actual_plan_initial_tanks(physics, monkeypatch):
    from stowage.checker.full import departure_condition
    from stowage.generate.random_yard import RandomGenerationReport
    from stowage.model import MovePlan
    instance = build_instance()
    instance = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"slice_of": "parent"})})
    recipe, adjustable = _separate_witness(instance, monkeypatch)
    original_tanks = dict(instance.onboard.tank_masses_kg)
    original_mass = sum(original_tanks.values())
    audit = RandomGenerationReport()
    accepted = gate.accept_background_candidate(instance, audit)
    assert accepted is instance
    assert accepted.onboard.tank_masses_kg == original_tanks
    assert sum(accepted.onboard.tank_masses_kg.values()) == original_mass
    plan = MovePlan(id="actual-booked-plan", instance_id=instance.id, call_index=instance.call_index, moves=[])
    assert departure_condition(accepted, plan).tank_masses_kg == original_tanks
    assert audit.background_last["booking_tank_masses_kg"] == original_tanks
    assert audit.background_last["witness_tank_masses_kg"] == recipe
    assert audit.background_last["witness_ballast_mass_kg"] > audit.background_last["booking_ballast_mass_kg"]
    assert all(accepted.onboard.tank_masses_kg.get(tank.id, 0.) < recipe[tank.id] for tank in adjustable)


def test_witness_cache_ignores_separate_booking_ballast(physics, monkeypatch):
    instance = build_instance()
    recipe, adjustable = _separate_witness(instance, monkeypatch)
    gate.evaluate_background(instance)
    changed = instance.model_copy(deep=True)
    changed.onboard.tank_masses_kg[adjustable[0].id] = .25 * recipe[adjustable[0].id]
    assert gate.evaluate_background(changed).cache_hit
    assert len(physics[0]) == 1


def test_witness_cache_invalidates_changed_witness_recipe(physics, monkeypatch):
    instance = build_instance()
    recipe, adjustable = _separate_witness(instance, monkeypatch)
    gate.evaluate_background(instance)
    recipe[adjustable[0].id] *= .9
    assert not gate.evaluate_background(instance).cache_hit
    assert len(physics[0]) == 2


@pytest.fixture(scope="module")
def booking_geometry():
    from stowage.vessel.fleet_slices import load_fleet_preset
    geometry = load_fleet_preset("feeder_500", "full", with_hull=False)
    # Cargo/background sizing uses this feeder's loading-condition budget.
    # Borrowing the unrelated 100 m3 model-placeholder tanks makes its fixed
    # cargo, consumables and D40 displacement screen mutually inconsistent.
    # Retain the real tank capacities/policy, omitting only expensive hull
    # physics that this controlled witness-plumbing test does not assert.
    physical = load_fleet_preset("feeder_500", "full", with_hull=True)
    return geometry.model_copy(update={"tanks": physical.tanks,
                                       "voyage_policy": physical.voyage_policy})


@pytest.mark.parametrize("generator", ["random", "simulated"])
def test_generated_booking_never_receives_heavy_gate_witness(monkeypatch, booking_geometry, generator):
    """Geometry-only plumbing test; existing real-hull tests own physics acceptance."""
    from stowage.generate import instances, random_yard
    from stowage.generate.random_export import booking_tank_masses
    provider = random_yard if generator == "random" else instances
    monkeypatch.setattr(provider, "_full_vessel", lambda _: booking_geometry)
    # Run the real shared acceptance wrapper rather than conftest's geometry
    # diagnostic bypass; only its expensive checker result is controlled here.
    monkeypatch.setattr(provider, "accept_background_candidate", gate.accept_background_candidate)
    probes = []
    def witness(instance):
        tanks = dict(instance.onboard.tank_masses_kg)
        for tank in instance.vessel.tanks:
            if tank.adjustable:
                tanks[tank.id] = .9 * tank.capacity_m3 * tank.content_density_kg_m3
        probes.append((instance, tanks))
        return gate.BackgroundAcceptance(True, (), "kept", tanks, {}, 0.)
    monkeypatch.setattr(gate, "evaluate_background", witness)
    report = provider.RandomGenerationReport() if generator == "random" else provider.GenerationReport()
    if generator == "random":
        instance = provider.generate_random_instance("tiny", seed=1, report=report)
    else:
        original_screen = provider.screen_load_list
        def geometry_screen(vessel, boxes, **kwargs):
            # This explicit fixture has no hull; retain all cargo/mass screens.
            return [reason for reason in original_screen(vessel, boxes, **kwargs)
                    if reason != "checker.missing_vessel_data"]
        monkeypatch.setattr(provider, "screen_load_list", geometry_screen)
        instance = provider.generate_simulated_instance("tiny", seed=1, report=report, without_dg=True)
    assert probes and report.background_checks > 0
    expected = booking_tank_masses(instance.vessel,
        [box for box in instance.containers if box.id in instance.load_list],
        provider.load_param_file("cargo", "feeder_export"))
    assert instance.onboard.tank_masses_kg == expected
    assert report.background_last["booking_tank_masses_kg"] == expected
    assert report.background_last["witness_tank_masses_kg"] != expected
    assert report.background_last["witness_ballast_mass_kg"] > report.background_last["booking_ballast_mass_kg"]


@pytest.mark.parametrize("not_found_at", ["initial", "fallback"])
def test_not_found_is_never_cached_even_after_a_later_accept(monkeypatch, not_found_at):
    import stowage.checker
    import stowage.solver.ballast
    gate.clear_background_cache()
    calls = []
    def finish(instance, plan, **kwargs):
        phase = "initial" if plan.ballast is not None else "fallback"
        calls.append(phase)
        if phase == not_found_at:
            return SimpleNamespace(status="not_found", plan=None)
        return SimpleNamespace(status="ok", plan=plan.model_copy(update={
            "ballast": BallastCondition(tank_masses_kg={}, source="planner")}))
    # Initial evidence fails so both finisher branches are exercised each time.
    monkeypatch.setattr(stowage.solver.ballast, "finish_ballast", finish)
    monkeypatch.setattr(stowage.checker, "check", lambda *args: _report(failing=len(calls) % 2 == 1))
    monkeypatch.setattr(stowage.checker, "departure_condition", lambda instance, plan: instance.onboard)
    first = gate.evaluate_background(build_instance())
    second = gate.evaluate_background(build_instance())
    assert not first.cache_hit and not second.cache_hit
    assert calls == ["initial", "fallback", "initial", "fallback"]
    assert first.accepted == (not_found_at == "initial")
    gate.clear_background_cache()


def test_rule_rejections_remain_retryable_instead_of_sticking_in_cache(physics):
    physics[1][0] = _report(failing=True)
    first = gate.evaluate_background(build_instance())
    physics[1][0] = _report()
    second = gate.evaluate_background(build_instance())
    assert not first.accepted
    assert second.accepted and not second.cache_hit


@pytest.mark.parametrize("change", ["content_preserved_mtime", "addition", "deletion", "newest_mtime", "certificate"])
def test_stability_table_manifest_invalidates_background_acceptance(physics, monkeypatch, tmp_path, change):
    import os
    monkeypatch.setattr(gate, "_stability_table_directory", lambda: tmp_path)
    # No table loading is mocked into production here: the controlled checker
    # fixture isolates cache identity from the numerical NPZ file format.
    first = tmp_path / "matching-a.npz"
    second = tmp_path / "matching-b.npz"
    first.write_bytes(b"first-table")
    second.write_bytes(b"other-table")
    instance = build_instance()
    gate.evaluate_background(instance)
    assert gate.evaluate_background(instance).cache_hit
    stat = first.stat()
    if change == "content_preserved_mtime":
        first.write_bytes(b"third-table")  # same length; restored mtime must not mask new bytes
        os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif change == "addition":
        (tmp_path / "matching-new.npz").write_bytes(b"new-table")
    elif change == "deletion":
        second.unlink()
    elif change == "newest_mtime":
        os.utime(first, ns=(stat.st_atime_ns, max(stat.st_mtime_ns, second.stat().st_mtime_ns)+1_000_000_000))
    else:
        first.with_suffix(".certificate.json").write_text('{"qualified": true}')
    changed = gate.evaluate_background(instance)
    assert changed.accepted and not changed.cache_hit
    assert len(physics[0]) == 2


def test_repeated_large_stability_manifest_reuses_stable_file_hashes(monkeypatch, tmp_path):
    """A warm gate must not reread the entire legacy table archive per key."""
    from pathlib import Path
    monkeypatch.setattr(gate, "_stability_table_directory", lambda: tmp_path)
    gate._TABLE_HASHES.clear()
    for index in range(300):
        (tmp_path / f"table-{index:04d}.npz").write_bytes(b"stable-table")
    first = gate._stability_table_manifest()
    actual_open = Path.open
    reads = []
    def track_open(path, *args, **kwargs):
        if path.parent == tmp_path:
            reads.append(path)
        return actual_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", track_open)
    assert gate._stability_table_manifest() == first
    assert not reads
    gate._TABLE_HASHES.clear()
