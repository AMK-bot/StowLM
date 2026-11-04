import hashlib
import json
import pytest
from experiments.mvp.reader import brief_report as report


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup(tmp_path, monkeypatch, audit):
    run = tmp_path/'runs/brief'
    case = {'case_id':'brief-01','instance_id':'fixture','sentence':'Close bay 1.',
            'static_screen':{'proven_impossible':False}, 'instruction_selection':{'vacuous':True}}
    sha = write(run/'cohort.json', {'cases':[case]})
    write(run/'preregister.json',{'cohort_sha256':sha})
    audit['plan_sha256'] = write(run/'plans/brief-01.json', {'case_id':'brief-01','pipeline_wall_s':10})
    audit['parser_sha256'] = write(run/'parser/brief-01.json',{'case_id':'brief-01','brief':{'sentences':[]}})
    write(run/'audits/brief-01.json', dict(audit,case_id='brief-01'))
    monkeypatch.setattr(report,'RUN',run)
    monkeypatch.setattr(report,'HERE',tmp_path)
    monkeypatch.setattr(report,'ROOT',tmp_path)
    monkeypatch.setattr(report,'summarize', lambda *a:{})
    monkeypatch.setattr(report,'report_text',lambda *a:'Exact ablation unavailable.')
    return run


def test_unassessed_returned_plan_is_unknown_and_invalid_baseline_not_credited(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, {'plan_present':True,'baseline_passed':True,
                                  'baseline_breaks_instruction':True,'audit_completed':False})
    got=report.build()
    text=(tmp_path/'BRIEF.md').read_text()
    assert got['baseline_broken']==0
    assert got['reader_broken']==0 and got['passes']==0
    assert 'Returned plans with unassessed instruction compliance: 1.' in text
    assert 'Vacuous DG restrictions: 1/30' in text


def test_changed_artifact_invalidates_fresh_audit(tmp_path, monkeypatch):
    run=setup(tmp_path,monkeypatch,{'audit_completed':True})
    write(run/'plans/brief-01.json',{'case_id':'brief-01','pipeline_wall_s':11})
    with pytest.raises(ValueError,match='artifact hash mismatch'):
        report.build()
