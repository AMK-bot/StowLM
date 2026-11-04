"""Preserved actual native calls: never launch a search during evidence replay."""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'fixtures/profile-short'


@pytest.mark.parametrize('name,budget,finalize,wall', [
    ('live', 120., 40., 720.), ('standard', 300., 100., 1800.)])
def test_real_profile_dispatch_and_hard_wall(name, budget, finalize, wall):
    summary = json.loads((OUTPUT / 'summary.json').read_text())
    selection_path = HERE / 'fixtures/profile-selection.json'
    selection = json.loads(selection_path.read_text())
    assert summary['selection_sha256'] == hashlib.sha256(selection_path.read_bytes()).hexdigest()
    assert summary['passed'] is True
    row = next(item for item in summary['runs'] if item['profile'] == name)
    selected = selection['profiles'][name]
    assert row['actual_native_kwargs'] == selected['engine_kwargs'] == row['selected_engine_kwargs']
    assert row['actual_native_kwargs']['budget_s'] == budget
    assert row['actual_native_kwargs']['finalize_s'] == finalize
    assert row['selected_engine_wall_s'] == selected['engine_timeout_s'] == wall
    assert row['native_worker_reaped'] is True
    assert row['runner_deadline']['expired'] is True
    assert row['runner_deadline']['timeout_s'] == 2.
    assert row['status'] == 'no_checked_plan'
    assert row['package_present'] is False
    assert 2. <= row['engine_wall_s'] < 5.
    outcome_bytes = gzip.decompress((OUTPUT / row['outcome_artifact']).read_bytes())
    assert hashlib.sha256(outcome_bytes).hexdigest() == row['outcome_sha256']
    outcome = json.loads(outcome_bytes)
    assert outcome['evidence']['runner_deadline'] == row['runner_deadline']
    assert outcome['status'] == row['status']
    assert outcome['profile']['engine_timeout_s'] == wall
    assert outcome['plan'] is None and outcome['package'] is None
    assert any(step['stage'] == 'engine' for step in outcome['receipt']['steps'])
