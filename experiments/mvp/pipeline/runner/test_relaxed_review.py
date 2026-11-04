"""Review18: native relaxed ranks, JSON evidence and level-separated reports."""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.runner.benchmark import summarize
from experiments.mvp.pipeline.runner.evidence import json_evidence

HERE = Path(__file__).resolve().parent


def test_real_native_unknown_rank_is_preserved_as_unknown_before_hash(tmp_path):
    from engine.peel_ladder.tiers import load_table, rank_report
    report = dict(passed=False, violations=[dict(rule_id='expert.e6.heavy_under_deck',
        measured=None, limit=None, severity='hard')], parts={'rules': {'ran': True, 'error': None}},
        count={'weighted_nonproductive': 17}, shut_out=[])
    rank = rank_report(report, load_table(HERE.parents[1] / 'frontier/engine/tiers.toml'))
    assert math.isinf(rank[4])
    with pytest.raises(ValueError):
        sha256_json({'rank': rank})
    safe = json_evidence({'rank': rank})
    assert safe['rank'][4] is None
    assert {'path': 'rank.4', 'kind': 'positive_infinity'} in safe['nonfinite_values']
    assert len(sha256_json(safe)) == 64
    from experiments.mvp.pipeline.runner.benchmark import _write
    _write(tmp_path / 'measurements.json', {'rank': rank})
    saved = json.loads((tmp_path / 'measurements.json').read_text())
    assert saved == safe and len(sha256_json(saved)) == 64


def row(status, level, *, passed=False, completed=True, percent=100., lifts=7.):
    return dict(map='synthetic', size_class='tiny', with_rules=False, target_budget_s=180.,
                status=status, passed=passed, level=level, completed=completed,
                boxes_aboard_percent=percent, lifts=lifts, wall_s=2., first_legal_s=None,
                admission={'load1': 30.})


def test_summary_separates_relaxed_levels_and_partial_from_full_passes():
    values = [row('checked_plan', 0, passed=True), row('relaxed_plan', 2),
              row('relaxed_plan', 3, completed=False, percent=75.), row('relaxed_plan', 5),
              row('no_checked_plan', None, completed=False, percent=0., lifts=None)]
    result = summarize(values)
    cell = result['cells'][0]
    assert cell['full_passes'] == 1 and cell['pass_rate'] == .2
    assert cell['relaxed_plans'] == 3 and cell['mean_lifts_passing'] == 7.
    by_level = {value['level']: value for value in cell['relaxed_by_level']}
    assert set(by_level) == {2, 3, 5}
    assert by_level[3]['partial'] == 1 and by_level[3]['completed'] == 0
    assert by_level[3]['mean_boxes_aboard_percent'] == 75.
    assert by_level[3]['mean_lifts_completed'] is None and by_level[3]['mean_lifts_partial'] == 7.
    assert all(value['full_passes'] == 0 for value in by_level.values())
    assert result['relaxed_by_level'] == cell['relaxed_by_level']
    sha256_json(result)


@pytest.mark.parametrize('level,violations,accepted', [(3, [], True), (None, [], False),
                                                     (3, ['instruction broken'], False)])
def test_relaxed_benchmark_requires_native_classification_and_instruction_recheck(monkeypatch, level, violations, accepted):
    import experiments.mvp.pipeline.runner.benchmark as study
    import stowage.checker as checker
    import engine.peel as peel
    import experiments.mvp.reader.pipeline as pipeline
    import experiments.mvp.reader.schema as schema
    from stowage.model import MovePlan
    plan = MovePlan(id='synthetic', instance_id='synthetic', call_index=0).model_dump(mode='json')
    report = SimpleNamespace(passed=False, count=SimpleNamespace(weighted_nonproductive=7.),
                             model_dump=lambda **kwargs: {'passed': False})
    monkeypatch.setattr(checker, 'check', lambda *args: report)
    monkeypatch.setattr(peel, 'inspect', lambda *args: dict(level=level, completed=False, boxes_aboard_percent=75.))
    monkeypatch.setattr(schema, 'context_from_instance', lambda instance: {'bays': [1]})
    monkeypatch.setattr(pipeline, 'intrinsic_violations', lambda *args: violations)
    readings = [dict(status='accepted', sentence='Close bay 1.', parsed=dict(schema_version='stowlm.reader/2',
                    constraint=dict(type='close_bay', bay=1)))]
    actual = study._validated_relaxed_plan(object(), plan, readings)
    assert (actual is not None) == accepted
    if actual:
        assert actual['level'] == 3 and actual['completed'] is False


def test_recorded_real_relaxed_result_stays_labelled_and_hashable():
    path = HERE / 'fixtures/relaxed-review/summary.json'
    assert path.is_file(), 'Run the admitted relaxed_probe once to record real release-path evidence'
    value = json.loads(path.read_text())
    assert value['status'] == 'relaxed_plan' and value['passed'] is False
    assert value['level'] in range(1, 6)
    assert value['engine_version'] == '0.13.0' and value['engine_manifest_verified'] is True
    assert len(value['engine_manifest_sha256']) == 64
    assert 'not medium performance' in value['label']
    assert value['admission']['load1'] <= 45
    assert value['pid'] > 0
    assert len(sha256_json(value)) == 64
