"""D124 final row-d checks, CPU fixtures only; live generation0 stays untouched."""
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def modules(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    stage = root/'experiments/mvp/exit/patches/d124'
    result = {}
    for name in ('checker_watchdog', 'outcomes', 'final_recipe'):
        source = root/'src/llm/exit'/f'{name}.py'
        if not (root/'src/llm/exit/outcomes.py').exists():
            source = stage/f'{name}.py'
        spec = importlib.util.spec_from_file_location(f'_d124_final_{name}', source)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, f'llm.exit.{name}', module)
        result[name] = module
    return result


@pytest.fixture
def harness(modules, monkeypatch, tmp_path):
    from llm.exit import runtime, identity, selection
    from llm.stowlm import planner
    from stowage.model.fixtures import build_instance
    from stowage.model import MovePlan
    instance = build_instance()
    path = tmp_path/'map.json'; path.write_text(instance.model_dump_json())
    plan = MovePlan(id='own', instance_id=instance.id, call_index=instance.call_index)
    calls = dict(verify=0, resident=0, swallowed=False)
    class Planner:
        def __init__(self, **kwargs): pass
        def plan(self, instance, **kwargs):
            from stowage.checker import check
            try:
                check(instance, plan)
            except Exception:
                calls['swallowed'] = True
            return SimpleNamespace(status='ok', plan=plan, metrics={})
    def resident(value): calls['resident'] += 1
    def verify(*args, **kwargs):
        calls['verify'] += 1
        return SimpleNamespace(passed=True, plan=plan, weighted_moves=3.,
            details=dict(checker_available=True, report={'passed':True}))
    monkeypatch.setattr(planner, 'StowLMPlanner', Planner)
    monkeypatch.setattr(runtime, '_resident_for', resident)
    monkeypatch.setattr(identity, 'identity_guard', lambda *_:
        SimpleNamespace(require_allowed=lambda *args, **kwargs:None))
    monkeypatch.setattr(selection, 'decoder_factory', lambda:None)
    monkeypatch.setattr(selection, 'verify_candidate', verify)
    final = modules['final_recipe']
    monkeypatch.setattr(final, 'scoped_v4_finalize', nullcontext)
    monkeypatch.setattr(final, '_pins', lambda _:
        dict(adapter={'weights':'adapter'}, sources={'final_recipe.py':'new-source'}))
    monkeypatch.delenv('STOWAGE_BALLAST_V4', raising=False)
    args = SimpleNamespace(corpus=tmp_path, seed=0, max_tokens=10)
    return final, path, plan, args, calls, Planner


def test_internal_timeout_bypasses_exception_handler_and_final_verification(modules, harness, monkeypatch, tmp_path):
    import stowage.checker as checker
    original = checker.check
    watchdog = modules['checker_watchdog']
    monkeypatch.setattr(watchdog, 'run_full_check', lambda *args, **kwargs:
        watchdog.CheckOutcome(False, reason='watchdog_expired', killed=True))
    final, path, _, args, calls, _ = harness
    rows = final.evaluate_recipe(tmp_path/'adapter', [path], tmp_path/'audit', args)
    row = rows[0]
    assert not row['passed'] and row['unavailable'] and row['plan'] is None
    assert row['weighted_moves'] is None and calls['verify'] == 0
    assert calls['swallowed'] is False and checker.check is original
    assert row['verification_counts']['unavailable'] == 1
    assert row['verification_counts']['physics_failed'] == 0
    assert row['verification']['checker_watchdog']['expired']
    assert row['planner_status'] == 'checker_unavailable'


def test_scoped_checker_restores_after_regular_error(modules):
    import stowage.checker as checker
    final = modules['final_recipe']; original = checker.check
    with pytest.raises(RuntimeError, match='planner crash'):
        with final.scoped_checker_watchdog([]):
            assert checker.check is not original
            raise RuntimeError('planner crash')
    assert checker.check is original


