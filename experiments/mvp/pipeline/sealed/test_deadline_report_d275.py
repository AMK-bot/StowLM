"""A wall deadline can retain only a freshly audited L0 incumbent."""
import importlib.util
from pathlib import Path

from experiments.mvp.pipeline.sealed.common import HERE


spec=importlib.util.spec_from_file_location('experiments.mvp.pipeline.sealed._report_d275',HERE/'fixtures/d275/report.py')
report=importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def row():
    return dict(status='deadline_exceeded',deadline_exceeded=True,deadline_with_incumbent=True,
        level=0,passed=True,full_checker_pass=True,audit_verified=True,audit_fresh_process=True,
        arm='pipeline',plan={'synthetic':True})


def test_deadline_is_a_pass_only_with_audited_incumbent_and_all_existing_gates():
    good=row()
    assert report.passed(good)
    for field in ('deadline_with_incumbent','passed','full_checker_pass','audit_verified','audit_fresh_process'):
        assert not report.passed(dict(good,**{field:False})),field
    assert not report.passed(dict(good,level=1))
    assert not report.passed(dict(good,arm='instruction',instruction_recheck=False))
    assert report.passed(dict(good,arm='instruction',instruction_recheck=True))


def test_deadlines_stay_a_separate_column_from_crashes_and_no_incumbent_is_not_a_pass():
    rows={'pass':dict(row(),wall_s=1200.),'budget':dict(row(),deadline_with_incumbent=False,
        passed=False,full_checker_pass=False,plan=None,wall_s=1200.)}
    summary=report._arm_summary(rows,2)
    assert summary['statuses']=={'deadline_exceeded':2}
    assert summary['passes_L0']==1
    assert 'deadline_with_incumbent' in report.CSV_FIELDS
