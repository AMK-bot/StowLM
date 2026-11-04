import hashlib,json
from decimal import Decimal
from pathlib import Path
import pytest
from experiments.mvp.reader.brief_report_combined import read_group,metrics


def setup(tmp_path):
    cohort={'cases':[{'case_id':'a'},{'case_id':'b'}]}
    (tmp_path/'cohort.json').write_text(json.dumps(cohort))
    sha=hashlib.sha256((tmp_path/'cohort.json').read_bytes()).hexdigest()
    (tmp_path/'preregister.json').write_text(json.dumps({'cohort_sha256':sha}))
    for folder in ('plans','parser','audits'):(tmp_path/folder).mkdir()
    for name in ('a','b'):
        plan={'case_id':name,'pipeline_wall_s':10 if name=='a' else 0,'timing_unknown':name=='b'}
        parse={'case_id':name,'brief':{'sentences':[{'parser':{'cost_usd':'0.001'}}]}}
        for folder,row in (('plans',plan),('parser',parse)):
            (tmp_path/folder/f'{name}.json').write_text(json.dumps(row))
    return tmp_path


def test_interrupted_timing_is_excluded_and_unassessed_plan_not_counted(tmp_path):
    path=setup(tmp_path)
    (path/'audits/a.json').write_text(json.dumps({'case_id':'a','plan_present':True,'audit_completed':False}))
    group=read_group(path);result=metrics(group)
    assert result['planned_maps']==2 and result['returned_plans']==0
    assert result['mean_pipeline_wall_s']==10 and result['timing_unknown']==1
    assert Decimal(result['cost_usd'])==Decimal('0.002')


def test_completed_fresh_audit_must_link_saved_plan_and_parser(tmp_path):
    path=setup(tmp_path)
    (path/'audits/a.json').write_text(json.dumps({'case_id':'a','audit_completed':True}))
    with pytest.raises(ValueError,match='Audit linkage missing'):read_group(path)


def test_cohort_cannot_change_after_manifest(tmp_path):
    path=setup(tmp_path)
    (path/'cohort.json').write_text(json.dumps({'cases':[]}))
    with pytest.raises(ValueError,match='Cohort hash mismatch'):read_group(path)
