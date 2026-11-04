"""Spawn-safe prep-only target facade, native function/result unchanged."""
import importlib.util,json,os,resource,sys,threading,time
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent
ROOT=HERE.parents[5]
_record_lock=threading.Lock()
def _write(p,value):
    with _record_lock:
        temp=p.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(p)
def guarded_target(function,args,kwargs,owner_pid,deadline):
    # A spawned interpreter does not inherit Python fleet-path monkeypatches.
    path=HERE/'build_ships_maps.py';spec=importlib.util.spec_from_file_location('guard_child_build',path);build=importlib.util.module_from_spec(spec);sys.modules[spec.name]=build;spec.loader.exec_module(build);build.install_runtime()
    pid=os.getpid();started=time.monotonic();record=HERE/'ships'/f'ballast-child-{pid}.json';finished=threading.Event()
    from stowage.params import load_param_file
    raw=load_param_file('fleet',args[0].vessel.id);assert raw['id']==args[0].vessel.id
    def monitor():
        while not finished.is_set():
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;status='running'
            if rss>=56_000_000_000:status='memory_limit'
            if (ROOT/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING').exists():status='BENCH-RUNNING'
            if (HERE/'ships'/f'stop-{owner_pid}.json').exists():status='owner_stop'
            if time.monotonic()>=deadline:status='deadline'
            _write(record,dict(pid=pid,owned_generator_pid=owner_pid,parent_pid=os.getppid(),peak_rss_bytes=rss,status=status,elapsed_s=time.monotonic()-started,private_registry_vessel=raw['id'],native_target=function.__module__+'.'+function.__name__,native_algorithm_unchanged=True))
            if status!='running':os._exit(137 if status=='memory_limit'else 124)
            time.sleep(.1)
    threading.Thread(target=monitor,daemon=True).start()
    try:return function(*args,**kwargs)
    finally:
        finished.set();_write(record,dict(pid=pid,owned_generator_pid=owner_pid,parent_pid=os.getppid(),peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,status='native_returned',elapsed_s=time.monotonic()-started,private_registry_vessel=raw['id'],native_algorithm_unchanged=True))
def install():
    from stowage.solver import ballast
    native=ballast.run_with_deadline
    def supervised(function,args=(),kwargs=None,*,deadline):
        return native(guarded_target,(function,args,{}if kwargs is None else kwargs,os.getpid(),deadline),{},deadline=deadline)
    ballast.run_with_deadline=supervised

def _probe(instance):
    time.sleep(.3)
    return instance.vessel.id
