"""Read-only source/input snapshots and a minimum 60-second quiet-window gate."""
import argparse
import json
from pathlib import Path
import runpy
import time
from stowage.linear.cache import source_fingerprint
import stowage.versions as versions
HERE=Path(__file__).resolve().parent
helper=runpy.run_path(str(HERE/'narrow.py'))
p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); p.add_argument('--compare',type=Path); a=p.parse_args()
snapshot={'unix_time':time.time(),'source_fingerprint':source_fingerprint(),'source_file_hashes':helper['source_file_hashes'](),
          'component_versions':{**versions.component_versions(),**{k:v for k,v in vars(versions).items() if k.endswith('_VERSION')}},
          'inputs':{ship:helper['input_snapshot'](ship) for ship in ('feeder_500','feeder_1000','feeder_1700')}}
if a.compare:
    before=json.loads(a.compare.read_text()); elapsed=snapshot['unix_time']-before['unix_time']
    snapshot['quiet_window_seconds']=elapsed
    snapshot['stable_60s']=elapsed>=60 and all(snapshot[k]==before.get(k) for k in ('source_fingerprint','source_file_hashes','component_versions','inputs'))
    snapshot['changed_source_files']=[k for k in set(snapshot['source_file_hashes'])|set(before['source_file_hashes']) if snapshot['source_file_hashes'].get(k)!=before['source_file_hashes'].get(k)]
a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(snapshot,indent=2)+'\n')
print(json.dumps({k:v for k,v in snapshot.items() if k not in ('source_file_hashes','inputs','component_versions')}),flush=True)
if a.compare and not snapshot['stable_60s']: raise SystemExit(1)
