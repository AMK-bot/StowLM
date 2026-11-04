"""One authoritative declaration, scoped list omissions and fail-closed conflicts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from . import read_documents
from .interpretation import SCHEMA
from .spark_reader import RecordedClient
from .test_realistic_v7 import packet
from .test_validation import case


@pytest.fixture
def desk(packet):
    base,_,raw,rid,box=packet
    ref='SYN-UNIT-1'
    def doc(role,text,mime='text/plain',complete=False):
        return {'id':role,'role':role,'booking_ref':ref,'complete_booking_list':complete,'text':text,'mime_type':mime}
    docs=[doc('booking',f'Booking {ref}\n{rid}; POD {box["pod"]}; Vessel DEMO; Status CONFIRMED'),
          doc('shipping_instructions',f'id,iso_type,pol,empty,tare_kg\n{rid},{box["iso_type"]},{box["pol"]},LADEN,{box["tare_kg"]} kg\n','text/csv'),
          doc('vgm',f'id,gross_mass_kg\n{rid},{box["gross_mass_kg"]} kg\n','text/csv'),
          doc('gate_slip',f'{rid}; Seal SYN12345'),
          doc('dg_declaration',f'COMPLETE DG LIST FOR {ref}. No DG containers.',complete=True),
          doc('reefer_list',f'COMPLETE OPERATING REEFER LIST FOR {ref}. No operating reefers.',complete=True),
          doc('oog_list',f'COMPLETE OOG LIST FOR {ref}. No OOG containers.',complete=True)]
    raw['metadata']['policy_version']=8
    return base,docs,raw,rid,box


def run(desk):
    base,docs,raw,_,_=desk
    return read_documents(docs,base_instance=base,client=RecordedClient(raw))


def test_one_authoritative_source_and_complete_lists_finish(desk):
    result=run(desk)
    assert result.status=='complete',result.questions
    assert result.rows[0]['dg'] is None and result.rows[0]['reefer'] is False
    assert any(s['field']=='pod' and s['accepted'] and s['evidence']=='authoritative_declaration' for s in result.field_sources)
    assert all(any(s['field']==field and s['evidence']=='closed_world_declaration' and s['accepted'] for s in result.field_sources) for field in ('dg','reefer','oog'))


def test_authority_does_not_override_a_confirmed_wrong_vgm(desk):
    desk[1][2]['text']=desk[1][2]['text'].replace(str(desk[4]['gross_mass_kg']),'999999')
    r=run(desk)
    assert any(q.field=='gross_mass_kg' and q.reason in ('intake_mass_limit','container_mass_limit') for q in r.questions)


def test_supplementary_conflict_still_shows_both_values(desk):
    base,docs,raw,rid,box=desk
    text=f'{rid}; POD USLAX'
    docs.append({'id':'email','role':'email','text':text})
    r=run(desk)
    q=next(q for q in r.questions if q.field=='pod' and q.reason=='cross_document_conflict')
    assert box['pod'] in q.prompt and 'USLAX' in q.prompt


def test_ungrounded_redundant_proposal_never_accepts_or_stops_a_sourced_field(desk):
    base,docs,raw,rid,box=desk
    raw['observations'].append({'row_id':rid,'field':'pol','value':'USLAX','document':'booking','quote':'not in source'})
    r=run(desk)
    assert r.status=='complete',r.questions
    assert not any(s.get('value')=='USLAX' and s['accepted'] for s in r.field_sources)
    assert r.rows[0]['pol']==box['pol']


@pytest.mark.parametrize('role',['vgm','dg_declaration','reefer_list','oog_list','booking'])
def test_absent_document_is_one_global_task_not_n_row_tasks(desk,role):
    desk[1][:]=[d for d in desk[1] if d['role']!=role]
    r=run(desk)
    qs=[q for q in r.questions if q.reason=='missing_document' and q.field=='document:'+role]
    assert len(qs)==1 and qs[0].row is None
    fields={'vgm':('gross_mass_kg','weight_class'),'dg_declaration':('dg',),'reefer_list':('reefer',),'oog_list':('oog',),'booking':('pod','booked_vessel','booking_status')}[role]
    assert not [q for q in r.questions if q.row is not None and q.field in fields],r.questions


def test_model_omission_never_makes_a_listed_dg_box_non_dg(desk):
    base,docs,raw,rid,box=desk
    next(d for d in docs if d['role']=='dg_declaration')['text']+=f'\n{rid}; UN 1263; Class 3; PG III; MP NO; LQ NO'
    r=run(desk)
    assert any(q.field=='dg' for q in r.questions)
    assert not any(s['field']=='dg' and s.get('value') is None and s['accepted'] for s in r.field_sources)


def test_closed_world_cannot_cross_booking_scope(desk):
    next(d for d in desk[1] if d['role']=='dg_declaration')['booking_ref']='OTHER'
    r=run(desk)
    assert any(q.reason=='document_scope_mismatch' for q in r.questions)
    assert not any(s['field']=='dg' and s.get('closed_world') for s in r.field_sources)


def test_authoritative_photo_still_needs_independent_corroboration(desk,tmp_path):
    from PIL import Image
    base,docs,raw,rid,box=desk
    image=tmp_path/'vgm.png';Image.new('RGB',(20,20),'white').save(image)
    docs[:]=[d for d in docs if d['role']!='vgm']
    docs.append({'id':'photo','role':'vgm','booking_ref':'SYN-UNIT-1','mime_type':'image/png','path':str(image)})
    raw['observations'].append({'row_id':rid,'field':'gross_mass_kg','value':box['gross_mass_kg'],'unit':'kg','document':'photo','quote':f'{rid} VGM {box["gross_mass_kg"]} kg'})
    r=run(desk)
    assert any(q.field=='gross_mass_kg' and q.reason=='needs_corroboration' for q in r.questions)


def test_schema_rejection_returns_one_batch_question_and_preserves_attempt(desk):
    base,docs,raw,rid,box=desk
    raw['observations']=[{'row_id':rid,'field':'dg.additional.0.dg.un_number','value':'1263','document':'booking','quote':rid}]
    r=run(desk)
    assert r.status=='rejected' and len(r.questions)==1
    assert not r.field_sources
    assert len(json.loads(r.records[-1].notes)['schema_rejection']['attempts'])==1


def test_spent_v7_silent_setpoint_is_blocked_under_authority_policy():
    case=Path(__file__).parent/'fixtures/realistic_v7/blind_03'
    raw=json.loads((case/'spark_raw.json').read_text())
    raw['metadata']['policy_version']=8
    result=read_documents(json.loads((case/'documents.json').read_text()),
                          base_instance=json.loads((case/'base_instance.json').read_text()),client=RecordedClient(raw))
    index=next(i for i,r in enumerate(result.rows) if r['id']=='QZXU0000061')
    assert any(q.row==index and q.field=='reefer_setpoint_c' and q.reason=='reefer_dependency' for q in result.questions)
    assert not any(s['row']==index and s['field']=='reefer_setpoint_c' and s['accepted'] for s in result.field_sources)


def test_missing_completeness_flag_never_asserts_non_dg(desk):
    doc=next(d for d in desk[1] if d['role']=='dg_declaration')
    del doc['complete_booking_list']
    r=run(desk)
    assert any(q.row is None and q.reason=='incomplete_document_scope' for q in r.questions)
    assert not any(s['field']=='dg' and s.get('closed_world') for s in r.field_sources)


def test_unmatched_list_identity_does_not_clear_any_omitted_box(desk):
    next(d for d in desk[1] if d['role']=='dg_declaration')['text']+='\nZZZU9999999; UN 1263'
    r=run(desk)
    assert any(q.reason=='list_membership_conflict' for q in r.questions)
    assert not any(s['field']=='dg' and s.get('closed_world') for s in r.field_sources)


def test_missing_booking_ref_cannot_authorize_positive_vgm(desk):
    del next(d for d in desk[1] if d['role']=='vgm')['booking_ref']
    r=run(desk)
    assert any(q.reason=='document_scope_mismatch' for q in r.questions)
    assert not any(s['field']=='gross_mass_kg' and s['accepted'] for s in r.field_sources)
