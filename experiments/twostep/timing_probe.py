"""One-core diagnostic of public v2 setup and first action enumeration, no tuning."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time
import signal

# The diagnostic owns its timeout; it cannot leave a background orphan.
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(SystemExit(124)))
signal.alarm(180)
for variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[variable] = '1'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance-root',type=Path,default=Path('experiments/instances-v2'))
    parser.add_argument('--output',type=Path,default=Path('experiments/twostep/v2-timing.json'))
    parser.add_argument('--cap-seconds',type=int,default=180)
    args=parser.parse_args()
    signal.alarm(args.cap_seconds)
    args.output.with_suffix('.pid').write_text(str(os.getpid())+'\n')
    started=time.perf_counter()
    row={}
    def stage(name, operation):
        row['active']=name
        args.output.write_text(json.dumps(row,indent=2)+'\n')
        before=time.perf_counter()
        result=operation()
        row[name+'_seconds']=time.perf_counter()-before
        row['elapsed_seconds']=time.perf_counter()-started
        args.output.write_text(json.dumps(row,indent=2)+'\n')
        print(json.dumps(row),flush=True)
        return result
    from stowage.io import load_instance
    from stowage.actions import get_action_space
    from stowage.solver.twostep import _canonical_instance
    from stowage.solver.twostep_adapter import make_planning_engine
    manifest=json.loads((args.instance_root/'manifest.json').read_text())
    shards=manifest['groups']['random_yard/tiny']['shards']
    selected=next(shard for split in ('train','val') for shard in shards if shard['split']==split)
    row.update(split=selected['split'],instance=selected['instance'])
    instance=stage('load_instance',lambda:load_instance(args.instance_root/'random_yard/tiny'/selected['instance']))
    canonical=stage('canonical_instance',lambda:_canonical_instance(instance))
    engine=stage('factory',lambda:make_planning_engine(canonical,get_action_space()))
    actions=stage('legal_actions',engine.legal_actions)
    row['actions']=len(actions)
    row['active']='done'
    args.output.write_text(json.dumps(row,indent=2)+'\n')
if __name__=='__main__':
    main()
