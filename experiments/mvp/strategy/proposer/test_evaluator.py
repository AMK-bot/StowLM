"""Adapter contracts; real parity is exercised by verify_parity.py."""
import json
from pathlib import Path

import pytest

from experiments.mvp.strategy.proposer import evaluator


@pytest.fixture
def instance():
    from stowage.io import load_instance
    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / 'experiments/mvp/strategy/headroom/train_maps.json').read_text())
    return load_instance(root / manifest['maps'][0]['input'])


def test_invalid_consumes_one_try_without_solver(instance, tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator.headroom, 'evaluate', lambda *a: pytest.fail('invalid text reached solver'))
    row = evaluator.evaluate_text(instance, '```invalid```', tmp_path)
    assert row['status'] == 'invalid_layout'
    assert row['proposal_tries'] == 1 and row['evaluations_used'] == 0
    assert row['checked_evaluations'] == row['solver_evaluations'] == 0
    assert row['moves'] is None and not row['repair_attempted']
    assert evaluator.evaluate_text(instance, '```invalid```', tmp_path) == row
    with pytest.raises(ValueError, match='different request'):
        evaluator.evaluate_text(instance, 'different invalid text', tmp_path)


@pytest.mark.parametrize('checked,passed', [(True, True), (True, False), (False, False)])
def test_exact_backend_and_only_checked_moves(instance, tmp_path, monkeypatch, checked, passed):
    import stowage.checker as checker
    monkeypatch.setattr(checker, 'check', lambda *a, **kw: object())
    from experiments.mvp.strategy.headroom.layout import unrestricted_layout
    from experiments.mvp.strategy.proposer.format import format_layout
    text = format_layout(unrestricted_layout(instance), instance)
    calls = []
    def solve(inst, model, layout, identifier, kind, folder, config):
        calls.append(('solve', config, layout.fingerprint))
        assert inst is instance and kind == 'proposer'
        return {'layout_id': identifier, 'status': 'ok', 'solver_complete': True, 'raw_moves': 999}
    def finish(inst, model, row, folder, config):
        calls.append(('finish', config))
        check = {'passed': passed, 'finalize_status': 'ok' if checked else 'timeout'}
        if checked:
            checker.check(inst, None)
            check['checker_passed'] = passed
        if passed:
            check['moves'] = 17
        return dict(row, check=check)
    monkeypatch.setattr(evaluator.headroom, 'evaluate', solve)
    monkeypatch.setattr(evaluator.headroom, 'finish_check', finish)
    row = evaluator.evaluate_text(instance, text, tmp_path)
    assert [call[0] for call in calls] == ['solve', 'finish']
    assert calls[0][1] == calls[1][1] == evaluator.DEFAULT_CONFIG
    assert row['proposal_tries'] == row['solver_evaluations'] == row['finisher_evaluations'] == 1
    assert row['evaluations_used'] == row['checked_evaluations'] == int(checked)
    assert row['terminal_certificate_check_calls'] == row['terminal_certificate_check_completed'] == int(checked)
    assert row['all_full_checker_invocations'] is None
    assert row['terminal_check_accounting'] == 'observed'
    assert row['moves'] == (17 if passed else None)
    assert row['passed'] is passed


def test_checker_exception_still_counts_invocation(instance, tmp_path, monkeypatch):
    import stowage.checker as checker
    from experiments.mvp.strategy.headroom.layout import unrestricted_layout
    from experiments.mvp.strategy.proposer.format import format_layout
    original = checker.check
    monkeypatch.setattr(evaluator.headroom, 'evaluate',
                        lambda *a: {'layout_id': a[3], 'status': 'ok', 'solver_complete': True})
    def finish(inst, model, row, folder, config):
        try:
            checker.check(inst, None)
        except RuntimeError:
            return dict(row, check={'passed': False, 'finalize_status': 'exception'})
        pytest.fail('checker exception expected')
    def explode(*args, **kwargs):
        raise RuntimeError('checker failed')
    monkeypatch.setattr(checker, 'check', explode)
    monkeypatch.setattr(evaluator.headroom, 'finish_check', finish)
    row = evaluator.evaluate_text(instance, format_layout(unrestricted_layout(instance)), tmp_path)
    assert row['checked_evaluations'] == row['evaluations_used'] == 0 and row['moves'] is None
    assert row['terminal_certificate_check_calls'] == 1
    assert row['terminal_certificate_check_completed'] == 0
    assert row['terminal_certificate_check_status'] == 'exception'
    assert checker.check is explode


