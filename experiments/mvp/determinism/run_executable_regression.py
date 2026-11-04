"""Run admitted before/after regression serially, preserving intentional failures."""
import json,os,resource,subprocess,sys,time,hashlib
from datetime import datetime,timezone
from pathlib import Path
import xml.etree.ElementTree as ET
H=Path(__file__).resolve().parent;D=H/'regression';D.mkdir(exist_ok=True);out=D/'controller.json';assert not out.exists();files=['patches/test_archive_preflight_subprocess.py','patches/archive_asset_closure.py','patches/live_admission.py','replay_worker_asset_ab.py','code_audit.py'];frozen={name:hashlib.sha256((H/name).read_bytes()).hexdigest()for name in files}
def save(x):out.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')
state=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),completed=False,source_sha256=frozen,jobs=[],admission='Root admitted3beforeFAIL+3afterPASS freshhashseed subprocess regression after wholechecker naturalexit. Serial1core/50%duty/native1; each physical child gates Machbusy+1<=15/free>=15%.');save(state);start=time.perf_counter()
for mode,expected in(('before',1),('after',0)):
 assert all(hashlib.sha256((H/name).read_bytes()).hexdigest()==value for name,value in frozen.items())
 xml=D/f'{mode}.xml';record=dict(mode=mode,expected_exit_code=expected,started_utc=datetime.now(timezone.utc).isoformat(),junit=str(xml));state['jobs'].append(record);save(state)
 command=[sys.executable,'-m','pytest','--import-mode=prepend',str(H/'patches/test_archive_preflight_subprocess.py'),'-q','--junitxml='+str(xml)];record['command']=command
 with(D/f'{mode}.log').open('w')as log:
  p=subprocess.Popen(command,cwd=H.parents[2],env={**os.environ,'D112_ARCHIVE_MODE':mode,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'},stdout=log,stderr=subprocess.STDOUT);record['pid']=p.pid;save(state);u0=resource.getrusage(resource.RUSAGE_CHILDREN);s=time.perf_counter();record['exit_code']=p.wait();record['reaped']=True
 u=resource.getrusage(resource.RUSAGE_CHILDREN);record.update(wall_s=time.perf_counter()-s,child_tree_cpu_s=u.ru_utime+u.ru_stime-u0.ru_utime-u0.ru_stime,child_tree_lifetime_peak_rss_bytes=u.ru_maxrss,ended_utc=datetime.now(timezone.utc).isoformat());suites=ET.parse(xml).getroot().findall('testsuite');record['counts']={key:sum(int(x.attrib[key])for x in suites)for key in('tests','failures','errors','skipped')};record['expected_outcome_matched']=record['exit_code']==expected and record['counts']['tests']==3 and record['counts']['errors']==0 and record['counts']['failures']==(3 if mode=='before'else 0);save(state);assert record['expected_outcome_matched'],record
state.update(completed=True,wall_s=time.perf_counter()-start,ended_utc=datetime.now(timezone.utc).isoformat(),source_after_same=all(hashlib.sha256((H/name).read_bytes()).hexdigest()==value for name,value in frozen.items()));save(state);print(json.dumps({key:state[key]for key in('pid','completed','wall_s','source_after_same')}))
