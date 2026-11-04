"""Deferred preregistered medium-region diagnostic, separate immutable cohort.

Never changes frozen full-region admission or common code. Original background
acceptance and every original C1 producer screen remain in place; the only
background amendment is the predeclared120s budget, not acceptance criteria.
"""
import argparse,importlib.util,inspect,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent
NAME='synthetic_3600_closed'
ROOT=HERE.parents[5]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def write(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(p)
def worker(seed=None):
    build=load('medium3600_build',HERE/'build_ships_maps.py');build.NAMES=(NAME,)
    prereg=HERE/'maps/amendment-3600-closed-epoch.json';specs=json.loads(prereg.read_text())['deferred_medium_diagnostics']
    assert len(specs)==12 and {s['seed']for s in specs}=={245100000,245100001}
    assert all(s['ship']==NAME and s['region']=='medium' for s in specs)
    root=HERE/'maps/closed3600-medium-guarded-certified';root.mkdir(exist_ok=True)
    request=root/'requests.json'
    if request.exists():assert json.loads(request.read_text())['specs']==specs
    else:write(request,dict(specs=specs,preregistration_sha256=build.sha(prereg),protocol='Deferredmedium original background gate/C1 screens; budgetonly120s D58 retry; no selection or rule change'))
    build.MAPS=root
    source=inspect.getsource(build.generate_maps);assert source.count('identity=f"XL.')==1
    source=source.replace('identity=f"XL.','identity=f"CERT.CLOSED.MEDIUM.GUARDED.')
    if seed is not None:
        source=source.replace("        parent_spec=dict(",f"        if seed!={seed}:continue\n        parent_spec=dict(",1)
    exec(source,build.__dict__)
    load('medium3600_telemetry',HERE/'ships/progress.py').install(HERE/'ships')
    load('medium3600_e9fix',HERE/'ships/generator/e9fix.py').install()
    load('medium3600_cache',HERE/'prep/geometry_cache.py').install()
    fast=load('medium3600_projection',HERE/'solver/fast_engine.py');fast.THRESHOLD=0;fast.install()
    import medium_ballast_guard
    medium_ballast_guard.install()
    sys.argv=[str(HERE/'build_ships_maps.py'),'maps','--ship',NAME,'--region','medium','--background-s','120']
    build.main()
def main():
    p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--seed',type=int,choices=(245100000,245100001));p.add_argument('--hard-s',type=float,default=600);a=p.parse_args()
    if a.worker:return worker(a.seed)
    sentinel=ROOT/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING'
    if sentinel.exists():raise SystemExit('BENCH-RUNNING: secondary preparation lane unavailable')
    log=HERE/'ships'/('medium3600-guarded-certify'+(f'-s{a.seed}'if a.seed else '')+'.log');started=time.monotonic()
    command=['nice','-n','5',sys.executable,'-B',__file__,'--worker','--hard-s',str(a.hard_s)]
    if a.seed is not None:command+=['--seed',str(a.seed)]
    with log.open('w')as out:
        child=subprocess.Popen(command,stdout=out,stderr=subprocess.STDOUT,env=dict(os.environ,C6_XL_HARD_S=str(a.hard_s)))
        write(log.with_suffix('.ownership.json'),dict(parent_pid=os.getpid(),owned_child_pid=child.pid,hard_s=a.hard_s,command=command))
        while child.poll()is None:
            reason='BENCH-RUNNING sentinel appeared' if sentinel.exists() else 'timeout' if time.monotonic()-started>a.hard_s else None
            if reason:
                write(HERE/'ships'/f'stop-{child.pid}.json',dict(reason=reason))
                if time.monotonic()-started>a.hard_s+2:
                    try:child.terminate()
                    except PermissionError:pass
            time.sleep(.1)
    monitor=HERE/'ships'/f'monitor-{child.pid}.json'
    write(log.with_suffix('.execution.json'),dict(pid=child.pid,returncode=child.returncode,wall_s=time.monotonic()-started,monitor=json.loads(monitor.read_text())if monitor.exists()else None))
    return child.returncode
if __name__=='__main__':sys.exit(main())
