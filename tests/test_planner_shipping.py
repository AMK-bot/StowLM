"""Shipping requires a complete, clean checker verdict on the exact candidate."""
from types import SimpleNamespace

import pytest

import stowage.interfaces  # noqa: F401
from stowage.frozen import stub_override
from stowage.model import CheckReport, MoveCount, MovePlan, PartReport, PlannerResult, Provenance
from stowage.planner import run_planner


def candidate(instance, **updates):
    result = PlannerResult(status="ok", planner="test", planner_version="1", plan=MovePlan(
        id="shipping-plan", instance_id=instance.id, call_index=instance.call_index))
    return result.model_copy(update=updates)


def planner(result, *, sets_ballast=False):
    return SimpleNamespace(name="test", version="1", sets_ballast=sets_ballast,
                           plan=lambda *a, **kw: result)


def full_report():
    return CheckReport(passed=True, count=MoveCount(), parts={name: PartReport(name=name) for name in (
        "moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")})


@pytest.fixture
def approving_pipeline(monkeypatch):
    # Synthetic unit oracle: no claim that the hull-free fixture passes real physics.
    monkeypatch.setattr("stowage.planner.finalize", lambda i, r, **kw: r)
    monkeypatch.setattr("stowage.checker.check", lambda *a, **kw: full_report())


def test_approving_checker_stub_cannot_ship(empty_ship_instance):
    with stub_override("planner.finalize", lambda i, r, **kw: r, reason="shipping regression"), \
         stub_override("checker.check", lambda *a, **kw: full_report(), reason="shipping regression"):
        result, report = run_planner(planner(candidate(empty_ship_instance)), empty_ship_instance,
                                     allow_repair=False)
    assert report.passed
    assert not result.metrics["shippable"]
    assert not result.shippable


def test_unchecked_later_plan_cannot_ship(empty_ship_instance, approving_pipeline):
    raw = candidate(empty_ship_instance)
    raw.later_plans = [raw.plan.model_copy(update={"id": "unchecked", "call_index": 1})]
    result, report = run_planner(planner(raw), empty_ship_instance, allow_repair=False)
    assert report.passed
    assert not result.metrics["shippable"]
    assert not result.shippable


@pytest.mark.parametrize("repair_failure", [False, True])
def test_missing_declared_ballast_stays_invalid_after_repair(
        empty_ship_instance, approving_pipeline, monkeypatch, repair_failure):
    def repair(i, r, report, **kw):
        if repair_failure:
            raise RuntimeError("repair failed")
        return r
    monkeypatch.setattr("stowage.solver.repair.repair", repair)
    result, report = run_planner(planner(candidate(empty_ship_instance), sets_ballast=True),
                                 empty_ship_instance)
    assert not report.passed
    assert any(v.rule_id == "checker.invalid_ballast" for v in report.violations)
    assert not result.metrics["shippable"]


def test_caller_shipping_flag_is_not_evidence(empty_ship_instance):
    result = candidate(empty_ship_instance, metrics={"shippable": True})
    assert not result.shippable


def test_clean_full_check_binds_exact_plan(empty_ship_instance, approving_pipeline):
    result, report = run_planner(planner(candidate(empty_ship_instance)), empty_ship_instance,
                                 allow_repair=False)
    assert report.passed and result.shippable and result.metrics["shippable"]
    changed = result.model_copy(update={"plan": result.plan.model_copy(update={"call_index": 1})})
    assert not changed.shippable
    # Serialized results must be checked again; a stored boolean is not evidence.
    restored = PlannerResult.model_validate_json(result.model_dump_json())
    assert not restored.shippable


@pytest.mark.parametrize("target", ["instance", "report"])
def test_mutating_checked_input_or_verdict_invalidates_evidence(
        empty_ship_instance, approving_pipeline, target):
    instance = empty_ship_instance.model_copy(deep=True)
    result, report = run_planner(planner(candidate(instance)), instance, allow_repair=False)
    assert result.shippable
    if target == "instance":
        instance.seed += 1
    else:
        report.passed = False
    assert not result.shippable


def test_nested_input_provenance_cannot_ship(empty_ship_instance, approving_pipeline):
    instance = empty_ship_instance.model_copy(deep=True)
    dg = next(c.dg for c in instance.containers if c.dg is not None)
    dg.shipment_provenance = Provenance(stubs={"test.shipment": "nested shipping regression"})
    result, report = run_planner(planner(candidate(instance)), instance, allow_repair=False)
    assert report.passed
    assert not result.shippable
    assert not result.metrics["shippable"]


