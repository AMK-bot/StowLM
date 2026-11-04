"""Convergence runner transports D50 evidence; mocked checks prove no physics."""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet_voyage import build_voyage_policy


RUNNER = Path(__file__).resolve().parents[2]/"experiments/physics-review/rerun/fix-t2-vessel/recheck.py"


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(sys, "path", sys.path.copy())
    spec = importlib.util.spec_from_file_location("convergence_d56_recheck", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _instance(next_locode=None):
    instance = build_instance()
    instance.vessel.voyage_policy = build_voyage_policy(
        instance.vessel.tanks, parameter_source="synthetic fixture policy; transport test only",
        vessel_id=instance.vessel.id)
    if next_locode is not None:
        old_locode = instance.rotation.calls[instance.call_index+1].locode
        instance.rotation.calls[instance.call_index+1].locode = next_locode
        # This creates a different lightweight test route, not a modification
        # of any historical convergence booking or loading recipe.
        for box in instance.containers:
            if box.pod == old_locode:
                box.pod = next_locode
    return Instance.model_validate(instance.model_dump())


@pytest.mark.parametrize("locode,density", [("THBKK", 1025.), ("VNSGN", 1000.),
                                           ("HKHKG", 1025.), ("MYPKG", 1025.)])
def test_attach_voyage_uses_exact_next_call_environment_and_preserves_state(runner, locode, density):
    instance = _instance(locode)
    before = instance.model_dump_json()
    result = runner.attach_voyage(instance)
    assert instance.model_dump_json() == before
    assert result.voyage is not None and result.voyage.consumption is None
    assert result.voyage.ballast_operations == []
    departure, arrival = result.voyage.departure, result.voyage.arrival
    assert (departure.phase, departure.call_index, departure.locode) == ("departure", instance.call_index, instance.port.locode)
    assert (arrival.phase, arrival.call_index, arrival.locode) == ("arrival", instance.call_index+1, locode)
    assert departure.water_density_kg_m3 == instance.port.water_density_kg_m3
    assert arrival.water_density_kg_m3 == density
    assert departure.id != arrival.id and arrival.environment_id and arrival.density_id
    assert arrival.provenance.parents
    assert result.model_dump(exclude={"voyage", "provenance"}) == instance.model_dump(exclude={"voyage", "provenance"})
    assert Instance.model_validate_json(result.model_dump_json()) == result
    assert runner.attach_voyage(instance).model_dump_json() == result.model_dump_json()


def test_unknown_next_call_environment_fails_closed(runner):
    with pytest.raises(ValueError, match="environment|matching|profile"):
        runner.attach_voyage(_instance("ZZZZZ"))


def _mock_case(monkeypatch, *, require_voyage):
    """Bounded report transport only; never a checker-approved condition."""
    import stowage.vessel.fleet as fleet

    instance = _instance()
    masses = {next(tank.id for tank in instance.vessel.tanks if tank.adjustable): 20.0}
    calls = []

    def checked(actual, plan):
        calls.append((actual, plan))
        if require_voyage:
            assert actual.voyage is not None, "standard convergence condition lacks D50 VoyageSpec"
        assert plan.ballast.tank_masses_kg == masses
        assert actual.onboard.tank_masses_kg == instance.onboard.tank_masses_kg
        assert actual.onboard.placements == instance.onboard.placements
        assert [box.gross_mass_kg for box in actual.containers] == [box.gross_mass_kg for box in instance.containers]
        parts = {}
        if actual.voyage is not None:
            for identity in (actual.voyage.departure, actual.voyage.arrival):
                for part in ("stability", "strength"):
                    parts[f"condition:{identity.id}:{part}"] = dict(
                        name=part, ran=True, violations=[], metrics={"GM_m": 1.5}, margins={}, error=None)
        return dict(status="completed", passed=True, wall_s=0., report={"parts": parts})

    monkeypatch.setitem(sys.modules, "harness2", SimpleNamespace(build=lambda *a, **kw: (instance.model_copy(deep=True), {})))
    monkeypatch.setitem(sys.modules, "regression", SimpleNamespace(witness_masses=lambda *a: masses))
    monkeypatch.setitem(sys.modules, "slices", SimpleNamespace(bounded_full_check=checked))
    monkeypatch.setattr(fleet, "load_vessel", lambda *a, **kw: instance.vessel)
    monkeypatch.setattr(fleet, "load_ship_params", lambda *a: {})
    return calls


def test_run_attaches_voyage_and_retains_full_per_phase_reports(runner, monkeypatch, tmp_path):
    calls = _mock_case(monkeypatch, require_voyage=True)
    row = runner.run("feeder_500", "C1", tmp_path)
    assert len(calls) == 1
    assert row["input_fingerprint"]
    assert set(row["phase_reports"]) == {"departure", "arrival"}
    for phase in ("departure", "arrival"):
        assert row["phase_reports"][phase]["identity"]["phase"] == phase
        assert set(row["phase_reports"][phase]["parts"]) == {"stability", "strength"}


def test_run_refuses_legacy_unbound_cached_results(runner, monkeypatch, tmp_path):
    calls = _mock_case(monkeypatch, require_voyage=False)
    path = tmp_path/"feeder_500_C1.json"
    path.write_text(json.dumps({"ship": "feeder_500", "condition": "C1", "check": {"passed": False}}))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="cached.*input|input.*cached"):
        runner.run("feeder_500", "C1", tmp_path)
    assert calls == []
    assert path.read_bytes() == before


