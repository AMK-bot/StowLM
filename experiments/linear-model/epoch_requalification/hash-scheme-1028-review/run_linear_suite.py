"""Single-process default-marker verification with owned child accounting."""
from datetime import datetime,timezone
import json,os
from pathlib import Path
import subprocess,sys,time
HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'pyproject.toml').exists())
command=[sys.executable,'-m','pytest','tests/linear','-q','-o','junit_family=legacy',
         '--junitxml',str(HERE/'linear-final.xml')]
began=time.monotonic();record=dict(command=command,start_utc=datetime.now(timezone.utc).isoformat(),
    cores=1,gpu=False,blas_threads=1,purpose='default linear test gate; not epoch qualification')
with (HERE/'linear-final.log').open('w') as log:
    process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    record['owned_pid']=process.pid
    (HERE/'linear-final.execution.json').write_text(json.dumps(record,indent=2)+'\n')
    _,status,usage=os.wait4(process.pid,0)
    code=os.waitstatus_to_exitcode(status);process.returncode=code
record.update(exit_code=code,wall_seconds=time.monotonic()-began,cpu_seconds=usage.ru_utime+usage.ru_stime,
    max_rss_bytes=usage.ru_maxrss,finished_utc=datetime.now(timezone.utc).isoformat())
(HERE/'linear-final.execution.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record));raise SystemExit(code)
