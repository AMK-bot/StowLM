"""Resumable D30 validation shards; concurrency follows an admitted budget.

The executive reserves resources before raising the integer in --concurrency.
Each child PID, command, exit and output is recorded. This runner never kills
another process and never silently retries a failed scientific measurement.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--concurrency',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=3791)
    parser.add_argument('--samples',type=int,default=128)
    args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=True)
    script=Path(__file__).with_name('d30_validate.py')
    pending=[(ship,level) for level in ('light','medium','full')
             for ship in ('feeder_500','feeder_1000','feeder_1700')]
    active=[];finished=[];started=time.monotonic()
    def save():
        value={'runner_pid':os.getpid(),'seed':args.seed,'samples_per_case':args.samples,
               'elapsed_seconds':time.monotonic()-started,'pending':pending,
               'active':[{key:value for key,value in job.items() if key not in ('child','stream','timer')}
                         for job in active],'finished':finished}
        target=args.directory/'batch-status.json';temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(target)
    while pending or active:
        workers=int(args.concurrency.read_text().strip())
        if not 1<=workers<=3:raise ValueError('admitted workers must be 1..3')
        while pending and len(active)<workers:
            ship,level=pending.pop(0);output=args.directory/f'{ship}-{level}.json'
            log=output.with_suffix('.log')
            if output.exists():
                existing=json.loads(output.read_text())
                if existing.get('seed')==args.seed and existing.get('samples')==args.samples:
                    from stowage.linear.dependencies import dependency_fingerprint
                    from stowage.linear.margin_policy import policy_inputs
                    from stowage.linear.family_calibration import reference_inputs,reference_cache_identity
                    from stowage.vessel.fleet import load_ship_params
                    prior=existing['physical_inputs_end']
                    if (prior['coefficient_builder']!=dependency_fingerprint('coefficient') or
                        prior['calibration_builder']!=dependency_fingerprint('calibration') or
                        prior['margin_policy']!=policy_inputs() or
                        prior['reference']!=reference_cache_identity(reference_inputs(load_ship_params(ship)))):
                        raise ValueError(f'prior observation has different physical/policy inputs: {output}')
                    finished.append({'ship':ship,'level':level,'status':'existing_observation',
                        'output':str(output),'exit_code':0 if existing.get('all_measured_unsafe_errors_covered')
                        and existing.get('captured_public_residuals_covered') and existing.get('physical_inputs_unchanged')
                        and existing.get('default_structurally_feasible') else 1})
                    continue
                raise ValueError(f'refusing to overwrite a different experiment: {output}')
            command=[sys.executable,str(script),'--ship',ship,'--level',level,
                     '--samples',str(args.samples),'--seed',str(args.seed),'--output',str(output)]
            stream=log.open('w');child=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
            job={'ship':ship,'level':level,'pid':child.pid,'command':command,'output':str(output),
                 'log':str(log),'start':datetime.now().astimezone().isoformat(),
                 'child':child,'stream':stream,'timer':time.monotonic()}
            active.append(job);print(json.dumps({key:value for key,value in job.items()
                if key not in ('child','stream','timer')}),flush=True);save()
        for job in active[:]:
            code=job['child'].poll()
            if code is None:continue
            job['stream'].close()
            result={key:value for key,value in job.items() if key not in ('child','stream','timer')}
            result.update(status='finished',exit_code=code,wall_seconds=time.monotonic()-job['timer'])
            finished.append(result);active.remove(job);print(json.dumps(result),flush=True);save()
        save()
        if pending or active:time.sleep(1)
    peak=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/1024**2
    result={'cases':len(finished),'failures':sum(job['exit_code']!=0 for job in finished),
            'wall_seconds':time.monotonic()-started,'peak_child_rss_mib':peak}
    (args.directory/'compute-status.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
    if result['failures']:raise SystemExit(1)


if __name__=='__main__':main()
