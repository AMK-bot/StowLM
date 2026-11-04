"""Cold-start timing uses new admitted child processes and stops before search."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.runner.cold_start import HERE, ROOT, measure


def test_measure_admits_each_fresh_child_and_preserves_load(monkeypatch):
    import experiments.mvp.pipeline.runner.cold_start as cold
    calls = []
    admissions = []

    def admit():
        admissions.append(True)
        return {'load1': 30., 'enabled_cores': 8, 'reading_age_s': 1.}

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[1] == '-c'
        assert kwargs['env']['OMP_NUM_THREADS'] == '1'
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(imports_ready_s=1.,
            assemble_s=2., envelope_s=2.01, first_reader_call_s=2.1, status='probe_complete')))

    monkeypatch.setattr(cold.subprocess, 'run', run)
    result = measure(samples=2, admit=admit)
    assert len(admissions) == len(calls) == 2
    assert result['median_s']['first_reader_call_s'] == 2.1
    assert all(row['admission']['load1'] == 30. for row in result['rows'])
    assert result['no_search'] is result['no_model'] is True
    assert 'filesystem and vessel caches left warm' in result['cold']


@pytest.mark.parametrize('samples', [0, -1, True, 1.5])
def test_measure_rejects_invalid_samples(samples):
    with pytest.raises(ValueError, match='positive integer'):
        measure(samples=samples, admit=lambda: pytest.fail('must validate before admission'))


def test_measure_refuses_sealed_paths_before_read(tmp_path):
    with pytest.raises(ValueError, match='sealed'):
        measure(tmp_path / 'sealed/never-read.json', admit=lambda: pytest.fail('must not admit'))


def test_probe_failure_has_a_clear_error(monkeypatch):
    import experiments.mvp.pipeline.runner.cold_start as cold
    monkeypatch.setattr(cold.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match='could not finish'):
        measure(samples=1, admit=lambda: {})