def test_incomplete_checker_report_cannot_ship(empty_ship_instance, approving_pipeline, monkeypatch):
    monkeypatch.setattr("stowage.checker.check", lambda *a, **kw: CheckReport(passed=True))
    result, _ = run_planner(planner(candidate(empty_ship_instance)), empty_ship_instance,
                            allow_repair=False)
    assert not result.metrics["shippable"]
    assert not result.shippable


@pytest.mark.parametrize("target", ["result", "plan", "instance", "report"])
def test_all_provenance_sources_gate_shipping(empty_ship_instance, approving_pipeline, monkeypatch, target):
    empty_ship_instance = empty_ship_instance.model_copy(deep=True)
    raw = candidate(empty_ship_instance)
    taint = Provenance(stubs={"test.component": "shipping regression"})
    if target == "report":
        report = full_report().model_copy(update={"provenance": taint})
        monkeypatch.setattr("stowage.checker.check", lambda *a, **kw: report)
    else:
        {"result": raw, "plan": raw.plan, "instance": empty_ship_instance}[target].provenance = taint
    result, _ = run_planner(planner(raw), empty_ship_instance, allow_repair=False)
    assert not result.metrics["shippable"]
    assert not result.shippable


def test_missing_required_later_calls_cannot_ship(empty_ship_instance, approving_pipeline):
    instance = empty_ship_instance.model_copy(deep=True, update={"stage": "D", "plan_calls": [1]})
    result, report = run_planner(planner(candidate(instance)), instance, allow_repair=False)
    assert not report.passed
    assert any(v.rule_id == "checker.unchecked_plan_calls" for v in report.violations)
    assert not result.metrics["shippable"]
    assert not result.shippable


def test_repair_cannot_discard_nested_candidate_taint(empty_ship_instance, monkeypatch):
    instance = empty_ship_instance.model_copy(deep=True)
    raw = candidate(instance)
    raw.later_plans = [raw.plan.model_copy(update={
        "id": "discarded-later", "call_index": 1,
        "provenance": Provenance(stubs={"test.adapter": "nested adapter taint"}),
    })]
    monkeypatch.setattr("stowage.planner.finalize", lambda i, r, **kw: r)
    reports = iter([full_report().model_copy(update={"passed": False}), full_report()])
    monkeypatch.setattr("stowage.checker.check", lambda *a, **kw: next(reports))
    monkeypatch.setattr("stowage.solver.repair.repair", lambda i, r, report, **kw: candidate(i))
    result, report = run_planner(planner(raw), instance)
    assert report.passed
    assert "test.adapter" in result.provenance.stubs
    assert not result.shippable
    assert not result.metrics["shippable"]


def test_real_full_checker_clean_pass_remains_shippable():
    """Reuse the existing synthetic physical fixture; no production-ship claim."""
    from pathlib import Path
    import runpy
    from stowage.checker import report_matches_inputs

    fixture = runpy.run_path(str(Path(__file__).parent / 'checker/test_full_properties.py'))
    instance, plan = fixture['_random_plan'](0)
    # ASSUMPTION: these synthetic test boxes each have a 30,480 kg CSC plate
    # rating; this evidence is supplied explicitly, never inferred from ISO type.
    for box in instance.containers:
        box.max_gross_mass_kg = 30480
    raw = candidate(instance, plan=plan)
    result, report = run_planner(planner(raw, sets_ballast=True), instance, allow_repair=False)
    assert report.passed, report.violations
    assert report.count is not None
    assert report_matches_inputs(report, instance, result.plan)
    assert result.shippable and result.metrics['shippable']
    report.provenance.stubs['test.checker'] = 'post-check taint'
    assert not result.shippable


@pytest.mark.parametrize('fault', ['missing_part', 'not_ran', 'part_error', 'hard_violation', 'shut_out'])
def test_incomplete_or_failed_checks_never_ship(empty_ship_instance, monkeypatch, fault):
    from stowage.model import Violation

    report = full_report()
    if fault == 'missing_part':
        del report.parts['strength']
    elif fault == 'not_ran':
        report.parts['strength'].ran = False
    elif fault == 'part_error':
        report.parts['strength'].error = 'unavailable data'
    elif fault == 'hard_violation':
        report.violations = [Violation(rule_id='checker.missing_vessel_data', severity='hard',
                                       call_index=0, message='test missing data')]
    else:
        report.shut_out = [empty_ship_instance.containers[0].id]
    monkeypatch.setattr('stowage.planner.finalize', lambda i, r, **kw: r)
    monkeypatch.setattr('stowage.checker.check', lambda *a, **kw: report)
    result, _ = run_planner(planner(candidate(empty_ship_instance)), empty_ship_instance, allow_repair=False)
    assert not result.shippable and not result.metrics['shippable']


