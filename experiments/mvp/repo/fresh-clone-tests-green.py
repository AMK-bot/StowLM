"""Run only the admitted TRAIN-independent fast subset in the exact candidate copy."""
from pathlib import Path
import json, os, resource, subprocess, time

BASE=Path('/private/tmp/stowlm-judge-99-3-xzmzsouq')
TREE=BASE/'proposed'
REPORT=Path('experiments/mvp/repo/fresh-clone-tests-green.json')
LOGS=BASE/'logs'
LOGS.mkdir(exist_ok=True)
env=os.environ.copy()
for key in ('VIRTUAL_ENV','PYTHONHOME','UV_PROJECT_ENVIRONMENT','MODEL_API_KEY','SPARK_CREDIT_READY','HF_TOKEN','HUGGING_FACE_HUB_TOKEN','HUGGINGFACE_TOKEN','OPENAI_API_KEY','ANTHROPIC_API_KEY','SPARK_API_KEY'):
    env.pop(key,None)
env.update(PYTHONPATH=str(BASE/'guard'),UV_CACHE_DIR=str(BASE/'uv-cache-full'),HF_HOME=str(BASE/'empty-hf'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',MKL_NUM_THREADS='1',RAYON_NUM_THREADS='1',TOKENIZERS_PARALLELISM='false')
state={'tree':str(TREE),'estimate':{'cores':1,'peak_ram_gb':2,'wall_s':90,'gpu':False},'jobs':[],'completed':False}
def save(): REPORT.write_text(json.dumps(state,indent=2)+'\n')
def limits(): resource.setrlimit(resource.RLIMIT_CPU,(180,185))
def run(label,args):
    before=resource.getrusage(resource.RUSAGE_CHILDREN); began=time.monotonic()
    row={'label':label,'command':args,'cwd':str(TREE),'reaped':False,'owned_pids':[]}
    state['jobs'].append(row);save()
    with (LOGS/f'{label}.stdout').open('w') as out,(LOGS/f'{label}.stderr').open('w') as err:
        env['FRESH_CHILD_RECORD']=str(LOGS/f'{label}.owned.json')
        p=subprocess.Popen(args,cwd=TREE,env=env,stdout=out,stderr=err,preexec_fn=limits)
        row.update(pid=p.pid,owned_pids=[p.pid]);save()
        peak=None; timedout=False
        try:code=p.wait(timeout=240)
        except subprocess.TimeoutExpired:
            timedout=True
            owned_file=LOGS/f'{label}.owned.json'
            if owned_file.exists():
                owned=json.loads(owned_file.read_text())['pid']
                row['owned_pids'].append(owned);save()
                try:os.kill(owned,15)
                except ProcessLookupError:pass
            p.terminate()
            try:code=p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill();code=p.wait()
        owned_file=LOGS/f'{label}.owned.json'
        if owned_file.exists():row['owned_pids'].append(json.loads(owned_file.read_text())['pid'])
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    row.update(exit_code=code,reaped=True,wall_s=time.monotonic()-began,cpu_s=(after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime),sampled_child_tree_peak_rss_bytes=peak,waited_children_peak_rss_bytes=after.ru_maxrss,aborted=timedout,stdout=str(LOGS/f'{label}.stdout'),stderr=str(LOGS/f'{label}.stderr'))
    save();print(label,code,round(row['wall_s'],3),flush=True)

FILES=['tests/model/test_io.py','tests/model/test_instance.py','tests/model/test_geometry.py','tests/model/test_fixtures.py','tests/actions/test_occupancy.py','tests/rules/test_imdg_class_table.py','tests/vessel/test_tanks_geometry.py','tests/llm/test_mask_port_cache.py','app/tests/test_identity.py']
COMMON=['uv','run','--offline','--no-sync','python',str(BASE/'test-exec.py'),'-m','not slow and not gpu','-p','no:cacheprovider']
run('portable-green-subset',COMMON+FILES+['-q','--basetemp',str(BASE/'tmp-tests-green'),'--junitxml',str(REPORT.with_name('fresh-clone-tests-green.xml'))])
state['completed']=True;save()