def test_resume_checks_actual_payload_even_when_artifact_id_is_unchanged(runner, monkeypatch, tmp_path):
    calls = _mock_case(monkeypatch, require_voyage=True)
    first = runner.run("feeder_500", "C1", tmp_path)
    assert runner.run("feeder_500", "C1", tmp_path) == first
    assert len(calls) == 1
    build = sys.modules["harness2"].build
    def changed(*args, **kwargs):
        instance, info = build(*args, **kwargs)
        instance.containers[0].gross_mass_kg += 1.0
        return instance, info
    monkeypatch.setattr(sys.modules["harness2"], "build", changed)
    with pytest.raises(ValueError, match="cached.*input|input.*cached"):
        runner.run("feeder_500", "C1", tmp_path)
    assert len(calls) == 1


def test_historical_passive_spies_are_removed_without_replacing_physics_context(runner, monkeypatch):
    from stowage.checker import stability, stability_criteria, stability_curve

    original_eval, original_curve, original_context = (
        stability_criteria.evaluate_criteria, stability_curve.build_stability_curve, stability._context)
    spy_eval, spy_curve = lambda *a, **kw: None, lambda *a, **kw: None
    monkeypatch.setattr(stability_criteria, "evaluate_criteria", original_eval)
    monkeypatch.setattr(stability_curve, "build_stability_curve", original_curve)
    historical = SimpleNamespace(_spy_eval=spy_eval, _orig_eval=original_eval,
                                 _spy_curve=spy_curve, _orig_curve=original_curve)
    def import_harness(name):
        assert name == "harness2"
        stability_criteria.evaluate_criteria = spy_eval
        stability_curve.build_stability_curve = spy_curve
        return SimpleNamespace(h1=historical)
    monkeypatch.setattr(runner.importlib, "import_module", import_harness)
    runner._load_harness()
    assert stability_criteria.evaluate_criteria is original_eval
    assert stability_curve.build_stability_curve is original_curve
    assert stability._context is original_context


def test_phase_reports_keep_unavailable_arrival_visible_after_departure_failure(runner):
    instance = runner.attach_voyage(_instance())
    parts = {f"condition:{instance.voyage.departure.id}:{part}": dict(
        ran=True, violations=[dict(severity="hard", rule_id="test.departure.failure")])
        for part in ("stability", "strength")}
    phases = runner._phase_reports(dict(status="completed", report=dict(parts=parts)), instance.voyage)
    assert phases["departure"]["available"] is True
    assert phases["departure"]["passed"] is False
    assert phases["arrival"]["available"] is False
    assert phases["arrival"]["passed"] is None
    assert phases["arrival"]["identity"] == instance.voyage.arrival.model_dump(mode="json")
    assert phases["arrival"]["parts"] == {}


def test_main_does_not_bypass_checked_resume_for_an_existing_result(runner, monkeypatch, tmp_path):
    path = tmp_path/"feeder_500_C1.json"
    old = dict(ship="feeder_500", condition="C1", historical_witness=True,
               check=dict(passed=False, status="completed", wall_s=0.))
    path.write_text(json.dumps(old))
    calls = []
    def checked_run(*args):
        calls.append(args)
        return old
    monkeypatch.setattr(runner, "run", checked_run)
    monkeypatch.setattr(sys, "argv", [str(RUNNER), "--ships", "feeder_500", "--conditions", "C1", "--out", str(tmp_path)])
    runner.main()
    assert len(calls) == 1, "legacy result was reused without current input validation"
