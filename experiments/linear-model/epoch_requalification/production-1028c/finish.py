"""Pre-final: qualify both profiles before promotion, then replay public use."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from compute import run,abort_check
ROOT=Path(__file__).resolve().parent
OWNER=ROOT.parent
SHIPS=('feeder_500','feeder_1000','feeder_1700')

def read(p):return json.loads(p.read_text())
def ready():
    if not (ROOT/'closure/run-producers.json').exists():return False
    for profile in ('seawater','freshwater'):
        folder=ROOT/profile
        for ship in SHIPS:
            if not (folder/'ordinary'/ship/'equivalence.json').exists():return False
            if not (folder/'ordinary'/ship/'boundary-certificate.json').exists():return False
            for version in (1,2,3):
                for phase in ('train','holdout'):
                    p=folder/f'edges-v{version}'/ship/f'necessary-edges-{phase}.json'
                    if not p.exists() or not read(p).get('complete'):return False
        p=folder/'ordinary-logs/campaign.execution.json'
        if not p.exists() or not read(p).get('passed'):return False
    if not (ROOT/'seawater/edges-v3/feeder_500/necessary-review.json').exists():return False
    for name in ('claude-edge-cause','claude-face-pilot','claude-face-budget-pilot'):
        if not (ROOT/'freshwater/diagnostic-edges'/(name+'.json')).exists():return False
    if not (ROOT/'freshwater/review-exclusion.json').exists():
        # The exact certificate location is fixed by the freshwater adapter.
        sys.path.insert(0,str(ROOT/'freshwater'))
        from necessary_adapter import CERTIFICATE
        if not CERTIFICATE.exists():return False
    return True

def controller(name,args):
    """Orchestrators reserve each numerical child internally, not themselves."""
    logs=ROOT/'controller-logs';logs.mkdir(exist_ok=True)
    p=logs/(name+'.execution.json')
    if p.exists():
        prior=read(p)
        if prior.get('exit_code')==0:return
        raise ValueError('preserve prior failed controller: '+str(p))
    command=[sys.executable,*map(str,args)]
    began=time.monotonic()
    with (logs/(name+'.log')).open('w') as log:
        proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
        p.write_text(json.dumps(dict(command=command,pid=proc.pid))+'\n')
        status=proc.wait()
    p.write_text(json.dumps(dict(command=command,pid=proc.pid,exit_code=status,
        seconds=time.monotonic()-began),indent=2)+'\n')
    if status:raise RuntimeError(name+' failed')

def common(profile):
    folder=ROOT/profile
    return [ROOT/'promote_profile.py',profile,'--input',folder/'ordinary','--output',folder/'promotion',
        '--include-necessary','--necessary-edge-input',folder/'edges-v3',
        '--necessary-calibration-enrichment',folder/'edges-v1',folder/'edges-v2']

def publics():
    tasks=[('cache-warm',['-m','stowage.cache_warm'],3),
        ('seawater-public',[OWNER/'public_validate.py','--root',ROOT/'seawater/ordinary','--output',ROOT/'seawater/public-validation.json'],1),
        ('freshwater-public',[ROOT/'freshwater/profile_run.py',OWNER/'public_validate.py','--root',ROOT/'freshwater/ordinary','--output',ROOT/'freshwater/public-validation.json'],1),
        ('saved-voyages',[ROOT/'saved_contexts.py','--phase','validate'],1),
        ('aliases',[ROOT/'aliases.py'],1),('production-C2',[ROOT/'production_smoke.py'],1)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for f in as_completed([pool.submit(run,n,a,cores=c) for n,a,c in tasks]):f.result()

def main():
    began=time.monotonic();(ROOT/'finish-controller.pid').write_text(str(os.getpid())+'\n')
    while not ready():
        abort_check()
        if time.monotonic()-began>7200:raise TimeoutError('qualification evidence unavailable after2h')
        time.sleep(3)
    run('guard-pre-candidate',[ROOT/'closure/guard.py','--check'])
    run('seawater-candidate',common('seawater'))
    seawater=read(ROOT/'seawater/promotion/report.json');assert seawater['passed']
    run('freshwater-candidate',[*common('freshwater'),'--bank',ROOT/'seawater/promotion/candidate-bank.json'])
    freshwater=read(ROOT/'freshwater/promotion/report.json');assert freshwater['passed']
    final_candidate=ROOT/'freshwater/promotion/candidate-bank.json'
    controller('candidate-gates',[ROOT/'verify.py','--phase','candidate','--profile','both','--bank',final_candidate])
    run('guard-pre-apply',[ROOT/'closure/guard.py','--check'])
    run('seawater-apply',[*common('seawater'),'--apply'])
    assert read(ROOT/'seawater/promotion/report.json')['applied']
    run('freshwater-apply',[*common('freshwater'),'--apply'])
    assert read(ROOT/'freshwater/promotion/report.json')['applied']
    def replays():
        controller('ordinary-replays',[ROOT/'verify.py','--phase','ordinary','--profile','both'])
        controller('input-manifests',[ROOT/'verify.py','--phase','manifest','--profile','both'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(publics),pool.submit(replays)]
        for f in futures:f.result()
    run('linear-feasibility-tests',['-m','pytest','tests/linear','tests/feasibility','-q',
        '-o','junit_family=legacy','--junitxml',ROOT/'linear-feasibility-tests.xml'])
    run('results',[ROOT/'summarize.py'])
    run('guard-final',[ROOT/'closure/guard.py','--check'])
    record=dict(label='pre-final',passed=True,seconds=time.monotonic()-began,finished_utc=datetime.now(timezone.utc).isoformat())
    (ROOT/'finish-execution.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
if __name__=='__main__':main()
