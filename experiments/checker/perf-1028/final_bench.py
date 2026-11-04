"""Serial final reference/optimized campaigns, then interleaved warm checks."""
from pathlib import Path
import os
import subprocess
import sys

here = Path(__file__).resolve().parent
prefix = os.environ.get('STOWAGE_PERF_BENCH_PREFIX', 'final')
for mode in ('reference', 'optimized'):
    subprocess.run([sys.executable, str(here/'benchmark.py'), '--mode', mode,
        '--runs', '5', '--repeats', '5', '--profile', '--output', str(here/f'{prefix}-{mode}.json')], check=True)
subprocess.run([sys.executable, str(here/'paired_warm.py')], check=True)
