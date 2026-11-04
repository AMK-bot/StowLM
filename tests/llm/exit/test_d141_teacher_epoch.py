"""rev-v4-j P2: teacher proofs are bound to the full outcome dependency epoch; the coverage gate follows the gold maps
actually selected."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from llm.exit import runner as R
from llm.exit import selection as S
from llm.exit.outcome_epoch import epoch_id
from stowage.checker import CHECKER_VERSION
from stowage.checker.full import _input_bindings
from stowage.model import CheckReport, MovePlan
from stowage.model.fixtures import build_instance


@pytest.fixture
def teacher(tmp_path, monkeypatch):
    instance = build_instance()
    path = tmp_path/'instance.json'
    path.write_text(instance.model_dump_json())
    plan = MovePlan(id='teacher', instance_id=instance.id, call_index=instance.call_index)
    report = CheckReport(passed=True, provenance=dict(versions={'checker': CHECKER_VERSION},
                                                      parents=list(_input_bindings(instance, plan))))
    candidate = S.Candidate(plan, 'gold', 'gold', {'weighted_moves': 3})
    checked = S.Verification(True, plan, 3, {'report': report.model_dump(mode='json'), 'mask': {'passed': True}})
    calls = []
    def prepare(*args, **kwargs):
        calls.append(1)
        return [candidate], {'gold': checked}, [{'source': 'gold', 'passed': True}]
    monkeypatch.setattr(S, 'prepare_teacher_candidates', prepare)
    monkeypatch.setattr(R, 'load_gold_candidate', lambda *a: None)
    monkeypatch.setattr('llm.exit.identity.identity_guard', lambda *a: NS(require_allowed=lambda *a, **k: 'ok'))
    monkeypatch.setattr(R, 'source_pins', lambda: {'src/x.py': 'same'})  # src bytes never change in these tests
    monkeypatch.setattr(R, 'assert_audit_epoch', lambda expected: expected)
    monkeypatch.setattr(R, '_LOADED_AUDIT_EPOCH', {'params/limits.toml': 'old'})
    return NS(path=path, directory=tmp_path/'teacher', calls=calls, tmp=tmp_path)


def test_teacher_manifest_records_the_full_dependency_epoch(teacher):
    target = R.teacher_job(teacher.path, teacher.tmp, teacher.tmp/'gold', teacher.directory)
    binding = json.loads(Path(target).read_text())['binding']
    assert binding['outcome_epoch'] == epoch_id({'params/limits.toml': 'old'})
    assert R.teacher_job(teacher.path, teacher.tmp, teacher.tmp/'gold', teacher.directory) == target
    assert teacher.calls == [1]  # same epoch: cache reuse
    assert R.read_teacher_manifest(target, teacher.path)[0]


def test_resource_edit_with_unchanged_source_refuses_the_teacher_cache(teacher, monkeypatch):
    """Repro: rev-d137-resource-cache-repro.py. source_pins and checker_epoch are unchanged here."""
    target = R.teacher_job(teacher.path, teacher.tmp, teacher.tmp/'gold', teacher.directory)
    monkeypatch.setattr(R, '_LOADED_AUDIT_EPOCH', {'params/limits.toml': 'new'})
    with pytest.raises(RuntimeError, match='teacher cache epoch/input/budget changed'):
        R.teacher_job(teacher.path, teacher.tmp, teacher.tmp/'gold', teacher.directory)
    with pytest.raises(RuntimeError, match='teacher proof no longer belongs'):
        R.read_teacher_manifest(target, teacher.path)
    assert teacher.calls == [1]


def test_old_manifest_without_outcome_epoch_is_refused(teacher):
    target = Path(R.teacher_job(teacher.path, teacher.tmp, teacher.tmp/'gold', teacher.directory))
    data = json.loads(target.read_text())
    del data['binding']['outcome_epoch']
    target.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match='teacher proof no longer belongs'):
        R.read_teacher_manifest(target, teacher.path)


@pytest.mark.parametrize('moved', ['at_create', 'at_read'])
def test_drift_on_disk_refuses_teacher_write_and_read(teacher, monkeypatch, moved):
    def drifted(expected):
        raise RuntimeError('audit outcome dependency epoch changed')
    if moved == 'at_create':
        monkeypatch.setattr(R, 'assert_audit_epoch', drifted)
        with pytest.raises(RuntimeError, match='dependency epoch changed'):
            R.teacher_job(teacher.path, teacher.tmp, teacher.tmp/'gold', teacher.directory)
        assert not (teacher.directory/'teacher-manifest.json').exists()
    else:
        target = R.teacher_job(teacher.path, teacher.tmp, teacher.tmp/'gold', teacher.directory)
        monkeypatch.setattr(R, 'assert_audit_epoch', drifted)
        with pytest.raises(RuntimeError, match='dependency epoch changed'):
            R.read_teacher_manifest(target, teacher.path)


REPORT = dict(verified_teacher_maps=2, gold_passes=0)


def test_zero_selected_gold_maps_do_not_require_a_gold_pass():
    """Repro: rev-d137-zero-gold.py (--tiny 2 --small 0 --gold-map-fraction .1 rounds to zero gold maps)."""
    assert R.teacher_coverage_ok(REPORT, {'a': 'no_stored_gold', 'b': 'no_stored_gold'})


def test_selected_gold_maps_still_require_a_gold_pass():
    strata = {'a': 'checked_gold_teacher', 'b': 'no_stored_gold'}
    assert not R.teacher_coverage_ok(REPORT, strata)
    assert R.teacher_coverage_ok(dict(REPORT, gold_passes=1), strata)


def test_no_teacher_or_unknown_strata_fail_closed():
    assert not R.teacher_coverage_ok(dict(verified_teacher_maps=0, gold_passes=0), {'a': 'no_stored_gold'})
    assert not R.teacher_coverage_ok(REPORT, None)