@pytest.mark.parametrize('tainted', [False, True])
def test_repaired_pipeline_report_binds_final_plan_after_lineage_merge(monkeypatch, tainted):
    from pathlib import Path
    import runpy
    from stowage.checker import report_matches_inputs

    fixture = runpy.run_path(str(Path(__file__).parent / 'checker/test_full_properties.py'))
    instance, good_plan = fixture['_random_plan'](0)
    for box in instance.containers:
        # ASSUMPTION: explicit synthetic CSC ratings for these test containers.
        box.max_gross_mass_kg = 30480
    bad_plan = good_plan.model_copy(deep=True, update={'id': 'binding.original'})
    bad_plan.moves[0].src = 'A-999-01-01'
    bad_plan.provenance = Provenance(parents=['original.plan.source'],
        stubs={'test.original': 'retain across repair'} if tainted else {})
    raw = candidate(instance, plan=bad_plan, provenance=Provenance(parents=['original.result.source']))
    checked_plans = []
    from stowage import checker
    real_check = checker.check

    def recording_check(i, p, **kwargs):
        checked_plans.append(p)
        return real_check(i, p, **kwargs)

    monkeypatch.setattr(checker, 'check', recording_check)

    def replace(i, r, first_report, **kwargs):
        assert not first_report.passed
        assert report_matches_inputs(first_report, i, r.plan)
        # Simulate a repair that mutates the original before returning a fresh result.
        r.plan.provenance.stubs.clear()
        r.provenance.stubs.clear()
        repaired = good_plan.model_copy(deep=True, update={
            'id': 'binding.repaired', 'provenance': Provenance(parents=['repair.source'])})
        return candidate(i, plan=repaired, repaired=True,
                         provenance=Provenance(parents=['repair.result.source']))

    monkeypatch.setattr('stowage.solver.repair.repair', replace)
    result, report = run_planner(planner(raw, sets_ballast=True), instance)
    assert result.repaired and report.passed, report.violations
    assert len(checked_plans) == 2
    assert checked_plans[-1] is result.plan
    assert report_matches_inputs(report, instance, result.plan)
    assert report_matches_inputs(CheckReport.model_validate_json(report.model_dump_json()), instance,
                                 MovePlan.model_validate_json(result.plan.model_dump_json()))
    assert {'original.plan.source', 'original.result.source', 'repair.source',
            'repair.result.source', 'binding.original', 'binding.repaired'} <= set(result.plan.provenance.parents)
    for prefix in ('checker.instance.sha256:', 'checker.plan.sha256:'):
        assert len([p for p in report.provenance.parents if p.startswith(prefix)]) == 1
    assert result.shippable is (not tainted)
    assert result.metrics['shippable'] is (not tainted)
    if tainted:
        assert 'test.original' in result.provenance.stubs
        assert 'test.original' in result.plan.provenance.stubs
        assert 'test.original' in report.provenance.stubs
    result.plan.provenance.parents.append('post-check edit')
    assert not report_matches_inputs(report, instance, result.plan)
    assert not result.shippable


@pytest.mark.parametrize('repair_outcome', ['raises', 'no_plan'])
def test_failed_repair_preserves_first_report_taint(empty_ship_instance, monkeypatch, repair_outcome):
    initial = candidate(empty_ship_instance)
    first = full_report().model_copy(update={
        'passed': False, 'provenance': Provenance(stubs={'test.first_check': 'retain failed report'})})
    monkeypatch.setattr('stowage.planner.finalize', lambda i, r, **kw: r)
    monkeypatch.setattr('stowage.checker.check', lambda *a, **kw: first)

    def repair(i, r, report, **kwargs):
        report.provenance.stubs.clear()
        r.provenance.stubs.clear()
        if repair_outcome == 'raises':
            raise RuntimeError('repair failed after mutation')
        return candidate(i, plan=None, status='error')

    monkeypatch.setattr('stowage.solver.repair.repair', repair)
    result, report = run_planner(planner(initial), empty_ship_instance)
    assert 'test.first_check' in result.provenance.stubs
    assert not result.shippable and not result.metrics['shippable']
    assert (report is None) is (repair_outcome == 'no_plan')
