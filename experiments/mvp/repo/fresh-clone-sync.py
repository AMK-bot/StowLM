"""Populate a private cache by plain local copy, then install offline."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import time

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--source-cache', type=Path, default=HERE.parents[2] / '.uv-cache')
parser.add_argument('--cache-name', default='uv-cache')
parser.add_argument('--record', default='fresh-clone-sync.json')
args = parser.parse_args()
manifest = json.loads((HERE / 'fresh-clone-copy.json').read_text())
scratch = Path(manifest['scratch'])
tree = Path(manifest['tree'])
cache = scratch / args.cache_name
assert not cache.exists(), 'Preserve existing cache evidence; choose a new cache-name.'
record = dict(started_utc=datetime.now(timezone.utc).isoformat(), tree=str(tree),
    cache=str(cache), estimate=dict(cores=1, peak_ram_gb=1, wall_s=120, gpu=False),
    cache_source=str(args.source_cache), jobs=[], controller_pid=os.getpid())
output = HERE / args.record
assert not output.exists(), 'Preserve existing sync evidence; choose a new record.'


def save():
    output.write_text(json.dumps(record, indent=2)+'\n')


def run(label, command, env=None, timeout=180):
    row = dict(label=label, command=command, cwd=str(tree), reaped=False)
    record['jobs'].append(row)
    save()
    began = time.monotonic()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    with (scratch / 'logs' / f'{label}.log').open('w') as log:
        process = subprocess.Popen(command, cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT)
        row['pid'] = process.pid
        save()
        try:
            row['exit_code'] = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            row.update(exit_code=process.returncode, timeout=True)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    row.update(reaped=True, wall_s=time.monotonic()-began,
               cpu_s=after.ru_utime+after.ru_stime-usage.ru_utime-usage.ru_stime,
               child_tree_peak_rss_bytes=after.ru_maxrss)
    save()
    return row['exit_code']


save()
# macOS APFS clone-on-write: independent files, no shared mutable hardlinks.
code = run('cache-copy', ['/bin/cp', '-cR', record['cache_source'], str(cache)])
assert code == 0, 'Private cache copy failed; see recorded log, no network fallback.'
environment = dict(os.environ, UV_CACHE_DIR=str(cache), UV_OFFLINE='1',
    UV_PYTHON_DOWNLOADS='never', UV_CONCURRENT_INSTALLS='1', UV_CONCURRENT_BUILDS='1',
    UV_CONCURRENT_DOWNLOADS='1', UV_LINK_MODE='copy', RAYON_NUM_THREADS='1',
    OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
    VECLIB_MAXIMUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
run('uv-sync-offline', ['uv', 'sync', '--offline'], env=environment)
record.update(completed=True, ended_utc=datetime.now(timezone.utc).isoformat())
save()
print(json.dumps(dict(tree=str(tree), cache=str(cache), jobs=record['jobs'])))
