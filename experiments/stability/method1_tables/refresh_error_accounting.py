"""Recompute both-sided error accounting from stored independent curve pairs.

This performs no numerical physics run and writes no qualification manifest.
It preserves the original measured curves, timings, and their provenance.
"""
import importlib.util
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('method1_refresh_benchmark',ROOT/'benchmark.py')
benchmark=importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def evidence():
    return dict(source=str((ROOT/'benchmark.py').relative_to(ROOT.parents[2])),
        source_sha256=hashlib.sha256((ROOT/'benchmark.py').read_bytes()).hexdigest(),
        scope='max GZ/L5 includes both leeward and windward signed curves; common-domain signed area diagnostics retained on both sides',
        measurements='postprocessing stored curve pairs only; measured curves and timings unchanged')


def save(path,payload):
    temporary=path.with_suffix('.accounting.tmp')
    temporary.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def loaded(path):
    data=json.loads(path.read_text())
    mapping=dict(gz='max_gz_l5_fraction',area='max_area_l5_fraction',gm='gm_l5_fraction')
    for case in data['cases']:
        case['errors']=benchmark.compare(case['direct'],case['fast'],scope=data['comparison_scope'])
        case['passed']=all(case['errors'][key]<=.2 for key in mapping.values()) and bool(case['table_queries'])
    data['maxima']={key:max(case['errors'][key] for case in data['cases']) for key in mapping.values()}
    manifest=json.loads(Path(data['manifest']).read_text())
    tanks=json.loads(Path(manifest['validation']['tank_component_source']).read_text())
    budget=data['empirical_error_budget']
    budget['liquid']={key:max(tanks['maxima'][key],data['maxima'][metric]) for key,metric in mapping.items()}
    budget['combined']={key:(max(tanks['maxima'][key]+budget['kn'][key],data['maxima'][mapping[key]])
        if budget['full_direct_reference'] else budget['liquid'][key]+budget['kn'][key]) for key in mapping}
    data['all_passed']=all(case['passed'] for case in data['cases']) and all(value<=.2 for value in budget['combined'].values())
    data['error_accounting_refresh']='Stored measured curves re-accounted on both leeward and windward GZ branches; no new physics measurements or timing changes'
    data['error_accounting_evidence']=evidence()
    save(path,data)
    return dict(vessel=data['vessel'],maxima=data['maxima'],combined=budget['combined'],all_passed=data['all_passed'],
        manifest_numeric_limits_unchanged=all(manifest['validation'][key]==budget['combined'][key] for key in mapping))


def departure(path):
    data=json.loads(path.read_text())
    if not data.get('complete'):
        return dict(vessel=data['vessel'],skipped='benchmark still running')
    reference=json.loads(Path(data['reference_path']).read_text())
    data['loaded_interpolation_error']=benchmark.compare(reference['method1'],data['method1_cold'])
    data['error_accounting_refresh']='Stored measured curves re-accounted on both GZ branches; no new measurements'
    data['error_accounting_evidence']=evidence()
    save(path,data)
    return dict(vessel=data['vessel'],errors={key:data['loaded_interpolation_error'][key]
        for key in ('max_gz_l5_fraction','max_area_l5_fraction','gm_l5_fraction')})


if __name__=='__main__':
    for name in ('feeder_500','feeder_1000','feeder_1700'):
        print(json.dumps(loaded(ROOT/f'loaded_{name}.json')))
    for name in ('feeder_500','feeder_1000','feeder_1700'):
        print(json.dumps(departure(ROOT/f'benchmark_{name}.json')))
