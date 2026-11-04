"""Fresh immutable-runtime D132 checker/manual/GM worker; no numerical signals."""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import resource
import sys
import time

from runtime import (FENCE, LIVE_ROOT, configure_runtime, verify_runtime, assert_runtime_imports,
                     install_write_guard, save, sha, object_sha, read)


def caches(output, seed_vessels=True):
    original = LIVE_ROOT/'experiments/mvp/fixwindow/d126-20261030/cache'
    own = output/'cache'
    stable = own/'stability'
    vessel = own/'vessels'
    stable.mkdir(parents=True,exist_ok=True);vessel.mkdir(exist_ok=True)
    for source in (original/'stability').iterdir():
        target=stable/source.name
        if source.is_file() and not target.exists() and not target.is_symlink():
            target.symlink_to(source.resolve())
    for source in ((LIVE_ROOT/'experiments/mvp/batch-d132/checker/candidate/cache/vessels').glob('*.json') if seed_vessels else ()):
        target=vessel/source.name
        if source.is_file() and not target.exists() and not target.is_symlink():
            target.symlink_to(source.resolve())
    os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(vessel)
    import stowage.linear.cache as linear
    import stowage.checker.stability_tables as tables
    import stowage.checker.stability_liquid_tables as liquid
    import stowage.checker.stability_liquid as direct
    import stowage.checker.stability_sounding as sounding
    linear._DEFAULT_ROOT=original/'linear'
    for module in (liquid,direct,sounding):module.CACHE_DIRECTORY=stable
    tables.CACHE_DIR=stable
    for name in ('load_table_geometry','cached_geometry','migrate_legacy'):
        function=getattr(tables,name)
        if function.__kwdefaults__ and 'cache_dir' in function.__kwdefaults__:
            function.__kwdefaults__['cache_dir']=stable


def setup(output, runtime, label, ships):
    from stowage.vessel.fleet import load_vessel, load_ship_params
    from stowage.vessel.fleet_cache import _cache_key
    rows=[];start=time.perf_counter();cpu=time.process_time()
    for ship in ships:
        params=(runtime/f'params/fleet/{ship}.toml').read_bytes()
        for hull in (True,False):
            cache_key=_cache_key(ship,'full',hull,params)
            path=output/'cache/vessels'/f'{ship}.full.{"hull" if hull else "geometry"}.{cache_key}.json'
            hit=path.is_file()
            started=time.perf_counter();vessel=load_vessel(ship,with_hull=hull)
            rows.append({'ship':ship,'with_hull':hull,'key':cache_key,'cache_hit_before':hit,
                'cache_exists_after':path.is_file(),'wall_s':time.perf_counter()-started,
                'vessel_sha256':object_sha(vessel.model_dump(mode='json')),
                'raw_model_dump_json_sha256':__import__('hashlib').sha256(vessel.model_dump_json().encode()).hexdigest(),
                'max_gm_lashing_m':vessel.hull.max_gm_lashing_m if vessel.hull else None})
            save(output/f'setup-{label}.json',{'rows':rows,'complete':len(rows)==2*len(ships),
                'fleet_complete':len(rows)==6,
                'wall_s':time.perf_counter()-start,'cpu_s':time.process_time()-cpu})
    return rows