@pytest.mark.parametrize('config', [
    {'planner_seed': 1}, {'finisher_budget': 601}, {'small_budget': 0},
    {'medium_budget': float('nan')}, {'finisher_budget': float('inf')},
])
def test_config_guard(config):
    with pytest.raises(ValueError):
        evaluator.evaluation_config(config)


def test_safe_attempt_id(instance, tmp_path):
    with pytest.raises(ValueError, match='attempt_id'):
        evaluator.evaluate_text(instance, 'invalid', tmp_path, attempt_id='../escape')


def _stub_solve(monkeypatch):
    monkeypatch.setattr(evaluator.headroom, 'evaluate',
                        lambda *a: {'layout_id': a[3], 'status': 'ok', 'solver_complete': True})


def _valid_text(instance):
    from experiments.mvp.strategy.headroom.layout import unrestricted_layout
    from experiments.mvp.strategy.proposer.format import format_layout
    return format_layout(unrestricted_layout(instance))


def test_v4_child_work_is_unknown_when_no_parent_certificate(instance, tmp_path, monkeypatch):
    _stub_solve(monkeypatch)
    # Simulate V4 child checking candidates, then rejecting the finisher result.
    # Such child-process calls do not go through the parent's checker wrapper.
    monkeypatch.setattr(evaluator.headroom, 'finish_check', lambda inst, model, row, folder, config:
                        dict(row, check={'passed': False, 'finalize_status': 'not_found',
                                         'metrics': {'child_candidates_checked': 2}}))
    row = evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    assert row['terminal_certificate_check_calls'] == row['terminal_certificate_check_completed'] == 0
    assert row['checked_evaluations'] == 0 and row['proposal_tries'] == 1
    assert row['all_full_checker_invocations'] is None
    assert row['v4_internal_checker_work'] == evaluator.V4_INTERNAL_CHECKER_WORK


def test_terminal_interrupt_is_durable_and_not_completed(instance, tmp_path, monkeypatch):
    import stowage.checker as checker
    _stub_solve(monkeypatch)
    def interrupted(*a, **kw):
        raise KeyboardInterrupt('simulated interruption')
    monkeypatch.setattr(checker, 'check', interrupted)
    def finish(inst, model, row, folder, config):
        checker.check(inst, None)
    monkeypatch.setattr(evaluator.headroom, 'finish_check', finish)
    with pytest.raises(KeyboardInterrupt):
        evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    counter = json.loads((tmp_path / 'terminal_certificate_checks/proposal-001.json').read_text())
    assert counter['terminal_certificate_check_calls'] == 1
    assert counter['terminal_certificate_check_completed'] == 0
    assert counter['terminal_certificate_check_status'] == 'interrupted_or_unknown'
    assert checker.check is interrupted
    monkeypatch.setattr(evaluator.headroom, 'finish_check',
                        lambda inst, model, row, folder, config: dict(row, check={'passed': False}))
    with pytest.raises(RuntimeError, match='prior terminal checker work'):
        evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    assert json.loads((tmp_path / 'terminal_certificate_checks/proposal-001.json').read_text()) == counter


def test_preexisting_in_progress_counter_is_unknown(instance, tmp_path, monkeypatch):
    _stub_solve(monkeypatch)
    evaluator.headroom.atomic_json(tmp_path / 'terminal_certificate_checks/proposal-001.json',
                                  dict(evaluator._new_counters(),
                                       terminal_certificate_check_calls=1,
                                       terminal_certificate_check_status='in_progress'))
    monkeypatch.setattr(evaluator.headroom, 'finish_check',
                        lambda inst, model, row, folder, config: row)
    with pytest.raises(RuntimeError, match='prior terminal checker work'):
        evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    counter = json.loads((tmp_path / 'terminal_certificate_checks/proposal-001.json').read_text())
    assert counter['terminal_certificate_check_status'] == 'interrupted_or_unknown'
    assert counter['terminal_certificate_check_calls'] == 1
    assert counter['terminal_certificate_check_completed'] == 0


