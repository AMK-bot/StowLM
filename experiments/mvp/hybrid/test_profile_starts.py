"""Safety boundaries of the bounded diagnostic, without running a solver."""
from time import perf_counter

import pytest

from experiments.mvp.hybrid import profile_starts as profile


def test_phase_timer_preserves_results_errors_and_counts():
    metrics = {}
    sentinel = object()
    measured = profile.timed(metrics, 'phase', lambda value: value, perf_counter()+10)
    assert measured(sentinel) is sentinel
    assert measured(None) is None
    assert metrics['phase']['calls'] == 2
    assert metrics['phase']['none'] == 1
    assert metrics['phase']['wall_s'] >= 0
    assert metrics['phase']['cpu_s'] >= 0
    def fail():
        raise ValueError('original failure')
    with pytest.raises(ValueError, match='original failure'):
        profile.timed(metrics, 'failure', fail, perf_counter()+10)()
    assert metrics['failure']['calls'] == 1


def test_elapsed_deadline_prevents_more_work():
    with pytest.raises(profile.DiagnosticDeadline):
        profile.timed({}, 'phase', lambda: pytest.fail('work continued'), perf_counter()-1)()


@pytest.mark.parametrize('args', [
    ['--max-wall-s', '1201'], ['--iterations', '0'],
    ['--order', 'blocked,llm,llm'], ['--out', str(profile.HERE.parent/'outside')],
])
def test_invalid_or_unbounded_diagnostic_rejected_before_solver_import(monkeypatch, args):
    monkeypatch.setattr(profile.sys, 'argv', ['profile_starts.py', *args])
    with pytest.raises(SystemExit) as error:
        profile.main()
    assert error.value.code == 2
