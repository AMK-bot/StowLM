"""Sequential fresh-process epoch audits and model-cache/row timing proofs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from stowage.linear.cache import source_fingerprint
HERE=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--skip-missing',action='store_true');a=p.parse_args()
    helper=runpy.run_path(str(HERE/'audit_loader_epoch.py'))
    schema=runpy.run_path(str(HERE/'audit_schema13_epoch.py'))
    records={};pids={}
    for ship in ('feeder_500','feeder_1000','feeder_1700'):
        for level in ('full','medium','light'):
            name=f'{ship}/{level}';path=a.directory/f'narrow-{ship}-{level}.json'
            if not path.exists():
                records[name]={'status':'missing'};continue
            data=json.loads(path.read_text());source=source_fingerprint()
            direct=data.get('source_clean') and data.get('source_fingerprint_end')==source
            phases=[]
            if not direct and not helper['qualified'](path,data,source) and not schema['qualified'](path,data,source):
                phases.append('audit_schema13_epoch.py')
            phases.append('verify_case_cache.py')
            for script in phases:
                log=path.with_suffix('.'+script.removesuffix('.py')+'.log')
                with log.open('w') as stream:
                    child=subprocess.Popen([sys.executable,str(HERE/script),'--case',str(path)],stdout=stream,stderr=subprocess.STDOUT)
                    pids[name+'/'+script]={'pid':child.pid,'parent_pid':os.getpid(),'status':'running'}
                    (a.directory/'cache-proof-pids.json').write_text(json.dumps(pids,indent=2)+'\n')
                    child.wait()
                    pids[name+'/'+script]['status']='completed'
                    (a.directory/'cache-proof-pids.json').write_text(json.dumps(pids,indent=2)+'\n')
                print(json.dumps({'case':name,'phase':script,'returncode':child.returncode}),flush=True)
                if child.returncode:
                    records[name]={'status':'failed','phase':script,'log':str(log)};break
            else:
                records[name]=json.loads(path.with_suffix('.cache-verification.json').read_text())
            (a.directory/'cache-verification-index.json').write_text(json.dumps(records,indent=2)+'\n')
    (a.directory/'cache-verification-index.json').write_text(json.dumps(records,indent=2)+'\n')
    if any(d['status']!='verified_disk_hit' and not(a.skip_missing and d['status']=='missing') for d in records.values()):raise SystemExit(1)
if __name__=='__main__':main()
