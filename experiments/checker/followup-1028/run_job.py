"""Single-thread local verification telemetry and owned compute-ledger cleanup."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[3]


def hashes():
    paths = {path for directory in ('src/stowage', 'params', 'tests/checker',
        'tests/linear', 'tests/rules', 'tests/vessel')
        for path in (ROOT/directory).rglob('*')
        if path.is_file() and path.suffix in {'.py', '.toml', '.json'}}
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


parser = argparse.ArgumentParser()
parser.add_argument('--name', required=True)
parser.add_argument('--estimate', required=True)
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ['--'] else args.command
here = Path(__file__).resolve().parent
prefix = here/args.name
before = hashes()
started = datetime.now(timezone.utc).isoformat()
clock = perf_counter()
with prefix.with_suffix('.log').open('w') as stream:
    child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
    prefix.with_suffix('.running.json').write_text(json.dumps(dict(pid=child.pid,
        started=started, command=command)))
    code = child.wait()
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
after = hashes()
row = dict(name=args.name, command=command, estimate=args.estimate, started=started,
    exit_code=code, wall_seconds=perf_counter()-clock,
    cpu_seconds=usage.ru_utime+usage.ru_stime,
    peak_rss_gb=usage.ru_maxrss/(1e9 if sys.platform=='darwin' else 1e6),
    hashes_before=before, hashes_after=after,
    changed=[path for path in before.keys()|after.keys() if before.get(path)!=after.get(path)])
prefix.with_suffix('.run.json').write_text(json.dumps(row, indent=2)+'\n')
ledger = ROOT/'experiments/fleet/ledger.md'
owned = f'| w2-checker-followup/{args.name} |'
ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True)
                          if not line.startswith(owned)))
with (ROOT/'experiments/fleet/history.md').open('a') as stream:
    stream.write(f'\n{owned} followup-1028 | 1CPU/noGPU | estimate {args.estimate}; actual {row["wall_seconds"]:.3f}s | exit {code}; peak RSS {row["peak_rss_gb"]:.3f}GB; CPU {row["cpu_seconds"]:.3f}s |\n')
print(json.dumps({key:value for key,value in row.items() if not key.startswith('hashes_')}))
raise SystemExit(code)