def checks(args, output, label):
    from stowage.checker import check, report_matches_inputs
    from stowage.frozen import require_clean
    from stowage.io import load_instance,load_plan
    inv=read(FENCE/'inventory.json')
    excluded=inv['exclusion'];cases=inv['cases']
    selected=cases[args.start:args.start+args.limit]
    if args.keys:
        selected=[c for c in cases if c['key'] in args.keys.split(',')]
    if args.pilot:
        selected=[cases[i] for i in (0,20,40,60,73,86)]
    for case in selected:
        if (FENCE/'STOP').exists():
            raise RuntimeError('Owner STOP observed between plans; prior checkpoints preserved')
        assert case['split'] in ('train','val') and case['corpus_path'].startswith(case['split']+'/')
        assert case['input_sha256'] not in excluded['test_input_hashes']
        assert case['instance_id'] not in excluded['test_instance_ids']
        directory=output/case['key'];directory.mkdir(exist_ok=True)
        if (directory/'record.json').exists():
            prior=read(directory/'record.json')
            assert prior['runtime_epoch']==verify_runtime(args.runtime)['source_epoch_sha256']
            assert sha(directory/'report.json')==prior['report_sha256']
            continue
        assert sha(case['input_path'])==case['input_sha256']
        assert sha(case['plan_path'])==case['plan_sha256']
        instance,plan=load_instance(case['input_path']),load_plan(case['plan_path'])
        require_clean(instance,plan,what='D132 TRAIN/VAL saved inputs')
        before=object_sha([instance.model_dump(mode='json'),plan.model_dump(mode='json')])
        cpu,wall=time.process_time(),time.perf_counter()
        report=check(instance,plan)
        elapsed_cpu,elapsed_wall=time.process_time()-cpu,time.perf_counter()-wall
        assert before==object_sha([instance.model_dump(mode='json'),plan.model_dump(mode='json')])
        assert report_matches_inputs(report,instance,plan)
        require_clean(report,what='D132 full report')
        assert_runtime_imports(args.runtime)
        verify_runtime(args.runtime)
        # Canonical FULL report bytes: no field, float, provenance or limit stripped.
        value=report.model_dump(mode='json')
        payload=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
        (directory/'report.json').write_bytes(payload)
        row={'key':case['key'],'rung':case['rung'],'ship':case['ship'],'split':case['split'],
             'input_sha256':case['input_sha256'],'plan_sha256':case['plan_sha256'],
             'runtime_epoch':verify_runtime(args.runtime)['source_epoch_sha256'],
             'report_sha256':sha(directory/'report.json'),'passed':report.passed,
             'cpu_s':elapsed_cpu,'wall_s':elapsed_wall,'inputs_immutable':True,
             'full_report_bound':True,'hard_rules':sorted({v.rule_id for v in report.violations if v.severity=='hard'})}
        if args.baseline:
            base=args.baseline/case['key']/'report.json'
            row['baseline_report_sha256']=sha(base)
            row['exact']=base.read_bytes()==payload
        save(directory/'record.json',row)
        all_rows=[read(p) for p in output.glob('case-*/record.json')]
        save(output/'STATUS.json',{'expected':100,'reports':len(all_rows),'complete':len(all_rows)==100,
            'exact':sum(r.get('exact',False) for r in all_rows),
            'mismatches':[r['key'] for r in all_rows if r.get('exact') is False],
            'errors':[r['key'] for r in all_rows if 'checker.internal_error' in r['hard_rules']],
            'wall_s':sum(r['wall_s'] for r in all_rows),'cpu_s':sum(r['cpu_s'] for r in all_rows)})
        print(json.dumps(row),flush=True)


def manual(args, output):
    import tempfile
    temp=output/'pytest-temp'
    temp.mkdir(exist_ok=True)
    os.environ['TMPDIR']=str(temp)
    tempfile.tempdir=str(temp)
    import pytest
    expected=[args.runtime/'tests'/p for p in ('checker/test_rules_heat_ids.py',
       'vessel/test_fleet_manual_restore.py','vessel/test_fleet_lashing_design.py','vessel/test_fleet_d56.py')]
    helpers=[p for p in (args.runtime/'conftest.py',args.runtime/'tests/conftest.py') if p.is_file()]
    before={str(p.relative_to(args.runtime)):sha(p) for p in [*expected,*helpers]}
    code=pytest.main(['-p','no:cacheprovider','-q','--basetemp='+str(temp/'basetemp'),*[str(p) for p in expected],
                     '--junitxml='+str(output/'manual40.xml')])
    after={str(p.relative_to(args.runtime)):sha(p) for p in [*expected,*helpers]}
    assert before==after,'Staged manual test source changed while running'
    save(output/'manual40-source-pins.json',{'files':before,'unchanged':True,
         'junitxml_sha256':sha(output/'manual40.xml'),'pytest_exit_code':code})
    return code


