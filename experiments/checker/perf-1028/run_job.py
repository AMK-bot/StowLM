"""Record one admitted local job's wall, child CPU, RSS and exact command."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
from time import perf_counter
from benchmark import source_hashes


def test_input_hashes():
    root = Path(__file__).resolve().parents[3]
    here = Path(__file__).resolve().parent
    paths = {*root.joinpath('tests/checker').glob('*.py'),
             *root.joinpath('tests/vessel').glob('*.py'), root/'conftest.py',
             here/'corpus.py', here/'corpus-manifest.json'}
    paths.update(root/relative for relative in json.loads((here/'corpus-assets-audit.json').read_text())['sha256'])
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}

parser = argparse.ArgumentParser()
parser.add_argument('--name', required=True)
parser.add_argument('--estimate', required=True)
parser.add_argument('--prefix', required=True)
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ['--'] else args.command
prefix = Path(args.prefix)
start = perf_counter()
sources_before = source_hashes()
inputs_before = test_input_hashes()
with prefix.with_suffix('.log').open('w') as stream:
    process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
    prefix.with_suffix('.running.json').write_text(json.dumps(dict(pid=process.pid, command=command)))
    code = process.wait()
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
sources_after = source_hashes()
inputs_after = test_input_hashes()
row = dict(name=args.name, command=command, estimate=args.estimate, exit_code=code,
           environment={key: os.environ.get(key) for key in ('PYTHONPATH',
               'STOWAGE_PERF_SOURCE_ROOT', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS',
               'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS')},
           wall_seconds=perf_counter()-start, user_cpu_seconds=usage.ru_utime,
           system_cpu_seconds=usage.ru_stime,
           peak_rss_gb=usage.ru_maxrss/(1e9 if sys.platform=='darwin' else 1e6),
           source_sha256_before=sources_before, source_sha256_after=sources_after,
           test_inputs_sha256_before=inputs_before, test_inputs_sha256_after=inputs_after,
           test_inputs_stable=inputs_before == inputs_after,
           sources_stable=sources_before == sources_after)
prefix.with_suffix('.run.json').write_text(json.dumps(row, indent=2)+'\n')
print(json.dumps(row), flush=True)
root = Path(__file__).resolve().parents[3]
ledger = root/'experiments/fleet/ledger.md'
match = f'| w2-perf-check/{args.name} |'
ledger.write_text(''.join(line for line in ledger.read_text().splitlines(keepends=True)
                          if not line.startswith(match)))
with (root/'experiments/fleet/history.md').open('a') as stream:
    stream.write(f'\n| w2-perf-check/{args.name} | perf-1028 | 1 CPU/no GPU | estimate {args.estimate}; actual {row["wall_seconds"]:.3f}s | exit {code}; peak RSS {row["peak_rss_gb"]:.3f}GB; CPU {usage.ru_utime+usage.ru_stime:.3f}s |\n')
raise SystemExit(code)
