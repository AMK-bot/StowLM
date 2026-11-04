"""Campaign accounting and one-shot provenance fail closed without model calls."""
from copy import deepcopy
import hashlib
import json

import pytest

from experiments.mvp.pipeline.contract.schema import IntakeResult, Question, sha256_file, sha256_json
from . import campaign_v7 as campaign
from .interpretation import SCHEMA, content, prompt


def sample(*, wrong_mass=False, questions=()):
    truth = {'id':'QZXU0000009','iso_type':'22G1','pol':'THLCH','pod':'SGSIN',
             'gross_mass_kg':12000.,'tare_kg':2300.,'empty':False,'reefer':False,'dg':None}
    row = {**truth,'gross_mass_kg':13000. if wrong_mass else 12000.}
    sources = [{'row_id':row['id'],'field':field,'value':value,'accepted':True,'document':'sheet'}
               for field,value in campaign.business_fields(row).items()]
    result = IntakeResult(status='needs_answers' if questions else 'complete',rows=[row],
                          questions=list(questions),field_sources=sources)
    expected = {'rows':[truth],'traps':[],'clean_control_ids':[truth['id']]}
    return result,expected


def q(field,reason,row=0):
    return Question(id=f'{field}:{reason}',row=row,field=field,reason=reason,prompt='Fixture question')


def test_global_sheet_review_stops_every_box_without_hiding_correct_fields():
    result,expected = sample(questions=[q('sheet:vgm','unmapped_sheet_headers',None)])
    scored = campaign.metrics(result,expected,name='test')
    assert scored['boxes_stopped']==scored['rows_total']==1
    assert scored['planner_workload']==1
    assert scored['rows_exact']==0
    assert scored['fields_correct']==scored['fields_total']


@pytest.mark.parametrize('question',[q(None,'invalid_partial_row'),q('pod','missing_declaration')])
def test_other_row_question_never_masks_a_silent_wrong_field(question):
    result,expected = sample(wrong_mass=True,questions=[question])
    scored = campaign.metrics(result,expected,name='test')
    assert scored['silent_wrong_values']==1
    assert scored['silent_wrong_details'][0]['field']=='gross_mass_kg'


def test_missing_field_and_global_sheet_review_do_not_count_as_found_planted_problem():
    result,expected = sample(questions=[q('pol','missing_declaration'),q('sheet:si','unmapped_sheet_headers',None)])
    expected['traps']=[{'row_id':'QZXU0000009','kind':'wrong_pol','field':'pol','reason':'wrong_pol','plausible':True}]
    scored = campaign.metrics(result,expected,name='test')
    assert scored['planted_problems_stopped']==0
    assert scored['traps'][0]['unrelated_question_only']
    result.questions=[q('pol','wrong_pol')]
    assert campaign.metrics(result,expected,name='test')['planted_problems_stopped']==1


def test_frozen_input_or_source_substitution_is_rejected(tmp_path,monkeypatch):
    source = tmp_path/'intake'; source.mkdir()
    (source/'stage.py').write_text('VERSION=7\n')
    cohort=source/'fixtures';cohort.mkdir()
    cases=[]
    for i in range(5):
        case=cohort/f'blind_{i}';case.mkdir()
        (case/'document.txt').write_text('SYNTHETIC opaque fixture bytes')
        cases.append({'split':case.name,'rows':1,'files':{'document.txt':sha256_file(case/'document.txt')}})
    manifest=cohort/'blind_freeze.json'
    manifest.write_text(json.dumps({'cases':cases,'frozen_before_tuning':True,'frozen_before_scoring':True}))
    (cohort/'blind_freeze.sha256').write_text(sha256_file(manifest)+'\n')
    monkeypatch.setattr(campaign,'HERE',source)
    monkeypatch.setattr(campaign,'dependency_fingerprints',lambda:{'dependency.py':'frozen'})
    monkeypatch.setattr(campaign,'runtime_dependencies',lambda:{'python':'fixture'})
    campaign.freeze(cohort)
    campaign.verify_frozen(cohort)
    (cohort/'blind_0/document.txt').write_text('substitution')
    with pytest.raises(ValueError,match='input changed'):
        campaign.verify_frozen(cohort)
    (cohort/'blind_0/document.txt').write_text('SYNTHETIC opaque fixture bytes')
    (source/'stage.py').write_text('VERSION=8\n')
    with pytest.raises(ValueError,match='source changed'):
        campaign.verify_frozen(cohort)


def test_captured_response_or_document_wire_substitution_is_rejected(tmp_path):
    documents=[{'id':'synthetic-test','text':'SYNTHETIC test document'}]
    emitted={'schema_version':SCHEMA,'header_maps':[],'observations':[],'exceptions':[],'notes':[]}
    text=json.dumps(emitted)
    meta={'request_id':'synthetic-test-request','raw_text':text,
          'documents_sha256':sha256_json(content(documents)),
          'prompt_sha256':hashlib.sha256(prompt().encode()).hexdigest(),
          'cost_usd':0.,'usage':{}}
    raw={**emitted,'metadata':meta}
    captured={'request_id':meta['request_id'],'text':text,'cost_usd':0.,'usage':{}}
    (tmp_path/'spark_raw.json').write_text(json.dumps(raw))
    (tmp_path/'spark_response.json').write_text(json.dumps(captured))
    assert campaign.authenticated_reading(tmp_path,documents)==raw
    with pytest.raises(ValueError,match='document wire'):
        campaign.authenticated_reading(tmp_path,[{'id':'different','text':'different'}])
    raw['observations']=[{'row_id':'QZXU0000009','field':'pod','value':'USLAX','document':'forged','quote':'forged'}]
    (tmp_path/'spark_raw.json').write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='proposal changed'):
        campaign.authenticated_reading(tmp_path,documents)
