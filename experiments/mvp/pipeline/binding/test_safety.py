"""Failure paths found in independent adapter review."""
import importlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.binding import adapter
from experiments.mvp.pipeline.binding.test_adapter import Candidate, _plan, _verdict
from experiments.mvp.pipeline.binding.test_hooks import fixture_instance


def test_unreplayable_candidate_returns_no_checked_plan(monkeypatch):
    instance = fixture_instance()
    report = NS(passed=False, count=None, model_dump=lambda **_: {'passed': False, 'count': None})
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {})
    monkeypatch.setattr(adapter, 'check', lambda *args: report)
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *args, **kwargs:
                        (Candidate(status='ok', plan=_plan(instance)), {}))
    result, evidence = adapter.plan_with_rules(instance, [], budget_s=0)
    assert result is None and evidence['status'] == 'no_checked_plan'
    assert evidence['checker']['count'] is None
    assert evidence['rejected_candidates'][0]['reason'] == 'original_instance_checker'


def test_active_native_stub_is_rejected_before_admission(monkeypatch):
    monkeypatch.setattr(adapter, 'active_stubs', lambda: {'stability': 'stub'})
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: (_ for _ in ()).throw(AssertionError('admission reached')))
    result, evidence = adapter.plan_with_rules(fixture_instance(), [], budget_s=0)
    assert result is None and evidence['status'] == 'constraint_unavailable'
    assert not evidence['passed'] and 'active checker stubs' in evidence['reason']


def test_invalid_relaxed_plan_is_not_released(monkeypatch):
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {})
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *args, **kwargs:
        (None, {'stages': {'peel_seam': {'relaxed_plan': {'malformed': True}}}}))
    result, evidence = adapter.plan_with_rules(fixture_instance(), [], budget_s=0)
    assert result is None and evidence['status'] == 'no_checked_plan'
    assert evidence['rejected_candidates'][0]['reason'] == 'invalid_relaxed_plan'


def test_alias_import_cannot_bypass_process_scope_lock(monkeypatch):
    from experiments.mvp.pipeline.binding import hooks
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    alias = importlib.import_module('binding.hooks')
    instance = fixture_instance()
    with hooks.binding_scope(instance):
        assert alias.binding_active()
        with pytest.raises(RuntimeError, match='already active'):
            with alias.binding_scope(instance):
                pass
    assert not alias.binding_active() and not hooks.binding_active()


def test_admission_waits_through_stale_high_load_and_insufficient_mask(monkeypatch, tmp_path):
    import json
    from experiments.mvp.pipeline.binding import admission
    state = tmp_path/'core-state.json'
    readings = iter([
        {'mask': 255, 'load1': 0, 't': 1},
        {'mask': 255, 'load1': 46, 't': 999},
        {'mask': 127, 'load1': 0, 't': 999},
        {'mask': 255, 'load1': 45, 't': 999},
    ])
    waits = []
    def update(_delay):
        waits.append(True)
        state.write_text(json.dumps(next(readings)))
    monkeypatch.setattr(admission.time, 'time', lambda: 1000.)
    monkeypatch.setattr(admission.time, 'sleep', update)
    # Initial nonexistent telemetry must wait too.
    evidence = admission.wait_for_capacity(state_path=state, poll_s=0)
    assert len(waits) == 4
    assert evidence['enabled_cores'] == 8 and evidence['load1'] == 45


def test_projected_instance_checker_pass_cannot_publish_incumbent(monkeypatch):
    import stowage.checker as checker
    from experiments.mvp.pipeline.binding.hooks import binding_scope
    instance = fixture_instance()
    report, _ = _verdict(True)
    monkeypatch.setattr(checker, 'check', lambda *args: report)
    events = []
    with binding_scope(instance, on_checked=lambda *args: events.append(args)):
        checker.check(instance.model_copy(), object())
        assert events == []
        checker.check(instance, object())
        assert len(events) == 1


def test_recovered_checked_incumbent_has_honest_lane_attribution(monkeypatch):
    import stowage.checker as checker
    from llm.instructions.schema import CloseBay
    instance = fixture_instance()
    good = _plan(instance)
    bad = good.model_copy(deep=True)
    report, row = _verdict(True)
    report.model_dump_json = lambda: '{}'
    compiled = NS(instance=instance, forbids=lambda *args: None,
                  violations=lambda plan: ['confirmed instruction'] if plan is bad else [])
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {})
    monkeypatch.setattr(adapter, 'require_clean', lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, 'compile_constraint', lambda *args, **kwargs: (compiled, {}))
    monkeypatch.setattr(adapter, 'inspect', lambda *args: row)
    native_check = lambda *args: report
    monkeypatch.setattr(checker, 'check', native_check)
    monkeypatch.setattr(adapter, 'check', native_check)
    def run(original, **kwargs):
        checker.check(original, good)
        return Candidate(status='ok', plan=bad), {'lifts': 1., 'winner_lane': 'assignment_lp'}
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', run)
    result, evidence = adapter.plan_with_rules(instance, [CloseBay(type='close_bay', bay=1)], budget_s=0)
    assert result.plan is good and evidence['passed']
    assert evidence['lane'] == 'checked_incumbent'
    assert evidence['stage_attribution'] == 'native_full_checker_observer'


