"""Write six unopened intake cases, then publish only counts and byte hashes.

Preflight uses a throwaway copy of a spent public map. Actual sealed cases are
never re-opened semantically: all construction checks operate on live objects
before serialization. Reading bytes solely for SHA-256 is permitted. No intake
parser, model, scorer or forbidden sealed map set is imported or opened.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from experiments.mvp.pipeline.contract.schema import sha256_file,sha256_json

HERE = Path(__file__).resolve().parent
OUT = HERE / 'fixtures' / 'sealed_intake_v1'
TEMPLATE = HERE / 'make_authority_fixtures_v8.py'


def template():
    spec=importlib.util.spec_from_file_location('independent_intake_fixture_template',TEMPLATE)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prior_usage():
    """Inspect only existing spent metadata; never traverse a sealed directory."""
    used=set()
    fingerprints={}
    def walk(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key=='source_map' and isinstance(child,str):
                    used.add(str((ROOT/child).resolve()))
                else:
                    walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    root=HERE/'fixtures'
    for directory,subdirs,names in __import__('os').walk(root):
        subdirs[:]=[name for name in subdirs if not name.startswith('sealed')]
        for name in names:
            if not (name=='expected.json' or 'manifest' in name.lower() or 'freeze' in name.lower()) or not name.endswith('.json'):
                continue
            path=Path(directory)/name
            walk(json.loads(path.read_text()))
            fingerprints[str(path.relative_to(ROOT))]=sha256_file(path)
    return used,fingerprints


def sources():
    used,pins=prior_usage()
    allowed=[]
    for line in (ROOT/'experiments/mvp/pipeline/maps/dev.txt').read_text().splitlines():
        if not line or line.startswith('#'):continue
        path=(ROOT/line).resolve()
        if path.parent!=(ROOT/'experiments/mvp/pipeline/maps/dev').resolve() or 'sealed'in path.parts:
            raise ValueError('Only approved unsealed development-map paths may be selected')
        if str(path)not in used:allowed.append(path)
    if len(allowed)<6:raise ValueError('Fewer than six unused unsealed development maps remain')
    chosen=allowed[:6]
    assert len(set(chosen))==6 and not set(map(str,chosen))&used
    return chosen,pins,len(used)


def checked_template(output,*,public=False):
    module=template()
    module.OUT=output
    original_csv=module.csv_form
    original_dump=module.dump
    memory={}
    checks={'physical_csv_rows':0,'validated_instances':0,'complete_scopes':0,
            'dg_presence_consistent':0,'canonical_target_rows':0,'no_json_cells':0}
    def csv_checked(title,scope,headers,rows):
        assert len(set(headers))==len(headers)
        assert all(isinstance(header,str)and header.strip()for header in headers)
        for row in rows:
            assert len(row)==len(headers)
            assert all(not str(cell).lstrip().startswith(('{','['))for cell in row)
            assert re.fullmatch(r'[A-Z]{4}\d{7}',str(row[0]))
            if len(headers)==11:
                assert re.fullmatch(r'[A-Z0-9]{4}',str(row[1]))
                assert re.fullmatch(r'[A-Z]{5}',str(row[2]))
                assert row[3]in ('LADEN','EMPTY')
                assert str(row[4]).startswith('SYN-LCH-')
                assert isinstance(row[5],str)and row[5].strip()
                assert row[6]==''or re.fullmatch(r'[+-]?\d+(?:\.\d+)? [CF]',str(row[6]))
                assert re.fullmatch(r'\d+ kg',str(row[7]))
                assert row[8]==''or re.fullmatch(r'\d+ kg',str(row[8]))
                assert row[9]in ('','YES','NO')and row[10]in ('','YES','NO')
            elif len(headers)==5:
                assert re.fullmatch(r'\d+(?:\.\d+)? (?:kg|MT)',str(row[1]))
                assert row[2]=='1'and row[3]=='N. Chai'and row[4]=='02/12/2569'
            elif len(headers)==3:
                assert row[1]=='YES'and re.fullmatch(r'[+-]?\d+(?:\.\d+)? C',str(row[2]))
            else:raise AssertionError('Unexpected form shape')
            checks['physical_csv_rows']+=1
            checks['no_json_cells']+=len(row)
        result=original_csv(title,scope,headers,rows)
        # Parse the just-built string, never a newly written sealed file.
        assert list(csv.reader(io.StringIO(result)))[3]==headers
        return result
    def dump_checked(path,value):
        key=path.parent.name
        state=memory.setdefault(key,{})
        if path.name=='base_instance.json':
            assert len(value['load_list'])==12
            assert value['rule_overrides']['intake.policy']['document_authority']is True
            state['base']=value
            checks['validated_instances']+=1
        elif path.name=='documents.json':
            assert len({doc['id']for doc in value})==len(value)
            for doc in value:
                assert doc['synthetic']and doc['intake_policy']==8
                if doc.get('complete_booking_list'):
                    assert doc['role']in ('booking','dg_declaration','reefer_list','oog_list')
                    assert doc['booking_ref']in doc['text']and re.search(r'complete.+(?:list|container)',doc['text'],re.I)
                    checks['complete_scopes']+=1
            state['documents']=value
        elif path.name=='expected.json':
            base=state['base'];documents=state['documents'];rows=value['rows']
            assert len(rows)==12 and len({row['id']for row in rows})==12
            assert len({t['row_id']for t in value['traps']})<=4
            by_id={row['id']:row for row in base['containers']}
            assert all(by_id[row['id']]==row for row in rows)
            assert sha256_json(base)==value['base_instance_sha256']
            dgd=next(doc for doc in documents if doc['role']=='dg_declaration')
            listed=set(re.findall(r'^([A-Z]{4}\d{7});',dgd['text'],re.M))
            assert listed=={row['id']for row in rows if row['dg']}
            rf=next(doc for doc in documents if doc['role']=='reefer_list')
            rf_ids={cells[0]for cells in list(csv.reader(io.StringIO(rf['text'])))[4:]}
            assert rf_ids=={row['id']for row in rows if row['reefer']}
            assert len(value['tally_cells'])==12
            checks['dg_presence_consistent']+=1
            checks['canonical_target_rows']+=len(rows)
            # Do not retain new sealed document strings/answers after writing.
            memory.pop(key)
        original_dump(path,value)
    module.csv_form=csv_checked
    module.dump=dump_checked
    return module,checks


def preflight():
    """Throwaway PUBLIC construction test; never consumes a fresh selected map."""
    public=HERE/'fixtures/authority_v8/development_01/expected.json'
    spent=json.loads(public.read_text())
    source=ROOT/spent['source_map']
    with tempfile.TemporaryDirectory(prefix='intake-public-preflight-')as directory:
        temporary=Path(directory)
        copied=temporary/'public_source.json'
        shutil.copyfile(source,copied)
        output=temporary/'public_outputs'
        module,checks=checked_template(output,public=True)
        module.ROOT=temporary
        si=['Container','ISO','POL','Empty','Booking reference','Commodity','Setpoint','Gross mass','Tare kg','Marine pollutant','Limited quantity']
        vgm=['Container Number','VGM','Method','Signed by','Date']
        result=module.build(copied,'public_preflight',1,si,vgm,[],public=True)
        assert result['rows']==12 and checks['validated_instances']==1
        assert checks['canonical_target_rows']==12 and checks['dg_presence_consistent']==1
        assert checks['complete_scopes']==4
        # This is a declared PUBLIC throwaway, not a sealed case.
        documents=json.loads((output/'public_preflight/documents.json').read_text())
        sheet=next(doc for doc in documents if doc['role']=='shipping_instructions')
        rows=list(csv.reader(io.StringIO(sheet['text'])))
        assert rows[3]==si and all(cells[4].startswith('SYN-LCH-')for cells in rows[4:])
    return {'public_preflight':'passed','checks':checks}


def generate():
    if OUT.exists():raise ValueError('Sealed destination already exists; never overwrite or regenerate')
    module=template()
    module.wait_for_slot()
    test=preflight()
    selected,usage_pins,used_count=sources()
    OUT.mkdir()
    module,checks=checked_template(OUT)
    module.VESSEL='SYNTHETIC NARA'
    module.VOYAGE='2612E'
    module.POLICY['vessel']=module.VESSEL
    module.POLICY['voyage']=module.VOYAGE
    # Independent fresh wording is private until the one authorized sealed run.
    heads=[
      ['Export equipment identity','Declared equipment ISO','UNLOCODE of loading','Equipment laden condition','Carrier reservation ref','Manifest goods description','Carriage controller temperature','Shipper total mass copy','Tare on equipment declaration','Marine pollutant as declared','Limited quantity as declared'],
      ['Freight box reference','Equipment code ISO6346','Loading terminal location','Full versus empty','Ocean booking identifier','Description for cargo manifest','Operating temperature requirement','Declared gross incl box','Equipment tare recorded','MP marked on SI copy','LQ marked on SI copy'],
      ['Transport unit identifier','Size type for equipment','Port code at loading','Empty/laden declaration','Reservation confirmation no','Declared merchandise','Requested carriage temperature','Whole-unit gross from shipper','Container tare from shipper','Sea pollutant marker','Limited-quantity marker'],
      ['Container serial reference','Equipment ISO size and type','Loading place code','Equipment loading condition','Carrier booking reference','Cargo goods particulars','Temperature setting required','Shipper-declared CTU gross','Empty mass of CTU','Pollutant status on shipper copy','Quantity exemption on shipper copy'],
      ['Equipment shipped under booking','ISO equipment classification','Load location UN code','Load state full/empty','Shipping reservation identifier','Commodity on instructions','Transport thermostat instruction','Declared unit gross weight','Equipment tare as marked','Marine-pollutant declaration copy','LQ declaration copy'],
      ['Cargo equipment serial','ISO container size-type','Export loading location','Laden or empty equipment','Carrier reservation number','Shipper cargo description','Thermostat for sea carriage','Gross weight supplied by shipper','Unladen container mass','MP from shipping instructions','LQ from shipping instructions'],
    ]
    vgms=[
      ['Freight unit verified','VGM including equipment tare','Method number','Declaration signed by','Gross declaration date'],
      ['Container under declaration','Verified gross weight for loading','Verification method','Authorised mass signatory','Local declaration date'],
      ['Certified cargo equipment','Mass verified incl tare','Weighing method number','Signature on VGM','Date of mass certification'],
      ['Unit covered by weighing','Gross certified for vessel','Method of verification','Authorised certifying person','Date declared locally'],
      ['Shipping equipment weighed','Verified mass whole equipment','SOLAS verification method','VGM authorising signatory','Certification local date'],
      ['Equipment identity for VGM','Certified total gross mass','Gross verification method','Signed for mass declaration','Date of VGM signature'],
    ]
    scenarios=[['image_pod','declared_vs_vgm'],['dg_mp_conflict','amendment_pod'],
               ['fahrenheit','reefer_iso','missing_vgm'],['unknown_un','wrong_pol','duplicate'],
               ['cod','temperature_range'],[]]
    cases=[]
    for i,source in enumerate(selected):
        # No new case has been read or scored. Recheck only OLD usage metadata.
        currently_used,_=prior_usage()
        assert str(source)not in currently_used
        result=module.build(source,f'sealed_{i+1:02d}',201+i,heads[i],vgms[i],scenarios[i])
        # Keep manifest metadata non-semantic: counts and byte digests only.
        cases.append({'split':result['split'],'rows':result['rows'],
                      'source_map_sha256':result['source_map_sha256'],'files':result['files']})
    value={'schema_version':'stowlm.intake-sealed-input-freeze/1','cohort':'sealed_intake_v1',
           'evaluation_split':'sealed','synthetic':True,'independent_fixture_author':True,
           'frozen_utc':datetime.now(timezone.utc).isoformat(),'frozen_before_tuning':True,
           'frozen_before_scoring':True,'unopened_after_generation':True,'cases':cases,
           'case_count':len(cases),'total_boxes':sum(case['rows']for case in cases),
           'generator_sha256':sha256_file(Path(__file__)),
           'template_generator_sha256':sha256_file(TEMPLATE),
           'prior_usage_metadata_sha256':sha256_json(usage_pins),
           'excluded_prior_source_maps':used_count,
           'generation_checks':checks,'public_preflight_checks':test['checks']}
    path=OUT/'blind_freeze.json'
    module.dump(path,value)
    digest=sha256_file(path)
    (OUT/'blind_freeze.sha256').write_text(digest+'  blind_freeze.json\n')
    # Never parse/open any just-written sealed input after this publication.
    return {'case_count':len(cases),'box_count':value['total_boxes'],
            'manifest_path':str(path.relative_to(ROOT)),'manifest_sha256':digest,
            'generator_validation_checks':checks,'public_preflight':'passed'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    if args.preflight_only:
        template().wait_for_slot()
        output=preflight()
    else:output=generate()
    print(json.dumps(output,sort_keys=True),flush=True)
