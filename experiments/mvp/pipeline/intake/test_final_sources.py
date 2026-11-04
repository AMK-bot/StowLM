"""Explicit equipment claims and redundant sheet proposals retain strict proof."""
from copy import deepcopy
import pytest

from .test_authority_v8 import desk, run
from .test_realistic_v7 import packet
from .test_validation import case
from .paper import baseline_paper, grounded
from .sheets import parse_sheet
from .interpretation import proposal, SCHEMA


@pytest.mark.parametrize('field,value,header,unit',[
    ('length_ft',40.,'Length ft','ft'),('height_m',2.896,'Height m','m'),
    ('tank',True,'Tank',None),('equipment_reefer',False,'Reefer equipment',None)])
def test_explicit_equipment_fields_are_software_bound(field,value,header,unit):
    cell=str(value) if unit else 'YES' if value else 'NO'
    doc={'id':'sheet','text':f'Container,{header}\nABCU1234567,{cell}\n'}
    mapping={'document':'sheet','header_row':1,'columns':[
        {'column':1,'header':'Container','field':'id'},
        {'column':2,'header':header,'field':field,**({'unit':unit} if unit else {})}]}
    parsed=parse_sheet(doc,mapping)
    assert not parsed['issues']
    assert next(o['value'] for o in parsed['observations'] if o['field']==field)==value
    if unit:
        doc['text']=doc['text'].replace(header,'Number')
        mapping['columns'][1]['header']='Number'
        assert any(q['reason']=='missing_or_invalid_unit' for q in parse_sheet(doc,mapping)['issues'])


@pytest.mark.parametrize('field,label,value,unit',[
    ('length_ft','LENGTH',40,'ft'),('height_m','HEIGHT',2.896,'m')])
def test_prose_geometry_is_label_and_unit_bound(field,label,value,unit):
    quote=f'ABCU1234567; {label} {value} {unit}'
    obs={'row_id':'ABCU1234567','field':field,'value':value,'quote':quote,'unit':unit}
    assert grounded(obs,{'text':quote})
    assert not grounded({**obs,'value':value+1},{'text':quote})
    wrong=quote.replace(label,'REFERENCE')
    assert not grounded({**obs,'quote':wrong},{'text':wrong})
    assert any(o['field']==field for o in baseline_paper({'id':'paper','text':quote}))


def test_equipment_claims_are_allowed_but_never_derived():
    raw={'schema_version':SCHEMA,'observations':[{'row_id':'ABCU1234567','field':'tank',
        'value':True,'document':'paper','quote':'ABCU1234567 TANK YES'}]}
    assert proposal(raw,[{'id':'paper'}])


@pytest.mark.parametrize('field,declarations',[
    ('iso_type','ISO 22G1; ISO 22R1'),('tank','TANK NO; TANK YES'),
    ('length_ft','LENGTH 20 ft; LENGTH 40 ft')])
def test_independent_explicit_equipment_parser_preserves_both_claims(field,declarations):
    observations=baseline_paper({'id':'paper','text':f'ABCU1234567; {declarations}'})
    assert len({str(o['value']) for o in observations if o['field']==field})==2


def test_redundant_model_csv_value_never_becomes_evidence_or_a_task(desk):
    base,docs,raw,rid,box=desk
    raw['observations'].append({'row_id':rid,'field':'gross_mass_kg','value':box['gross_mass_kg'],
                                'document':'vgm','quote':'this model quote is ignored','unit':'kg'})
    result=run(desk)
    assert result.status=='complete',result.questions
    traces=[s for s in result.field_sources if s.get('evidence')=='rejected_model_sheet']
    assert traces and not any(s['accepted'] for s in traces)


def test_unsupported_model_csv_value_still_stops(desk):
    raw=desk[2]
    raw['observations'].append({'row_id':desk[3],'field':'gross_mass_kg','value':12345,
                                'document':'vgm','quote':'12345 kg','unit':'kg'})
    result=run(desk)
    assert any(q.reason=='model_sheet_value' for q in result.questions)
    assert not any(s['accepted'] and s.get('value')==12345 for s in result.field_sources)


def test_grounded_supplementary_geometry_stops_iso_even_with_other_questions(desk):
    base,docs,raw,rid,box=desk
    length=40 if box['length_ft']==20 else 20
    text=f'{rid}; LENGTH {length} ft'
    docs.append({'id':'email','role':'email','text':text})
    docs[:]=[d for d in docs if d['role']!='vgm']
    result=run(desk)
    assert any(q.field=='iso_type' for q in result.questions)
    assert not any(s['accepted'] and s['field']=='iso_type' for s in result.field_sources)
