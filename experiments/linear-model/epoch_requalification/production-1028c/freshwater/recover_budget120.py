"""Recover interrupted full original populations under existing checker120s retry.

New shards preserve all original failures. Only the public stability work budget
changes; all seeds, samples, producers, samplers, physics and reserves are fixed.
ABORT must be cleared by the owning executive before any numerical worker starts.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

RUN=Path(__file__).resolve().parent
OWNER=RUN.parent.parent
ROOT=OWNER.parents[2]
sys.path.insert(0,str(RUN.parent))
from compute import run,abort_check
SHIPS=('feeder_500','feeder_1000','feeder_1700')
PRECEDENT=OWNER.parent/'fix-t3-linear/requalify.py'
PRECEDENT_LOG=OWNER.parent/'fix-t3-linear/claude-compute.md'
ATTEMPT=RUN/'retry-budget120'
LOGS=RUN/'recovery-budget120'
SOURCE_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CATALOGUE_MARKERS=('ValidationError','validation errors for Catalogue','pending_contract','superseded_by','extra_forbidden')


def read(path):return json.loads(Path(path).read_text())

def pin(path):
    path=Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def write_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if read(path)!=value:raise ValueError('preserve existing recovery artifact: '+str(path))
        return
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')

def descriptor(ship,name):return read(OWNER/'production'/ship/name)

def verify_population(ship,name,path):
    old=descriptor(ship,name);record=read(path);raw=read(RUN/'ordinary'/ship/'raw.json')
    for key in ('phase','level','seed','samples'):
        if record[key]!=old[key]:raise ValueError(f'{ship}/{name} changed original {key}')
    verify_record(record,raw,old['samples'],path)
    return record

def verify_record(record,raw,samples,path):
    if (len(record['list_row_samples'])!=samples or record['samples']!=samples
            or record.get('physical_inputs_unchanged') is not True
            or record['physical_inputs_start']!=record['physical_inputs_end']
            or record['physical_inputs_start']!=raw['physical_inputs']
            or record.get('public_crosscheck_completed') is not True):
        raise ValueError('incomplete or stale physical population: '+str(path))

def publish_missing(source,target):
    if target.exists():
        if source.read_bytes()!=target.read_bytes():
            raise ValueError('never overwrite canonical evidence: '+str(target))
        return
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)

def expected_jobs():
    jobs=[]
    for ship in SHIPS:
        jobs.append(dict(name='raw-'+ship,kind='raw',ship=ship))
        for path in sorted((OWNER/'production'/ship).glob('*.json')):
            if path.name.startswith(('train-','holdout-','enrichment')):
                jobs.append(dict(name=ship+'-'+path.stem,kind='physical',ship=ship,file=path.name))
        jobs.append(dict(name='boundary-'+ship,kind='boundary',ship=ship))
    for kind,seed,samples,objective,pairs in (
        ('min',3821,64,'min',[]),('grid',3822,128,'grid',[]),
        ('pairs-3823',3823,256,'grid',['DB-M','HEEL','WING-A']),
        ('pairs-3824',3824,256,'grid',['DB-M','HEEL','WING-A'])):
        jobs.append(dict(name='targeted-'+kind,kind='targeted',ship='feeder_1000',target=kind,
                         seed=seed,samples=samples,objective=objective,pairs=pairs))
    if len(jobs)!=46:raise ValueError('frozen original design must have46 jobs')
    return jobs

def original_paths(job):
    ship=job['ship']
    if job['kind']=='raw':return [RUN/'ordinary'/ship/'raw.json']
    if job['kind']=='physical':
        old=descriptor(ship,job['file'])
        return [RUN/'ordinary'/ship/job['file'],RUN/'ordinary-shards'/ship/Path(job['file']).stem/ship/f"{old['phase']}-{old['level']}.json"]
    if job['kind']=='boundary':return list((RUN/'ordinary'/ship).glob('boundary*.json'))
    return list((RUN/'targeted'/job['target']/ship).glob('*.json'))

def job_complete(job):
    ship=job['ship'];kind=job['kind']
    if kind=='raw':return (RUN/'ordinary'/ship/'raw.json').exists()
    if kind=='physical':
        path=RUN/'ordinary'/ship/job['file']
        if not path.exists():return False
        verify_population(ship,job['file'],path);return True
    if kind=='boundary':
        folder=RUN/'ordinary'/ship
        if not all((folder/name).exists() for name in ('boundary.json','boundary-certificate.json','boundary-run.json')):return False
        record=read(folder/'boundary.json');summary=read(folder/'boundary-run.json')
        if record.get('passed') is not True or summary.get('failure') is not None:return False
        if len(record['calibration'])!=64 or len(record['holdout'])!=64:raise ValueError('boundary count changed')
        if (record['calibration_seed'],record['holdout_seed'])!=(3811,3812):raise ValueError('boundary seed changed')
        if summary.get('physical_inputs_unchanged') is not True:raise ValueError('boundary physical drift')
        return True
    path=RUN/'targeted'/job['target']/ship/'train-light.json'
    if not path.exists():return False
    record=read(path)
    verify_record(record,read(RUN/'ordinary'/ship/'raw.json'),job['samples'],path)
    if record['seed']!=job['seed'] or 'sampler_inputs_hash' not in record:return False
    return True

def archive_incomplete_canonical(job):
    """Preserve an unpublished/incomplete artifact before a new complete publish."""
    paths=[]
    if job['kind']=='boundary':paths=list((RUN/'ordinary'/job['ship']).glob('boundary*.json'))
    elif job['kind']=='physical':paths=[RUN/'ordinary'/job['ship']/job['file']]
    for path in paths:
        if path.exists():
            target=ATTEMPT/'preserved-incomplete-canonical'/path.relative_to(RUN)
            if target.exists():raise ValueError('archive already exists: '+str(target))
            target.parent.mkdir(parents=True,exist_ok=True);path.rename(target)

def physical_worker(job):
    from profile_run import load_driver,adapter_pin
    driver=load_driver();ship=job['ship'];name=job['file'];old=descriptor(ship,name)
    prepared=driver.prepare(ship,RUN/'ordinary');shard=ATTEMPT/'physical'/ship/Path(name).stem
    source=shard/ship/f"{old['phase']}-{old['level']}.json"
    write_new(shard/'retry-provenance.json',dict(controller=pin(__file__),precedent=pin(PRECEDENT),
        precedent_log=pin(PRECEDENT_LOG),original_population=pin(OWNER/'production'/ship/name),
        raw=pin(RUN/'ordinary'/ship/'raw.json'),profile_adapter=adapter_pin(),public_stability_budget_s=120,
        original_observations=[pin(p) for p in original_paths(job) if p.exists()],
        unchanged_population={key:old[key] for key in ('phase','level','seed','samples')},
        purpose='Fresh full same-population measurement after external interruption or public checker timeout; no prior exact values reused'))
    driver.physical_case(ship,old['level'],old['phase'],shard,prepared,
                         samples=old['samples'],seed=old['seed'],public=True)
    verify_population(ship,name,source);publish_missing(source,RUN/'ordinary'/ship/name)

def boundary_worker(job):
    from profile_run import install
    install();ship=job['ship'];folder=ATTEMPT/'boundary'
    source=OWNER/'boundary.py'
    spec=importlib.util.spec_from_file_location('freshwater_retry_boundary',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    sys.argv=[str(source),'--ship',ship,'--output',str(folder),'--samples','64','--holdout-samples','64']
    module.main()
    # Certificate evidence intentionally pins new immutable retry shard paths.
    for path in (folder/ship).glob('*.json'):publish_missing(path,RUN/'ordinary'/ship/path.name)

def targeted_worker(job):
    from profile_run import install
    install();source=OWNER/'targeted.py';folder=ATTEMPT/'targeted'/job['target']
    spec=importlib.util.spec_from_file_location('freshwater_retry_targeted',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    sys.argv=[str(source),'--ship',job['ship'],'--level','light','--output',str(folder),
              '--prepared',str(RUN/'ordinary'),'--samples',str(job['samples']),'--seed',str(job['seed']),
              '--mass-objective',job['objective'],'--budget-seconds','1200']
    if job['pairs']:sys.argv+=['--tank-pairs',*job['pairs']]
    module.main()
    # Preserve old targeted shards; publish only the final qualified record.
    source_path=folder/job['ship']/'train-light.json'
    record=read(source_path);verify_record(record,read(RUN/'ordinary'/job['ship']/'raw.json'),job['samples'],source_path)
    if record['seed']!=job['seed'] or 'sampler_inputs_hash' not in record:raise ValueError('targeted design missing')
    publish_missing(source_path,RUN/'ordinary'/job['ship']/('targeted-'+job['target']+'-light.json'))

def worker(name):
    abort_check()
    if os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S')!='120':raise ValueError('explicit public120s budget required')
    job=next(job for job in expected_jobs() if job['name']==name)
    if job['kind']=='physical':physical_worker(job)
    elif job['kind']=='boundary':boundary_worker(job)
    elif job['kind']=='targeted':targeted_worker(job)
    else:raise ValueError('raw must not be remeasured')
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=SOURCE_SHA:raise ValueError('recovery source drift')

def audit():
    statuses=[]
    for job in expected_jobs():
        path=RUN.parent/'compute-logs'/('freshwater-'+job['name']+'.execution.json')
        status=read(path) if path.exists() else None
        if status is not None and 'exit_code' not in status:raise ValueError('unfinished original record: '+job['name'])
        paths=[p for p in original_paths(job) if p.exists()]
        markers=[dict(observation=pin(p),markers=[s for s in CATALOGUE_MARKERS if s in p.read_text()])
                 for p in paths if any(s in p.read_text() for s in CATALOGUE_MARKERS)]
        statuses.append(dict(job=job,original_execution=pin(path) if path.exists() else None,
            exit_code=status.get('exit_code') if status else None,
            status='not_started' if status is None else 'passed' if status['exit_code']==0 else 'aborted' if status['exit_code']==86 else 'failed',
            complete=job_complete(job),observations=[pin(p) for p in paths],catalogue_markers=markers))
    edges=[]
    for path in sorted(RUN.glob('edges-v*/*/*.json')):
        text=path.read_text();record=read(path)
        markers=[s for s in CATALOGUE_MARKERS if s in text]
        edges.append(dict(observation=pin(path),complete=record.get('complete'),samples=record.get('samples'),
                          attempts=record.get('attempts'),markers=markers,whole_population_restart_required=bool(markers)))
    result=dict(controller=pin(__file__),original_jobs=46,jobs=statuses,edge_populations=edges,
                policy='Whole edge populations containing catalogue validation markers must be archived and remeasured before any resume; valid partial populations retain original checkpoint guards')
    write_new(LOGS/'affected-record-audit.json',result);return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker');parser.add_argument('--workers',type=int,choices=range(1,7),default=6)
    parser.add_argument('--phase',choices=('audit','recover'),default='recover')
    args=parser.parse_args()
    if args.worker:worker(args.worker);return
    if args.phase=='audit':audit();return
    abort_check();started=time.monotonic();LOGS.mkdir(exist_ok=True)
    (LOGS/'controller.pid').write_text(str(os.getpid())+'\n')
    report=audit();jobs=[]
    for row in report['jobs']:
        job=row['job']
        if job['kind']=='raw':
            if not row['complete'] or row['status']!='passed':raise ValueError('raw calibration incomplete')
        elif not row['complete']:
            archive_incomplete_canonical(job);jobs.append(job)
    failures=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(run,'freshwater-retry120-'+job['name'],[Path(__file__),'--worker',job['name']],
                             env_extra={'STOWAGE_STABILITY_TIME_BUDGET_S':'120'}):job for job in jobs}
        for future in as_completed(futures):
            try:future.result()
            except Exception as error:failures.append(dict(job=futures[future]['name'],error=str(error),budget_s=120))
    if failures:
        write_new(LOGS/'retry120-findings.json',dict(failures=failures,decision='stop and report; no further budget escalation'))
        raise ValueError('fresh full recovery failed; no further escalation authorized')
    verified=[]
    for job in expected_jobs():
        if job['kind']=='targeted':
            target=RUN/'ordinary'/job['ship']/('targeted-'+job['target']+'-light.json')
            if not target.exists():publish_missing(RUN/'targeted'/job['target']/job['ship']/'train-light.json',target)
            record=read(target);verify_record(record,read(RUN/'ordinary'/job['ship']/'raw.json'),job['samples'],target)
            if record['seed']!=job['seed']:raise ValueError('targeted seed changed')
            verified.append(pin(target))
        elif not job_complete(job):raise ValueError('original design still incomplete: '+job['name'])
        else:verified.extend(pin(p) for p in original_paths(job)[:1] if p.exists())
    for ship in SHIPS:
        path=RUN/'ordinary'/ship/'equivalence.json'
        if not path.exists():run('freshwater-recovery-equivalence-'+ship,[RUN/'ordinary_campaign.py','equivalence-worker',ship],
                                env_extra={'STOWAGE_STABILITY_TIME_BUDGET_S':'120'})
        verified.append(pin(path))
    result=dict(passed=True,controller=pin(__file__),precedent=pin(PRECEDENT),precedent_log=pin(PRECEDENT_LOG),
        original_job_count=46,recovered_jobs=[job['name'] for job in jobs],public_stability_budget_s=120,
        exact_population_design_unchanged=True,failed_shards_preserved=True,canonical_overwrites=0,
        verified_evidence=verified,seconds=time.monotonic()-started)
    write_new(LOGS/'summary.json',result);write_new(RUN/'ordinary-logs/campaign.execution.json',result)
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
