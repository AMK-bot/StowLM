import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from experiments.mvp.reader import run_brief_followup as runner


def test_second_runner_has_own_globals_without_changing_first():
    before=runner.first.RUN
    second=runner.second_runner()
    assert second.RUN==runner.RUN2 and runner.first.RUN==before
    assert second.plan_one.__code__.co_code==runner.first.plan_one.__code__.co_code
    assert second.plan_one.__globals__ is not runner.first.plan_one.__globals__
    assert second.plan_one.__globals__['RUN']==runner.RUN2


def test_phase_keeps_plans_before_audits_and_avoids_active_duplicate(tmp_path,monkeypatch):
    fake=NS(RUN=tmp_path,cases=lambda:[{'case_id':'a'},{'case_id':'b'}])
    monkeypatch.setattr(runner,'first',fake)
    (tmp_path/'plans').mkdir()
    phase,case=runner.pending_task('brief',set())
    assert (phase,case['case_id'])==('plans','a')
    assert runner.pending_task('brief',{('brief','plans','a')})[1]['case_id']=='b'
    (tmp_path/'plans/a.json').write_text('{}')
    (tmp_path/'plans/b.json').write_text('{}')
    assert runner.pending_task('brief',set())[0]=='audits'


def test_missing_second_readiness_never_admits_second_solver(monkeypatch,tmp_path):
    monkeypatch.setattr(runner,'RUN2',tmp_path)
    assert runner.pending_task('brief2',set()) is None
    assert runner.group_done('brief2') is False
