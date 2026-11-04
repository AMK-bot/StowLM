"""Scoped single-job accounting; never discovers or signals foreign processes."""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone

out = Path(sys.argv[1])
command = sys.argv[2:]
start = time.monotonic()
record = {'command': command, 'wrapper_pid': os.getpid(), 'start': datetime.now(timezone.utc).isoformat()}
with out.with_suffix('.log').open('w') as stream:
    process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
    record['child_pid'] = process.pid
    out.with_suffix('.execution.json').write_text(json.dumps(record, indent=2)+'\n')
    status = process.wait()
record.update(exit_code=status, elapsed_s=time.monotonic()-start,
              peak_child_rss_mib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/1024**2)
out.with_suffix('.execution.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record))
sys.exit(status)