def test_bad_worker_audit_does_not_erase_result_or_leak_scope_lock(monkeypatch, tmp_path):
    from experiments.mvp.pipeline.binding import hooks
    path = tmp_path/'worker-events.jsonl'
    original = hooks.tempfile.mkstemp
    def captured(*args, **kwargs):
        return original(prefix='binding-cleanup-', dir=tmp_path)
    monkeypatch.setattr(hooks.tempfile, 'mkstemp', captured)
    instance = fixture_instance()
    with hooks.binding_scope(instance) as audit:
        actual = next(tmp_path.iterdir())
        actual.write_text('{invalid json}\n{"event":"valid"}\n')
    assert audit['worker_events'] == [{'event': 'valid'}]
    assert audit['cleanup_errors']
    assert not hooks.binding_active()
    assert not actual.exists()
    with hooks.binding_scope(instance):
        assert hooks.binding_active()


def test_unexpected_late_engine_error_retains_checked_incumbent(monkeypatch):
    import stowage.checker as checker
    instance = fixture_instance()
    plan = _plan(instance)
    report, row = _verdict(True)
    report.model_dump_json = lambda: '{}'
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {})
    monkeypatch.setattr(adapter, 'require_clean', lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, 'inspect', lambda *args: row)
    native_check = lambda *args: report
    monkeypatch.setattr(checker, 'check', native_check)
    monkeypatch.setattr(adapter, 'check', native_check)
    def broken(original, **kwargs):
        checker.check(original, plan)
        raise RuntimeError('late optional failure')
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', broken)
    result, evidence = adapter.plan_with_rules(instance, [], budget_s=0)
    assert result.plan is plan and evidence['passed']
    assert evidence['lane'] == 'checked_incumbent'
    assert evidence['engine']['pipeline_errors'] == ['RuntimeError: late optional failure']


def test_native_cache_paths_and_keyword_defaults_restore_after_exception(monkeypatch):
    import os
    from stowage.checker import stability_tables, stability_sounding, stability_liquid, stability_liquid_tables
    from stowage.linear import cache
    from experiments.mvp.pipeline.binding.runtime import HERE, ROOT, runtime_cache_scope
    old = (stability_tables.CACHE_DIR, stability_sounding.CACHE_DIRECTORY,
           stability_liquid.CACHE_DIRECTORY, stability_liquid_tables.CACHE_DIRECTORY,
           cache._DEFAULT_ROOT, stability_tables.cached_geometry.__kwdefaults__)
    monkeypatch.setenv('STOWAGE_VESSEL_CACHE_DIR', '/private/tmp/outside-binding-cache')
    with pytest.raises(ValueError, match='intentional'):
        with runtime_cache_scope():
            assert stability_tables.CACHE_DIR == ROOT/'stability'
            assert stability_sounding.CACHE_DIRECTORY == ROOT/'stability'
            assert stability_liquid.CACHE_DIRECTORY == ROOT/'stability'
            assert stability_liquid_tables.CACHE_DIRECTORY == ROOT/'stability'
            assert cache._DEFAULT_ROOT == ROOT/'linear'
            assert stability_tables.cached_geometry.__kwdefaults__['cache_dir'] == ROOT/'stability'
            assert Path(os.environ['STOWAGE_VESSEL_CACHE_DIR']).is_relative_to(HERE)
            raise ValueError('intentional')
    assert (stability_tables.CACHE_DIR, stability_sounding.CACHE_DIRECTORY,
            stability_liquid.CACHE_DIRECTORY, stability_liquid_tables.CACHE_DIRECTORY,
            cache._DEFAULT_ROOT, stability_tables.cached_geometry.__kwdefaults__) == old
    assert os.environ['STOWAGE_VESSEL_CACHE_DIR'] == '/private/tmp/outside-binding-cache'


def test_45_foot_box_consumes_two_physical_cells_in_joint_capacity_screen():
    instance = fixture_instance()
    box = instance.containers[0].model_copy(update={'length_ft': 45})
    instance = instance.model_copy(update={'containers': [box], 'load_list': [box.id]})
    closed = {cell.id for cell in instance.vessel.cells[2:]}
    part = NS(_all_boxes=closed, capacity_shortfall=lambda: None)
    assert adapter.Intersection(instance, [part]).capacity_shortfall() is None


def test_native_optimality_scope_is_qualified_by_compiled_instruction_domain(monkeypatch):
    from experiments.mvp.reader.schema import OnePortPerHatch
    instance = fixture_instance()
    plan = _plan(instance)
    report, row = _verdict(True)
    compiled = NS(instance=instance, forbids=lambda *args: None, violations=lambda plan: [])
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {})
    monkeypatch.setattr(adapter, 'require_clean', lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, 'compile_constraint', lambda *args, **kwargs: (compiled, {}))
    monkeypatch.setattr(adapter, 'check', lambda *args: report)
    monkeypatch.setattr(adapter, 'inspect', lambda *args: row)
    native = {'proved_optimal': True, 'proof_scope': 'fewest checker-passing lifts'}
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *args, **kwargs:
                        (Candidate(status='ok', plan=plan), native))
    result, evidence = adapter.plan_with_rules(instance,
        [OnePortPerHatch(type='one_port_per_hatch')], budget_s=0)
    assert result is not None and evidence['passed']
    assert evidence['native_proved_optimal']
    assert 'chosen physical-cover/POD allocation' in evidence['proof_scope']
    assert evidence['engine']['proof_scope'] == 'fewest checker-passing lifts'
