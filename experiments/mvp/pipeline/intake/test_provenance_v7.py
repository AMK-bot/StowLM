"""Regression probes for v7 sheet coverage and independent evidence boundaries."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file,sha256_json
from . import answer_questions,read_documents
from .realistic import finish
from .spark_reader import RecordedClient
from .test_realistic_v7 import packet
from .test_validation import case


def add_column(document,header,value):
    rows=list(csv.reader(io.StringIO(document['text'])))
    rows[0].append(header)
    for row in rows[1:]:
        row.append(value)
    stream=io.StringIO()
    csv.writer(stream).writerows(rows)
    document['text']=stream.getvalue()


def remove_pod(document):
    rows=list(csv.reader(io.StringIO(document['text'])))
    column=rows[0].index('pod')
    for row in rows[1:]:
        row[column]=''
    stream=io.StringIO()
    csv.writer(stream).writerows(rows)
    document['text']=stream.getvalue()


def run(base,docs,raw):
    return read_documents(docs,base_instance=base,client=RecordedClient(raw))


def test_partial_unknown_populated_header_requires_document_review(packet):
    base,docs,raw,rid,box=packet
    add_column(docs[0],'Final destination as revised','USLAX')
    result=run(base,docs,raw)
    question=next(q for q in result.questions if q.reason=='unmapped_sheet_headers')
    assert question.row is None and question.field=='sheet:sheet'
    assert 'Final destination as revised' in question.prompt
    assert not any(s.get('value')=='USLAX' and s['accepted'] for s in result.field_sources)


def test_fully_unknown_supplementary_sheet_cannot_disappear(packet):
    base,docs,raw,rid,box=packet
    docs.append({'id':'later','mime_type':'text/csv','text':f'Equipment reference,Final carriage call\n{rid},USLAX\n'})
    result=run(base,docs,raw)
    assert any(q.reason=='unmapped_sheet_headers' and q.field=='sheet:later' and q.row is None for q in result.questions)


def test_model_partial_map_does_not_hide_an_uncovered_populated_header(packet):
    base,docs,raw,rid,box=packet
    add_column(docs[0],'Discharge per revision','USLAX')
    raw['header_maps']=[{'document':'sheet','header_row':1,'columns':[
        {'column':1,'header':'id','field':'id'}, {'column':4,'header':'pod','field':'pod'}]}]
    result=run(base,docs,raw)
    assert any(q.reason=='unmapped_sheet_headers' for q in result.questions)


def test_fully_blank_unused_column_does_not_require_review(packet):
    base,docs,raw,rid,box=packet
    add_column(docs[0],'Internal comments','')
    assert run(base,docs,raw).status=='complete'


def test_reviewed_sheet_is_explicit_host_action_not_model_authority(packet):
    base,docs,raw,rid,box=packet
    add_column(docs[0],'Final destination as revised','USLAX')
    raw['reviewed_sheets']=['sheet']
    # The evaluator never reads model-controlled review authority.
    result=finish(raw,docs,base)
    question=next(q for q in result.questions if q.reason=='unmapped_sheet_headers')
    with pytest.raises(ValueError,match='reviewed_sheet'):
        answer_questions(result,{question.id:{'action':'reviewed'}})
    final=answer_questions(result,{question.id:{'action':'reviewed_sheet'}})
    assert final.status=='complete'
    state=json.loads(final.records[-1].notes)['intake_context']['state']
    assert state['reviewed_sheets']==['sheet']
    assert final.rows[0]['pod']==box['pod']
    assert not any(s.get('value')=='USLAX' and s['accepted'] for s in final.field_sources)


def test_deterministic_prose_label_is_not_single_source_authority(packet):
    base,docs,raw,rid,box=packet
    remove_pod(docs[0])
    docs.append({'id':'email','role':'email','text':f'{rid}; POD {box["pod"]}'})
    result=run(base,docs,raw)
    assert any(q.field=='pod' and q.reason=='needs_corroboration' for q in result.questions)
    assert not any(s['accepted'] for s in result.field_sources if s['field']=='pod')


def test_forwarded_wrappers_do_not_create_two_independent_declarations(packet):
    base,docs,raw,rid,box=packet
    remove_pod(docs[0])
    line=f'{rid}; POD {box["pod"]}'
    for i,prefix in enumerate(('> ','>> ')):
        docs.append({'id':f'forward{i}','role':'email','text':f'From: forwarder {i}\n{prefix}{line}\n'})
    result=run(base,docs,raw)
    assert any(q.field=='pod' and q.reason=='needs_corroboration' for q in result.questions)


def test_forwarded_model_quotes_do_not_bypass_parser_quote_deduplication(packet):
    base,docs,raw,rid,box=packet
    remove_pod(docs[0])
    line=f'{rid}; POD {box["pod"]}'
    for i,prefix in enumerate(('> ','>> ')):
        text=f'From: forwarder {i}\n{prefix}{line}\n'
        docs.append({'id':f'forward{i}','text':text})
        raw['observations'].append({'row_id':rid,'field':'pod','value':box['pod'],
                                    'document':f'forward{i}','quote':text})
    result=run(base,docs,raw)
    assert any(q.field=='pod' and q.reason=='needs_corroboration' for q in result.questions)


def test_distinct_genuine_form_roles_can_corroborate_identical_row_words(packet):
    base,docs,raw,rid,box=packet
    remove_pod(docs[0])
    line=f'{rid}; POD {box["pod"]}'
    docs.extend([{'id':'booking','role':'booking','text':'BOOKING CONFIRMATION\n'+line},
                 {'id':'eir','role':'gate_slip','text':'EQUIPMENT INTERCHANGE RECEIPT\n'+line}])
    result=run(base,docs,raw)
    assert result.status=='complete',result.questions
    assert any(s['field']=='pod' and s['accepted'] for s in result.field_sources)


def test_identical_document_bytes_remain_one_source_even_with_different_roles(packet):
    base,docs,raw,rid,box=packet
    remove_pod(docs[0])
    line=f'{rid}; POD {box["pod"]}'
    docs.extend([{'id':'one','role':'booking','text':line},
                 {'id':'two','role':'gate_slip','text':line}])
    result=run(base,docs,raw)
    assert any(q.field=='pod' and q.reason=='needs_corroboration' for q in result.questions)


@pytest.mark.parametrize('field,typed', [('packing_form','packaged_goods_in_closed_freight_containers'),
                                       ('ctu_closed','YES'),('flashpoint_c','23 C')])
def test_contextual_dg_facts_from_a_sheet_stay_unknown(packet,field,typed):
    base,docs,raw,rid,box=packet
    add_column(docs[0],f'dg.{field}',typed)
    rows=list(csv.reader(io.StringIO(docs[0]['text'])))
    raw['header_maps']=[{'document':'sheet','header_row':1,'columns':[
        {'column':1,'header':'id','field':'id'},
        {'column':len(rows[0]),'header':f'dg.{field}','field':f'dg.{field}'}]}]
    result=run(base,docs,raw)
    contextual=[s for s in result.field_sources if s['field']==f'dg.{field}']
    assert contextual and not any(s['accepted'] for s in contextual)
    assert all(s['evidence']=='unknown_contextual_assessment' for s in contextual)
    assert result.rows[0]['dg'] is None


def test_finish_source_hash_covers_public_dispatch_and_all_local_authentication_helpers(packet):
    base,docs,raw,rid,box=packet
    files=('__init__.py','realistic.py','authority.py','paper.py','interpretation.py','sheets.py','cargo_gates.py',
           'provenance.py','text_provenance.py','validation.py','spark_reader.py','measure.py')
    expected=sha256_json({name:sha256_file(Path(__file__).with_name(name)) for name in files})
    assert run(base,docs,raw).records[-1].source_sha256==expected


@pytest.mark.parametrize('field,value',[('declaration_method','1'),('declaration_method','2'),
    ('declaration_date','01/11/2569'),('declaration_date','1/11/2569'),('declaration_date','2026-11-01'),
    ('signatory','N. Chai'),('signatory','สมชาย ใจดี'),
    ('voyage','2611S'),('booking_reference','BK-0001')])
def test_strict_noncargo_metadata_is_preserved_without_cargo_questions(packet,field,value):
    base,docs,raw,rid,box=packet
    header='Supporting form metadata'
    add_column(docs[0],header,value)
    rows=list(csv.reader(io.StringIO(docs[0]['text'])))
    raw['header_maps']=[{'document':'sheet','header_row':1,'columns':[
        {'column':1,'header':'id','field':'id'}, {'column':len(rows[0]),'header':header,'field':field}]}]
    result=run(base,docs,raw)
    assert result.status=='complete',result.questions
    source=next(s for s in result.field_sources if s['field']==field)
    assert source['accepted'] and source['evidence']=='verified_metadata'
    assert source['value']==value and field not in result.rows[0]


@pytest.mark.parametrize('field',['declaration_method','declaration_date','signatory','voyage','booking_reference'])
def test_model_cannot_hide_a_plausible_pod_value_as_noncargo_metadata(packet,field):
    base,docs,raw,rid,box=packet
    header='Final carriage call'
    add_column(docs[0],header,'VNSGN')
    rows=list(csv.reader(io.StringIO(docs[0]['text'])))
    raw['header_maps']=[{'document':'sheet','header_row':1,'columns':[
        {'column':1,'header':'id','field':'id'}, {'column':len(rows[0]),'header':header,'field':field}]}]
    result=run(base,docs,raw)
    assert any(q.field==field and q.reason=='invalid_metadata_cell' for q in result.questions)
    assert not any(s['field']==field and s['accepted'] for s in result.field_sources)


@pytest.mark.parametrize('field,value',[('declaration_method','3'),('declaration_date','31/02/2026'),
    ('signatory','VNSGN'),('signatory','QZXU0000011'),('voyage','22G1'),('booking_reference','12000')])
def test_invalid_metadata_cell_is_a_blocking_exception(packet,field,value):
    base,docs,raw,rid,box=packet
    add_column(docs[0],'Metadata',value)
    rows=list(csv.reader(io.StringIO(docs[0]['text'])))
    raw['header_maps']=[{'document':'sheet','header_row':1,'columns':[
        {'column':1,'header':'id','field':'id'}, {'column':len(rows[0]),'header':'Metadata','field':field}]}]
    assert any(q.reason=='invalid_metadata_cell' for q in run(base,docs,raw).questions)
