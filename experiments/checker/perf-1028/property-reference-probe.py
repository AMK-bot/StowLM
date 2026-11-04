"""One same-input old/new property-fixture probe; no source or policy changes."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import time
import traceback

for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
FILES=[ROOT/'tests/checker/test_full_properties.py',ROOT/'tests/checker/test_full_voyage.py',
       ROOT/'src/stowage/versions.py',*sorted((ROOT/'src/stowage/checker').glob('*.py')),
       *sorted((ROOT/'src/stowage/vessel').glob('fleet*.py'))]

def sources():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in FILES}


def main():
    import stowage.checker.full as full
    started=time.monotonic()
    result=dict(estimate=dict(wall_s=120,cores=1,ram_gb=2,gpu=False),sources_before=sources())
    try:
        t=time.monotonic()
        build=runpy.run_path(str(ROOT/'tests/checker/test_full_properties.py'))['_random_plan']
        instance,plan=build(0)
        result['preparation_s']=time.monotonic()-t
        originals=instance.model_dump_json(),plan.model_dump_json()
        for name,value in [('instance',instance),('plan',plan)]:
            (HERE/f'property-reference-{name}.json').write_text(value.model_dump_json(indent=2)+'\n')
        reports={}
        for name,function in [('reference',full.check_reference),('optimized',full.check)]:
            t=time.monotonic();report=function(instance,plan);elapsed=time.monotonic()-t
            reports[name]=report.model_dump(mode='json')
            (HERE/f'property-reference-{name}-report.json').write_text(json.dumps(reports[name],indent=2,allow_nan=False)+'\n')
            result[name]=dict(seconds=elapsed,passed=report.passed,
                violations=[v.model_dump(mode='json') for v in report.violations],
                parts={key:dict(ran=part.ran,error=part.error,
                    violations=[v.model_dump(mode='json') for v in part.violations],
                    metrics=part.metrics,margins=part.margins)
                    for key,part in report.parts.items()})
        # Match the documented determinism contract; timestamps alone are
        # informational. Preserve and compare every report number bit-for-bit.
        for value in reports.values():value['provenance']['created_at']=None
        left=json.dumps(reports['reference'],sort_keys=True,separators=(',',':'),allow_nan=False)
        right=json.dumps(reports['optimized'],sort_keys=True,separators=(',',':'),allow_nan=False)
        result['reports_bitwise_identical_except_informational_timestamp']=left==right
        result['inputs_unchanged']=(instance.model_dump_json(),plan.model_dump_json())==originals
        assert left==right,'Reference and optimized reports differ'
        assert result['inputs_unchanged'],'Checker mutated inputs'
    except Exception:
        result['error']=traceback.format_exc()
    result['sources_after']=sources()
    result['sources_unchanged']=result['sources_before']==result['sources_after']
    result['wall_s']=time.monotonic()-started
    usage=resource.getrusage(resource.RUSAGE_SELF)
    result['peak_rss_gb']=usage.ru_maxrss/1e9
    result['cpu_s']=usage.ru_utime+usage.ru_stime
    (HERE/'property-reference-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({key:result.get(key) for key in ['wall_s','preparation_s','peak_rss_gb','cpu_s',
        'reports_bitwise_identical_except_informational_timestamp','inputs_unchanged','sources_unchanged','error']}),flush=True)
    for name in ['reference','optimized']:
        if name in result:print(name,json.dumps(dict(passed=result[name]['passed'],seconds=result[name]['seconds'],
            violations=result[name]['violations'])),flush=True)
    return bool(result.get('error')) or not result['sources_unchanged']


if __name__=='__main__':
    raise SystemExit(main())
