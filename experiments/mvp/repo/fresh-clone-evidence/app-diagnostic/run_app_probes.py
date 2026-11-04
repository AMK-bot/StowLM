import json, os, pathlib, resource, subprocess, time

BASE=pathlib.Path('/private/tmp/stowlm-judge-99-3-xzmzsouq')
COPY=BASE/'proposed'
LOGS=BASE/'logs/app-probes'
LOGS.mkdir(parents=True,exist_ok=True)
env=os.environ.copy()
for key in tuple(env):
    if key in {'VIRTUAL_ENV','UV_PROJECT_ENVIRONMENT','PYTHONHOME','SPARK_CREDIT_READY','HF_TOKEN','MODEL_API_KEY'} or any(x in key.upper() for x in ('API_KEY','ACCESS_TOKEN','SECRET')):
        env.pop(key,None)
env.update(PYTHONPATH=str(BASE/'guard'),UV_CACHE_DIR=str(BASE/'uv-cache-full'),HF_HOME=str(BASE/'empty-hf'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',UV_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')

def limit():
    resource.setrlimit(resource.RLIMIT_CPU,(60,65))

def run(name,args):
    began=time.monotonic();before=resource.getrusage(resource.RUSAGE_CHILDREN)
    out=LOGS/f'{name}.stdout';err=LOGS/f'{name}.stderr'
    with out.open('wb') as so,err.open('wb') as se:
        p=subprocess.Popen(args,cwd=COPY,env=env,stdout=so,stderr=se,start_new_session=True,preexec_fn=limit)
        record={'name':name,'pid':p.pid,'argv':args,'cwd':str(COPY),'state':'started','start_epoch':time.time()}
        with (LOGS/'processes.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
        try:code=p.wait(timeout=120);timed=False
        except subprocess.TimeoutExpired:
            p.terminate()
            try:code=p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();code=p.wait(timeout=5)
            timed=True
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    record.update(state='reaped',exit_code=code,wall_s=time.monotonic()-began,cpu_s=(after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime),peak_child_rss_bytes=after.ru_maxrss,timed_out=timed,stdout=str(out),stderr=str(err))
    with (LOGS/'processes.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
    print(json.dumps(record),flush=True)

origin="""import importlib.util,pathlib
root=pathlib.Path.cwd().resolve()
for name in ('stowage','app'):
 s=importlib.util.find_spec(name);assert s and pathlib.Path(s.origin).resolve().is_relative_to(root),(name,s.origin if s else None)
 print('origin',name,s.origin,flush=True)
"""
run('origins',['uv','run','--offline','--no-sync','python','-c',origin])
run('app-start',['uv','run','--offline','--no-sync','python','-m','app','--instance','data/fixtures/empty_ship.json','--no-discovery','--no-companions','--port','0'])
checker=origin+"""import json
from stowage.io import load_instance
from stowage.model import PlannerResult
from stowage.checker.full import check,report_matches_inputs
i=load_instance('data/fixtures/empty_ship.json')
r=PlannerResult.model_validate(json.load(open('app/tests/fixtures/model_run/instances/fixture_empty_ship/result.json'))['result'])
report=check(i,r.plan)
out={'passed':report.passed,'binding':report_matches_inputs(report,i,r.plan),'report':report.model_dump(mode='json')}
print(json.dumps(out,allow_nan=True),flush=True)
"""
run('full-checker',['uv','run','--offline','--no-sync','python','-c',checker])
patch='experiments/mvp/determinism/patches/securing-fail-closed.patch'
run('d115-applicability',['git','apply','--check',patch])
securing=origin+"""from stowage.io import load_instance
from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
from llm.securing import SecuringEngine
i=load_instance('data/fixtures/empty_ship.json')
e=make_feasibility_engine(i,config=FeasibilityConfig(extra={'global_screen':0}))
s=SecuringEngine(e)
print('synthetic_securing_constructed',s.screen.bounds_available,flush=True)
"""
run('securing-baseline',['uv','run','--offline','--no-sync','python','-c',securing])
