"""Record the explicitly admitted cheap dependency/helper tests."""
import hashlib,json,os,resource,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
import xml.etree.ElementTree as ET
H=Path(__file__).resolve().parent;out=H/'cheap-tests.json';assert not out.exists();xml=H/'cheap-tests.xml';tests=[H/'patches/test_archive_asset_closure.py',H/'patches/test_archive_root_marker.py'];command=[sys.executable,'-m','pytest','--import-mode=prepend',*(str(p)for p in tests),'-q','--junitxml='+str(xml)]
row=dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat(),command=command,completed=False,expected_exit_code=0,source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in tests},scope='Root-admitted cheap artifact/helper/root-marker tests; no fresh numerical worker.')
def save():out.write_text(json.dumps(row,sort_keys=True,indent=2)+'\n')
save();start=time.perf_counter()
with(H/'cheap-tests.log').open('w')as log:
 p=subprocess.Popen(command,cwd=H.parents[2],env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'},stdout=log,stderr=subprocess.STDOUT);row['child_pid']=p.pid;save();row['exit_code']=p.wait();row['reaped']=True
u=resource.getrusage(resource.RUSAGE_CHILDREN);suites=ET.parse(xml).getroot().findall('testsuite');row.update(completed=True,wall_s=time.perf_counter()-start,child_tree_cpu_s=u.ru_utime+u.ru_stime,child_lifetime_peak_rss_bytes=u.ru_maxrss,counts={k:sum(int(s.attrib[k])for s in suites)for k in('tests','failures','errors','skipped')},ended_utc=datetime.now(timezone.utc).isoformat(),source_after_same=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in row['source_sha256'].items()));save();assert row['exit_code']==0 and row['counts']['failures']==row['counts']['errors']==0;print(json.dumps(row))
