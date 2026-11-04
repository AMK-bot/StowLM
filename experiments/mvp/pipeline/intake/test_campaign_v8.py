"""Preregistered failure scoring and immutable provenance without paid calls."""
from copy import deepcopy
import hashlib
import json

import pytest

from experiments.mvp.pipeline.contract.schema import IntakeResult, Question, sha256_file, sha256_json
from . import campaign_v8 as campaign
from .interpretation import SCHEMA, content, prompt


def truth(index=0):
    return {'id':f'QZXU{index:07d}','iso_type':'22G1','pol':'THLCH','pod':'SGSIN',
            'gross_mass_kg':12000.,'tare_kg':2200.,'empty':False,'reefer':False,'dg':None}


def expected(count=1):
    rows=[truth(i) for i in range(count)]
    return {'rows':rows,'traps':[{'row_id':rows[0]['id'],'kind':'wrong_pol','field':'pol','reason':'wrong_pol','plausible':True}],
            'clean_control_ids':[],'caption':f'{count} synthetic unit-test boxes','tally_cells':[]}


def emit():
    return {'schema_version':SCHEMA,'header_maps':[],'observations':[],'exceptions':[],'notes':[]}


def make_capture(case,documents,*,rejected=False,emitted=None,cost=.125):
    body=emitted if emitted is not None else emit()
    text=json.dumps(body)
    wire=content(documents)
    digest=sha256_json(wire)
    pd=hashlib.sha256(prompt().encode()).hexdigest()
    request={'schema_version':'stowlm.intake-request/8','policy_version':8,'attempt_limit':1,
             'started_utc':'2026-11-02T00:00:00Z','wire':wire,'documents_sha256':digest,'prompt_sha256':pd,
             'max_output_tokens':24576,'reasoning_effort':'low'}
    captured={'text':text,'request_id':'synthetic-request-id','usage':{'input_tokens':100,'output_tokens':50},
              'cost_usd':cost,'wall_s':2.5,'policy_version':8,'started_utc':request['started_utc'],
              'documents_sha256':digest,'prompt_sha256':pd}
    attempt={'wire':wire,'raw_text':text,'validation_error':'unsupported nested field' if rejected else None}
    meta={'policy_version':8,'request_id':captured['request_id'],'usage':captured['usage'],'cost_usd':cost,
          'raw_text':text,'attempts':[attempt],'documents_sha256':digest,'prompt_sha256':pd,'wall_s':2.5}
    artifact={'status':'schema_rejected','attempts':[attempt],'metadata':meta} if rejected else {**body,'metadata':meta}
    for name,value in [('spark_request.json',request),('spark_response.json',captured),
                       ('spark_rejection.json' if rejected else 'spark_raw.json',artifact)]:
        (case/name).write_text(json.dumps(value))
    return artifact


def test_closed_world_and_approximate_tare_provenance_counts_but_other_derived_does_not():
    row=truth()
    sources=[{'row_id':row['id'],'field':field,'accepted':True,'derived':True,
              'evidence':'derived_iso_tare' if field=='tare_kg' else 'closed_world_declaration'}
             for field in campaign.business_fields(row)]
    result=IntakeResult(status='complete',rows=[row],field_sources=sources)
    score=campaign.metrics(result,expected(),name='test')
    assert score['fields_correct']==score['fields_total']
    sources[0]['evidence']='arbitrary_model_inference'
    result.field_sources=sources
    assert campaign.metrics(result,expected(),name='test')['fields_correct']==score['fields_total']-1


def test_rejection_has_zero_accepted_fields_and_no_specific_trap_credit():
    target=expected(12)
    result=campaign.rejection_result(target)
    score=campaign.metrics(result,target,name=campaign.SPARK)
    assert score['fields_correct']==score['fields_accepted']==score['rows_exact']==0
    assert score['boxes_stopped']==12 and score['planner_workload']==1
    assert score['planted_problems_stopped']==0 and score['silent_wrong_values']==0
    assert {q.field for q in result.questions}==set(campaign.business_fields(target['rows'][0]))


def test_source_freeze_precedes_all_recordings_and_pins_policy(tmp_path,monkeypatch):
    source=tmp_path/'intake'; source.mkdir(); (source/'runtime.py').write_text('VERSION=8')
    cohort=source/'fixtures';cohort.mkdir()
    cases=[]
    for index in range(5):
        case=cohort/f'blind_{index:02d}';case.mkdir()
        for filename in ('doc.txt','documents.json','expected.json','base_instance.json'):
            (case/filename).write_text('Synthetic opaque bytes')
        cases.append({'split':case.name,'rows':12,'files':{p.name:sha256_file(p) for p in case.iterdir()}})
    manifest=cohort/'blind_freeze.json'
    manifest.write_text(json.dumps({'cases':cases,'frozen_before_tuning':True,'frozen_before_scoring':True}))
    (cohort/'blind_freeze.sha256').write_text(sha256_file(manifest))
    monkeypatch.setattr(campaign,'HERE',source)
    monkeypatch.setattr(campaign,'dependency_fingerprints',lambda:{'frozen':'dependency'})
    monkeypatch.setattr(campaign,'runtime_dependencies',lambda:{'python':'fixture'})
    old=cohort/'blind_00/spark_rejection.json';old.write_text('{}')
    with pytest.raises(ValueError,match='after a blind'):
        campaign.freeze(cohort)
    old.unlink()
    campaign.freeze(cohort)
    campaign.verify_frozen(cohort)
    source.joinpath('runtime.py').write_text('VERSION=9')
    with pytest.raises(ValueError,match='source changed'):
        campaign.verify_frozen(cohort)