def gm(args,output):
    import math
    from stowage.vessel.fleet import load_vessel,load_ship_params
    from stowage.vessel.fleet_lashing_design import populate_lashing_design
    expected={'feeder_500':(3.2838356051426465,'C3'),
              'feeder_800':(3.9584570964621637,'C2'),
              'feeder_1700':(5.530108952747626,'C3')}
    rows=[]
    for ship,(cap,governing) in expected.items():
        start,cpu=time.perf_counter(),time.process_time()
        vessel=load_vessel(ship)
        audit=populate_lashing_design(vessel,load_ship_params(ship),'D132 combined-source six-condition recomputation')
        records=audit['conditions']
        assert len(records)==6 and {(r['condition'],r['phase']) for r in records}=={
            (c,p) for c in ('C1','C2','C3') for p in ('departure','arrival')}
        assert all(math.isfinite(r['gm_m']) for r in records)
        maximum=max(records,key=lambda r:r['gm_m'])
        assert audit['governing']==maximum and audit['factor']==1.1
        assert audit['max_gm_lashing_m']==1.1*maximum['gm_m']==cap
        assert maximum['condition']==governing and maximum['phase']=='departure'
        rows.append({'ship':ship,'cap_m':cap,'governing':maximum,'audit':audit,
                     'wall_s':time.perf_counter()-start,'cpu_s':time.process_time()-cpu})
        save(output/'actual-six-condition-gm.json',{'complete':len(rows)==3,'rows':rows,
             'erratum':'Historical D126 C3-every-ship assumption was wrong: actual governing C3/C2/C3.'})
        print(json.dumps({'ship':ship,'cap_m':cap,'governing':maximum['condition'],'phase':maximum['phase']}),flush=True)
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('setup','checks','manual','gm'))
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--arm',choices=('baseline','baseline-cold','candidate'),required=True)
    p.add_argument('--start',type=int,default=0);p.add_argument('--limit',type=int,default=100)
    p.add_argument('--keys');p.add_argument('--baseline',type=Path);p.add_argument('--pilot',action='store_true')
    p.add_argument('--ships',default='feeder_500,feeder_800,feeder_1700')
    p.add_argument('--empty-vessel-cache',action='store_true',help='Never seed prior whole-vessel entries; preserve genuine cold control')
    p.add_argument('--tag',default='')
    args=p.parse_args()
    assert os.environ.get('PYTHONHASHSEED')=='0','Start interpreter with PYTHONHASHSEED=0'
    manifest=configure_runtime(args.runtime)
    output=FENCE/args.arm;output.mkdir(exist_ok=True)
    ships=args.ships.split(',')
    assert ships and len(ships)==len(set(ships)) and set(ships)<={'feeder_500','feeder_800','feeder_1700'}
    label=f'{args.mode}-{args.start}-{args.limit}'+('-selected' if args.keys else '')
    if args.pilot:label+='-pilot'
    if len(ships)!=3:label+='-'+args.ships
    if args.tag:
        assert args.tag.replace('-','').isalnum()
        label+='-'+args.tag
    config={'runtime':str(args.runtime),'runtime_epoch':manifest['source_epoch_sha256'],
            'python':sys.version,'executable':sys.executable,'hashseed':0,'native_threads':1,
            'singleflight':os.environ.get('STOWAGE_VESSEL_SINGLEFLIGHT'),
            'arm':args.arm,'seed_prior_vessel_files':not args.empty_vessel_cache,
            'dependency_versions':{p:version(p) for p in ('numpy','scipy','pydantic','pytest')},
            'inventory_sha256':sha(FENCE/'inventory.json'),'harness_sha256':sha(__file__),'mode':args.mode}
    save(output/f'config-{label}.json',config)
    denied,reads=install_write_guard((args.runtime,))
    caches(output,seed_vessels=not args.empty_vessel_cache)
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert rule_freeze_v1_enabled() is False
    start,cpu=time.perf_counter(),time.process_time();code=0
    try:
        if args.mode=='setup':setup(output,args.runtime,label,ships)
        elif args.mode=='checks':checks(args,output,label)
        elif args.mode=='manual':code=manual(args,output)
        else:code=gm(args,output)
    finally:
        ending=verify_runtime(args.runtime)
        assert ending==manifest
        origins=assert_runtime_imports(args.runtime)
        save(output/f'audit-{label}.json',{'source_unchanged':True,'runtime_epoch':manifest['source_epoch_sha256'],
            'blocked_writes':denied,'shared_cache_read_metadata':reads,'imports':origins,
            'wall_s':time.perf_counter()-start,'cpu_s':time.process_time()-cpu,
            'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)})
    return code


if __name__=='__main__':raise SystemExit(main())
