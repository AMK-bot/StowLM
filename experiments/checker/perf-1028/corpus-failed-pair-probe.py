"""Re-evaluate only the two failed fresh pairs; retain complete old/new evidence."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import resource
import time
import traceback

for variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[variable]='1'
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PATHS=[*sorted((ROOT/'src/stowage').rglob('*.py')),
       *sorted((ROOT/'params').rglob('*.toml')),HERE/'corpus.py',
       ROOT/'tests/checker/test_perf_equivalence_1028.py']
CASE_IDS=['current-random_yard-feeder_500-tiny-0','current-simulated_yard-feeder_1000-small-1']


def sources():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in PATHS}


def differences(a,b,path='report'):
    rows=[]
    if isinstance(a,dict) and isinstance(b,dict):
        for key in a.keys()|b.keys():
            if key=='created_at' and path.endswith('.provenance'):continue
            if key not in a or key not in b:
                rows.append(dict(path=path+'.'+key,reference=a.get(key),optimized=b.get(key)))
            else:rows.extend(differences(a[key],b[key],path+'.'+key))
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b):rows.append(dict(path=path+'.length',reference=len(a),optimized=len(b)))
        for index,(x,y) in enumerate(zip(a,b)):rows.extend(differences(x,y,f'{path}[{index}]'))
    else:
        same=type(a)is type(b) and a==b
        if isinstance(a,float) and isinstance(b,float):same=a.hex()==b.hex()
        if not same:
            row=dict(path=path,reference=a,optimized=b)
            if isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):
                row.update(absolute_difference=abs(a-b),within_1e9=math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-9))
            rows.append(row)
    return rows


def main():
    import stowage.checker.full as full
    spec=importlib.util.spec_from_file_location('perf_failed_pairs_corpus',HERE/'corpus.py')
    corpus=importlib.util.module_from_spec(spec);spec.loader.exec_module(corpus)
    all_rows={r['id']:r for r in corpus.cases()}
    result=dict(estimate=dict(wall_s=120,cores=1,ram_gb=2,gpu=False),sources_before=sources(),cases=[])
    started=time.monotonic()
    for case_id in CASE_IDS:
        row=dict(id=case_id,sources_before=sources());folder=HERE/('corpus-failed-pair-'+case_id)
        folder.mkdir(exist_ok=True)
        try:
            t=time.monotonic();instance,plan=corpus.prepare(all_rows[case_id]);row['preparation_s']=time.monotonic()-t
            before=instance.model_dump_json(),plan.model_dump_json()
            for kind,value in [('instance',instance),('plan',plan)]:
                (folder/(kind+'.json')).write_text(value.model_dump_json(indent=2)+'\n')
            reports={}
            for name,function in [('reference',full.check_reference),('optimized',full.check)]:
                t=time.monotonic();report=function(instance,plan);elapsed=time.monotonic()-t
                reports[name]=report.model_dump(mode='json')
                (folder/(name+'-report.json')).write_text(json.dumps(reports[name],indent=2,allow_nan=False)+'\n')
                row[name]=dict(seconds=elapsed,passed=report.passed,violations=[v.model_dump(mode='json') for v in report.violations],
                    parts={key:dict(ran=part.ran,error=part.error) for key,part in report.parts.items()})
            diffs=differences(reports['reference'],reports['optimized'])
            (folder/'differences.json').write_text(json.dumps(diffs,indent=2,allow_nan=False)+'\n')
            row.update(difference_count=len(diffs),numeric_differences=[r for r in diffs if 'absolute_difference' in r],
                inputs_unchanged=(instance.model_dump_json(),plan.model_dump_json())==before)
        except Exception:row['error']=traceback.format_exc()
        row['sources_after']=sources();row['sources_unchanged']=row['sources_before']==row['sources_after']
        result['cases'].append(row)
        (HERE/'corpus-failed-pair-probe-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        print(json.dumps({k:row.get(k) for k in ['id','preparation_s','difference_count','numeric_differences','inputs_unchanged','sources_unchanged','error']}),flush=True)
        for name in ['reference','optimized']:
            if name in row:print(case_id,name,json.dumps(row[name]),flush=True)
    result['sources_after']=sources();result['sources_unchanged']=result['sources_before']==result['sources_after']
    usage=resource.getrusage(resource.RUSAGE_SELF)
    result.update(wall_s=time.monotonic()-started,peak_rss_gb=usage.ru_maxrss/1e9,cpu_s=usage.ru_utime+usage.ru_stime)
    (HERE/'corpus-failed-pair-probe-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ['wall_s','peak_rss_gb','cpu_s','sources_unchanged']}),flush=True)


if __name__=='__main__':main()