def test_request_and_response_are_bound_before_proposal_acceptance(tmp_path):
    documents=[{'id':'unit','text':'SYNTHETIC source'}]
    raw=make_capture(tmp_path,documents)
    assert campaign.authenticated_reading(tmp_path,documents)==raw
    request=json.loads((tmp_path/'spark_request.json').read_text())
    request['wire'][0]['text']='forged prompt'
    (tmp_path/'spark_request.json').write_text(json.dumps(request))
    with pytest.raises(ValueError,match='frozen document wire'):
        campaign.authenticated_capture(tmp_path,documents)


def test_malformed_nested_schema_is_rejected_without_repair(tmp_path):
    documents=[{'id':'unit','text':'SYNTHETIC source'}]
    body=emit()
    body['observations']=[{'row_id':truth()['id'],'field':'dg.renamed_unsupported','value':True,'document':'unit','quote':'SYNTHETIC source'}]
    make_capture(tmp_path,documents,rejected=True,emitted=body)
    raw,_,status=campaign.authenticated_capture(tmp_path,documents)
    assert raw is None and status=='schema_rejected'
    assert 'renamed_unsupported' in (tmp_path/'spark_rejection.json').read_text()


def test_rejected_case_keeps_time_cost_and_literal_raw_tally(tmp_path,monkeypatch):
    documents=[{'id':'photo','mime_type':'image/png','path':str(tmp_path/'synthetic.png')}]
    (tmp_path/'synthetic.png').write_bytes(b'synthetic unit-test image bytes')
    body=emit()
    body['observations']=[{'row_id':truth()['id'],'field':'pod','value':'SGSIN','document':'photo','quote':f'{truth()["id"]} SGSIN'},
                          {'row_id':truth()['id'],'field':'dg.unsupported','value':True,'document':'photo','quote':'unsupported'}]
    make_capture(tmp_path,documents,rejected=True,emitted=body,cost=.125)
    target=expected();target['tally_cells']=[{'row_id':truth()['id'],'field':'pod','value':'SGSIN'}]
    (tmp_path/'expected.json').write_text(json.dumps(target));(tmp_path/'base_instance.json').write_text('{}')
    monkeypatch.setattr(campaign,'_input',lambda case:documents)
    monkeypatch.setattr(campaign,'parser_baseline',lambda *a,**k:campaign.rejection_result(target,'baseline_rejected'))
    score=campaign.score_case(tmp_path)
    model=score['methods'][campaign.SPARK]
    assert model['cost_usd']==.125 and model['wall_s']>=2.5
    assert model['fields_accepted']==0 and model['tally']['cells_correct']==1
    assert model['planted_problems_stopped']==0 and score['reading_status']=='schema_rejected'


def test_transport_failure_is_not_assumed_free(tmp_path,monkeypatch):
    documents=[{'id':'unit','text':'SYNTHETIC source'}]
    make_capture(tmp_path,documents)
    (tmp_path/'spark_raw.json').unlink()
    response=json.loads((tmp_path/'spark_response.json').read_text())
    response.update(status='transport_or_client_error');response.pop('text');response.pop('cost_usd')
    (tmp_path/'spark_response.json').write_text(json.dumps(response))
    target=expected();(tmp_path/'expected.json').write_text(json.dumps(target));(tmp_path/'base_instance.json').write_text('{}')
    monkeypatch.setattr(campaign,'_input',lambda case:documents)
    monkeypatch.setattr(campaign,'parser_baseline',lambda *a,**k:campaign.rejection_result(target,'baseline_rejected'))
    score=campaign.score_case(tmp_path)
    assert score['cost_unknown'] and score['reading_status']=='transport_failed'
    assert score['methods'][campaign.SPARK]['fields_accepted']==0


def test_blind_score_attempt_prevents_second_scoring_and_seals_all_receipts(tmp_path,monkeypatch):
    case=tmp_path/'blind_00';case.mkdir()
    documents=[{'id':'unit','text':'SYNTHETIC source'}]
    body=emit();body['unsupported_authority']=True
    make_capture(case,documents,rejected=True,emitted=body)
    target=expected(12);(case/'expected.json').write_text(json.dumps(target));(case/'base_instance.json').write_text('{}')
    monkeypatch.setattr(campaign,'_input',lambda path:documents)
    monkeypatch.setattr(campaign,'verify_frozen',lambda *a: {})
    monkeypatch.setattr(campaign,'parser_baseline',lambda *a,**k:campaign.rejection_result(target,'baseline_rejected'))
    score=campaign.score_case(case)
    assert campaign.verify_case_seal(case)==score
    with pytest.raises(ValueError,match='one-attempt'):
        campaign.score_case(case)
    (case/'spark_response.json').write_text('{}')
    with pytest.raises(ValueError,match='changed after publication'):
        campaign.verify_case_seal(case)


def test_aggregate_includes_all_failed_cases_and_rejects_omission():
    target=expected(12)
    scored=campaign.metrics(campaign.rejection_result(target),target,name=campaign.SPARK)
    cases=[{'case':f'blind_{i:02d}','reading_status':'schema_rejected','cost_unknown':False,
            'methods':{campaign.SPARK:deepcopy(scored),campaign.BASELINE:deepcopy(scored)}} for i in range(5)]
    result=campaign.aggregate(cases)
    assert result['box_count']==60 and result['case_count']==5 and result['rejected_case_count']==5
    assert result['methods'][campaign.SPARK]['planner_workload']==1
    with pytest.raises(ValueError,match='exactly once'):
        campaign.aggregate(cases[:-1])
