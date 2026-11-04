"""Prove archived import origins, then run one recorded archived controller."""
import argparse
import importlib
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime,timezone
from pathlib import Path
from archive_assets import verify_offset_dependencies, require_active_securing, require_archive_root


def main(manifest,archive):
    began=time.perf_counter();cpu=time.process_time()
    root=Path(__file__).resolve().parent;archive=archive.resolve();spec=json.loads(manifest.read_text());directory=Path(spec['directory']);output=directory/'archive-launcher.json';assert not output.exists()
    def save(value):
        temporary=output.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');temporary.replace(output)
    assert os.environ['PYTHONPATH']==str(archive/'src')
    critical=('stowage.io','stowage.feasibility._engine','stowage.solver.securing_screen','llm.stowlm.mask','llm.stowlm.format','llm.securing.mask_filter','llm.lookahead.mask_filter','llm.lookahead.support_bounds')
    origins={name:str(Path(importlib.import_module(name).__file__).resolve())for name in critical}
    assert all(Path(path).is_relative_to(archive/'src')for path in origins.values())
    modules={name:getattr(module,'__file__',None)for name,module in sys.modules.items()if name=='llm'or name=='stowage'or name.startswith(('llm.','stowage.'))}
    assert 'llm.stowlm.mask_mlx'not in sys.modules
    # Dependency and active-policy qualification precede controller launch.
    # Unknown-data fail-open remains unchanged in the production mask.
    from stowage.params import repo_root
    require_archive_root(archive,repo_root())
    verify_offset_dependencies(archive)
    from stowage.io import load_instance
    from stowage.solver.securing_screen import NecessaryDeckSecuringScreen
    import hashlib
    active_instances = []
    for job in spec['jobs']:
        pinned = job['case']['instance']
        path = Path(pinned['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != pinned['sha256']:
            raise ValueError(f'Benchmark instance hash mismatch: {path}')
        instance = load_instance(path)
        screen = NecessaryDeckSecuringScreen(instance)
        require_active_securing(screen)
        active_instances.append(dict(path=str(path),sha256=pinned['sha256'],
                                     bounds_available=True,policy=screen.policy_name))
    row=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),critical_import_origins=origins,preflight_loaded_modules=modules,active_securing_instances=active_instances,mask_mlx_imported=False,mlx_imported=any(name.startswith(('mlx.','mlx_lm'))for name in sys.modules),pythonpath=os.environ['PYTHONPATH'],completed=False)
    save(row);command=[sys.executable,str(archive/'experiments/mvp/lookahead/controller.py'),'--manifest',str(manifest.resolve())];row['command']=command
    with (directory/'controller.log').open('w')as log:
        process=subprocess.Popen(command,cwd=archive,env={**os.environ,'PYTHONPATH':str(archive/'src')},stdout=log,stderr=subprocess.STDOUT);row['controller_pid']=process.pid;save(row);row['exit_code']=process.wait();row['reaped']=True
    usage=resource.getrusage(resource.RUSAGE_CHILDREN);row.update(completed=True,wall_s=time.perf_counter()-began,launcher_cpu_s=time.process_time()-cpu,child_cpu_s=usage.ru_utime+usage.ru_stime,child_lifetime_peak_rss_bytes=usage.ru_maxrss,ended_utc=datetime.now(timezone.utc).isoformat());save(row);print(json.dumps({key:row[key]for key in ('completed','controller_pid','exit_code','reaped','wall_s')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--archive',type=Path,required=True);args=parser.parse_args();main(args.manifest,args.archive)