def test_legacy_cached_certificate_inferred_and_labelled(instance, tmp_path, monkeypatch):
    _stub_solve(monkeypatch)
    monkeypatch.setattr(evaluator.headroom, 'finish_check', lambda inst, model, row, folder, config:
                        dict(row, check={'passed': True, 'checker_passed': True, 'moves': 17}))
    row = evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    assert row['evaluations_used'] == row['checked_evaluations'] == 1
    assert row['terminal_certificate_check_calls'] == row['terminal_certificate_check_completed'] == 1
    assert row['terminal_check_accounting'] == 'legacy_inferred'
    assert row['all_full_checker_invocations'] is None


def test_legacy_attempt_invalid_preserves_wasted_proposal_not_budget(instance, tmp_path, monkeypatch):
    row = evaluator.evaluate_text(instance, 'bad layout', tmp_path)
    legacy = {key: value for key, value in row.items()
              if key not in {'checker_accounting_version', 'proposal_tries'}
              and not key.startswith('terminal_')}
    legacy['evaluations_used'] = 1
    evaluator.headroom.atomic_json(tmp_path / 'attempts/proposal-001.json', legacy)
    resumed = evaluator.evaluate_text(instance, 'bad layout', tmp_path)
    assert resumed['wasted_try'] and resumed['proposal_tries'] == 1
    assert resumed['evaluations_used'] == resumed['checked_evaluations'] == 0
    assert resumed['terminal_check_accounting'] == 'legacy_inferred'


@pytest.mark.parametrize('completed', [0, 1])
def test_prior_terminal_work_without_certificate_cannot_repeat(instance, tmp_path, monkeypatch, completed):
    _stub_solve(monkeypatch)
    counter = dict(evaluator._new_counters(), terminal_certificate_check_calls=1,
                   terminal_certificate_check_completed=completed,
                   terminal_certificate_check_status='completed' if completed else 'interrupted_or_unknown')
    path = tmp_path / 'terminal_certificate_checks/proposal-001.json'
    evaluator.headroom.atomic_json(path, counter)
    monkeypatch.setattr(evaluator.headroom, 'finish_check',
                        lambda *a: pytest.fail('prior terminal work was repeated'))
    with pytest.raises(RuntimeError, match='Requires new output'):
        evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    assert json.loads(path.read_text()) == counter


def test_prior_terminal_work_with_certificate_resumes_without_call(instance, tmp_path, monkeypatch):
    _stub_solve(monkeypatch)
    counter = dict(evaluator._new_counters(), terminal_certificate_check_calls=1,
                   terminal_certificate_check_completed=1, terminal_certificate_check_status='completed')
    evaluator.headroom.atomic_json(tmp_path / 'terminal_certificate_checks/proposal-001.json', counter)
    evaluator.headroom.atomic_json(tmp_path / 'checks/proposal-001.json',
                                  {'passed': True, 'checker_passed': True, 'moves': 17})
    monkeypatch.setattr(evaluator.headroom, 'finish_check',
                        lambda *a: pytest.fail('durable certificate was rerun'))
    row = evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    assert row['checked_evaluations'] == 1
    assert row['terminal_certificate_check_calls'] == row['terminal_certificate_check_completed'] == 1
    assert row['terminal_check_accounting'] == 'observed'
    assert row['passed'] and row['moves'] == 17


def test_durable_certificate_recovers_missing_completed_counter(instance, tmp_path, monkeypatch):
    _stub_solve(monkeypatch)
    evaluator.headroom.atomic_json(tmp_path / 'terminal_certificate_checks/proposal-001.json',
                                  dict(evaluator._new_counters(), terminal_certificate_check_calls=1,
                                       terminal_certificate_check_status='in_progress'))
    evaluator.headroom.atomic_json(tmp_path / 'checks/proposal-001.json',
                                  {'passed': False, 'checker_passed': False})
    monkeypatch.setattr(evaluator.headroom, 'finish_check',
                        lambda *a: pytest.fail('durable certificate was rerun'))
    row = evaluator.evaluate_text(instance, _valid_text(instance), tmp_path)
    assert row['checked_evaluations'] == 1
    assert row['terminal_certificate_check_calls'] == row['terminal_certificate_check_completed'] == 1
    assert row['terminal_check_accounting'] == 'certificate_inferred'
    assert row['terminal_certificate_check_status'] == 'completed'
    assert not row['passed'] and row['moves'] is None
