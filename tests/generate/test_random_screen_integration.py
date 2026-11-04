"""Optional C2 screening keeps candidate accounting and fails closed."""
import pytest

from stowage.generate import random_screen, random_yard
from stowage.vessel.fleet_slices import load_fleet_preset


@pytest.fixture
def geometry(monkeypatch):
    vessel = load_fleet_preset("feeder_500", "full", with_hull=False)
    monkeypatch.setattr(random_yard, "_full_vessel", lambda name: vessel)


def test_c2_rejection_redraws_and_records_direction(monkeypatch, geometry):
    checks = []
    def screen(instance, *, diagnostics):
        checks.append(instance)
        diagnostics.update(direction="necessary", status="infeasible" if len(checks) == 1 else "feasible")
        return ("c2",) if len(checks) == 1 else ()
    monkeypatch.setattr(random_screen, "necessary_completion_failures", screen)
    monkeypatch.setattr(random_yard, "completion_failures", lambda instance: ())
    report = random_yard.RandomGenerationReport()
    instance = random_yard.generate_random_instance(seed=1, c2_screen=True, report=report)
    assert report.c2_screened and report.c2_checks == 2
    assert report.completion_rejections["c2"] == 1
    assert report.c2_direction == "necessary" and not report.c2_errors
    assert instance.family["c2_screen"] == "necessary"
    assert checks[-1] is instance


def test_c2_unavailable_aborts_with_audit(monkeypatch, geometry):
    def screen(instance, *, diagnostics):
        diagnostics.update(direction="necessary", status="unavailable", error="unqualified test epoch")
        return ("c2.unavailable",)
    monkeypatch.setattr(random_screen, "necessary_completion_failures", screen)
    report = random_yard.RandomGenerationReport()
    with pytest.raises(ValueError, match="C2 unavailable: unqualified test epoch"):
        random_yard.generate_random_instance(seed=1, c2_screen=True, report=report)
    assert not report.accepted and report.c2_screened and report.c2_checks == 1
    assert report.completion_rejections["c2.unavailable"] == 1
    assert report.c2_errors == {"unqualified test epoch": 1}


def test_c2_option_requires_boolean_before_loading_vessel(monkeypatch):
    monkeypatch.setattr(random_yard, "_full_vessel", lambda name: pytest.fail("invalid input must fail early"))
    with pytest.raises(ValueError, match="c2_screen must be a boolean"):
        random_yard.generate_random_instance(seed=1, c2_screen=1)


def test_cli_c2_resume_cannot_be_reused_without_screen(monkeypatch, geometry, tmp_path):
    import json
    monkeypatch.setattr(random_screen, "necessary_completion_failures", lambda instance, **kwargs: ())
    inputs = random_yard._resume_inputs("feeder_500", "laem_chabang_like")
    monkeypatch.setattr(random_yard, "_resume_inputs", lambda *args: inputs)
    args = ["--size", "tiny", "--seed", "1", "--output", str(tmp_path)]
    assert random_yard.main([*args, "--c2-screen"]) == 0
    assert random_yard.main([*args, "--c2-screen"]) == 0
    audit = json.loads((tmp_path / "tiny/seed-00000001.audit.json").read_text())
    assert audit["c2_requested"] and audit["c2_screened"]
    assert audit["c2_direction"] == "necessary"
    with pytest.raises(ValueError, match="resume configuration mismatch"):
        random_yard.main(args)