def test_internal_transport_failure_is_unavailable(modules, monkeypatch):
    final = modules['final_recipe']; attempts = []
    def fail(*args, **kwargs): raise OSError('cannot launch')
    monkeypatch.setattr(modules['checker_watchdog'], 'run_full_check', fail)
    with pytest.raises(final.FinalAuditCheckUnavailable) as error:
        with final.scoped_checker_watchdog(attempts):
            from stowage.checker import check
            check(None, None)
    assert error.value.details['unavailable_reason'] == 'checker_process_or_transport_error'
    assert len(attempts) == 1 and not attempts[0]['checker_available']


def test_internal_and_final_checks_count_once_and_policy_is_cache_bound(modules, harness, monkeypatch, tmp_path):
    from stowage.model import CheckReport
    watchdog = modules['checker_watchdog']
    monkeypatch.setattr(watchdog, 'run_full_check', lambda *args, **kwargs:
        watchdog.CheckOutcome(True, report=CheckReport(passed=True)))
    final, path, _, args, calls, _ = harness
    out = tmp_path/'audit'
    rows = final.evaluate_recipe(tmp_path/'adapter', [path], out, args)
    row = rows[0]
    assert row['passed'] and not row['unavailable']
    assert [r['stage'] for r in row['checker_attempts']] == ['planner_internal', 'final_verify']
    assert row['verification_counts']['total'] == row['verification_counts']['passed'] == 2
    identity = json.loads((out/'identity.json').read_text())
    assert identity['sources'] == {'final_recipe.py':'new-source'}
    assert identity['recipe']['check_watchdog'] == dict(version='d124-owned-subprocess-v1', timeout_s=300.)
    monkeypatch.setattr(final, '_pins', lambda _:
        dict(adapter={'weights':'adapter'}, sources={'final_recipe.py':'changed-source'}))
    with pytest.raises(RuntimeError, match='cache adapter, maps, source or recipe'):
        final.evaluate_recipe(tmp_path/'adapter', [path], out, args)
    assert calls['resident'] == calls['verify'] == 1


def test_valid_internal_report_followed_by_expiry_discards_whole_audit_row(modules, harness, monkeypatch, tmp_path):
    from stowage.model import CheckReport
    final, path, plan, args, calls, planner = harness
    watchdog = modules['checker_watchdog']; responses = iter([
        watchdog.CheckOutcome(True, report=CheckReport(passed=True)),
        watchdog.CheckOutcome(False, reason='watchdog_expired')])
    monkeypatch.setattr(watchdog, 'run_full_check', lambda *args, **kwargs:next(responses))
    def run(self, instance, **kwargs):
        from stowage.checker import check
        check(instance, plan)
        check(instance, plan)
        return SimpleNamespace(status='ok', plan=plan, metrics={})
    monkeypatch.setattr(planner, 'plan', run)
    row = final.evaluate_recipe(tmp_path/'adapter', [path], tmp_path/'audit', args)[0]
    assert not row['passed'] and row['unavailable'] and calls['verify'] == 0
    assert row['verification_counts']['passed'] == 1
    assert row['verification_counts']['unavailable'] == 1
    assert row['verification_counts']['total'] == 2


def test_final_authoritative_check_expiry_is_unavailable_not_a_pass(modules, harness, monkeypatch, tmp_path):
    from llm.exit import selection
    from stowage.model import CheckReport
    watchdog = modules['checker_watchdog']
    monkeypatch.setattr(watchdog, 'run_full_check', lambda *args, **kwargs:
        watchdog.CheckOutcome(True, report=CheckReport(passed=True)))
    monkeypatch.setattr(selection, 'verify_candidate', lambda *args, **kwargs:
        SimpleNamespace(passed=False, plan=None, weighted_moves=None,
            details=dict(checker_available=False, unavailable=True,
                         checker_watchdog={'expired':True})))
    final, path, _, args, _, _ = harness
    row = final.evaluate_recipe(tmp_path/'adapter', [path], tmp_path/'audit', args)[0]
    assert row['unavailable'] and not row['passed'] and row['plan'] is None
    assert row['verification_counts']['passed'] == 1
    assert row['verification_counts']['unavailable'] == 1
    assert row['verification_counts']['physics_failed'] == 0
    assert row['verification_counts']['total'] == 2
