"""Run one audit in-process and record wall time and macOS peak RSS."""
import json
from pathlib import Path
import resource
import runpy
import sys
import time

script, metrics, *arguments = sys.argv[1:]
sys.argv = [script, *arguments]
started = time.monotonic()
exit_code = 0
try:
    runpy.run_path(script, run_name="__main__")
except BaseException as error:
    exit_code = error.code if isinstance(error, SystemExit) and isinstance(error.code, int) else 1
    raise
finally:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    record = {"script": script, "arguments": arguments, "wall_s": time.monotonic()-started,
              "peak_rss_bytes": usage.ru_maxrss, "rss_platform": sys.platform,
              "user_s": usage.ru_utime, "system_s": usage.ru_stime, "exit_code": exit_code}
    Path(metrics).write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps({"execution": record}), flush=True)
