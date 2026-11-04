"""Audit source-authority fixture contracts; never interpret blind documents."""
import ast
import csv
import io
import json
from pathlib import Path
import re

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file,sha256_json
from stowage.model.instance import Instance

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]


def test_blind_original_bytes_remain_frozen_and_prevalence_is_declared():
    path=HERE/'blind_freeze.json'
    manifest=json.loads(path.read_text())
    assert sha256_file(path)==(HERE/'blind_freeze.sha256').read_text().split()[0]
    assert manifest['frozen_before_tuning'] and manifest['frozen_before_scoring'] and manifest['independent_fixture_author']
    assert len(manifest['cases'])==5
    assert manifest['total_boxes']==sum(c['rows']for c in manifest['cases'])==60
    assert len({c['source_map']for c in manifest['cases']})==5
    for case in manifest['cases']:
        assert case['rows']==12
        assert case['trap_rows']==3 and case['trap_rows']/case['rows']<=1/3
        assert case['source_map'].startswith('experiments/mvp/pipeline/maps/dev/')
        for name,digest in case['files'].items():
            assert sha256_file(HERE/case['split']/name)==digest
    generator=HERE.parents[1]/'make_authority_fixtures_v8.py'
    assert manifest['generator_sha256']==sha256_file(generator)
    imports=[node.module or '' for node in ast.walk(ast.parse(generator.read_text()))if isinstance(node,ast.ImportFrom)]
    assert not any('intake' in module for module in imports)


@pytest.mark.parametrize('label',['development_01','development_02','demo'])
def test_public_authoritative_scope_and_ground_truth_are_consistent(label):
    path=HERE/label
    expected=json.loads((path/'expected.json').read_text())
    base_json=json.loads((path/'base_instance.json').read_text())
    base=Instance.model_validate(base_json)
    rows=expected['rows']
    assert len(rows)==len(base.load_list)==12
    assert len(expected['clean_control_ids'])==9
    assert len({t['row_id']for t in expected['traps']})==3
    assert sha256_json(base_json)==expected['base_instance_sha256']
    by_id={r['id']:r for r in base_json['containers']}
    assert all(by_id[r['id']]==r for r in rows)
    assert base.rule_overrides['intake.policy']['document_authority'] is True
    assert all(r['pol']=='THLCH' and r['pod']!='THLCH' and r['gross_mass_kg']%10==0 for r in rows)
    assert base.port.locode=='THLCH'
    docs=json.loads((path/'documents.json').read_text())
    assert len({d['id']for d in docs})==len(docs)
    assert {'booking','shipping_instructions','vgm','dg_declaration','reefer_list','oog_list','gate_slip','tally','email'}<={d['role']for d in docs}
    for doc in docs:
        assert doc['synthetic'] and doc['intake_policy']==8
        assert doc['booking_ref']==expected['booking_ref']
        source=ROOT/doc['path']
        if 'text'in doc:
            assert source.read_bytes().decode()==doc['text']
            assert 'SYNTHETIC' in doc['text']
        if doc.get('complete_booking_list'):
            assert doc['role']in {'booking','dg_declaration','reefer_list','oog_list'}
            assert re.search(r'complete.+(?:list|container)',doc['text'],re.I)
            assert expected['booking_ref']in doc['text']
        if doc['mime_type']=='text/csv':
            cells=list(csv.reader(io.StringIO(doc['text'])))
            assert not any(cell.lstrip().startswith(('{','['))for row in cells for cell in row)
    dg_doc=next(d for d in docs if d['role']=='dg_declaration')
    declared=set(re.findall(r'^([A-Z]{4}\d{7});',dg_doc['text'],re.M))
    assert declared=={r['id']for r in rows if r['dg']}
    assert 'All other containers on this booking are non-DG.'in dg_doc['text']
    assert 'All booked containers are in gauge.'in next(d['text']for d in docs if d['role']=='oog_list')
    missing_tare=next(iter(expected['derived_fields']))
    target=next(r for r in rows if r['id']==missing_tare)
    assert target['iso_type']=='45G1'and target['tare_kg']==3900
    si=list(csv.reader(io.StringIO(next(d['text']for d in docs if d['role']=='shipping_instructions'))))
    assert next(row for row in si if row[0]==missing_tare)[8]==''
    headers=si[3]
    assert 'booking' in headers[4].lower()
    assert any(word in headers[5].lower() for word in ('commodity','goods'))
    assert any(word in headers[6].lower() for word in ('setpoint','setting'))
    assert 'gross' in headers[7].lower() and 'tare' in headers[8].lower()
    for cells in si[4:]:
        assert len(cells)==len(headers)==11
        assert cells[4]==expected['booking_ref']
        assert cells[5]==expected['auxiliary_metadata'][cells[0]]['commodity']
        assert cells[6]=='' or re.fullmatch(r'[+-]?\d+(?:\.\d+)? [CF]',cells[6])
        assert re.fullmatch(r'\d+ kg',cells[7])
        assert cells[8]=='' or re.fullmatch(r'\d+ kg',cells[8])
        assert cells[9]in ('','YES','NO')and cells[10]in ('','YES','NO')
    rf=next(d for d in docs if d['role']=='reefer_list')
    rf_rows=list(csv.reader(io.StringIO(rf['text'])))[4:]
    assert {cells[0]for cells in rf_rows}=={r['id']for r in rows if r['reefer']}
    assert all(cells[1]=='YES'and cells[2].endswith(' C')for cells in rf_rows)
    assert len(expected['tally_cells'])==12
    email=next(d['text']for d in docs if d['role']=='email')
    assert 'บัญชีสินค้าอันตรายครอบคลุมใบจองนี้ทั้งชุด' in email and '2569'in email


def test_discarded_unscored_construction_inputs_are_preserved():
    archive=HERE.with_name('authority_v8_unscored_construction_error')
    old=archive/'blind_freeze.json'
    assert sha256_file(old)=='be5b4bdfeed3fb2be4c727c5bb76397ac29245ba2b232c98106c5d2318ddd6c4'
    manifest=json.loads(old.read_text())
    assert manifest['generator_sha256']==sha256_file(archive/'original_generator.py.archive')
    for case in manifest['cases']:
        for name,digest in case['files'].items():
            assert sha256_file(archive/case['split']/name)==digest
    assert not any(archive.rglob('spark_response.json'))
    assert not any(archive.rglob('spark_raw.json'))
    assert not any(archive.rglob('metrics.json'))
