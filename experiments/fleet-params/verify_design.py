"""One-thread scoped verification with an exact wall-time/result record."""
import datetime
import json
import os
from pathlib import Path
import resource
import subprocess
import time

here=Path(__file__).resolve().parent
started=datetime.datetime.now().astimezone().isoformat()
start=time.monotonic()
command=['uv','run','pytest','-q','tests/params','tests/vessel']
with (here/'design-pytest.log').open('w') as log:
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    (here/'design-verification-running.json').write_text(json.dumps(dict(pid=process.pid,started=started,command=command))+'\n')
    for line in process.stdout:
        print(line,end='',flush=True);log.write(line);log.flush()
    returncode=process.wait()
result=dict(command=command,started=started,finished=datetime.datetime.now().astimezone().isoformat(),returncode=returncode,wall_s=time.monotonic()-start,peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,thread_environment={k:os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS')})
(here/'design-verification.json').write_text(json.dumps(result,indent=2)+'\n')
(here/'design-verification-running.json').unlink()
print('VERIFICATION_RESULT',json.dumps(result),flush=True)
raise SystemExit(returncode)
