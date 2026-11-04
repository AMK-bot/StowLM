"""Bounded, resumable one-core demo; canonical release has only nine tiny val cases.

Run with BLAS/OMP/VECLIB thread caps of one. Each invocation stops after
90 seconds, retaining completed per-instance checkpoints; repeat to completion.
"""
import json
import os
from pathlib import Path
import resource
import signal
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.harness import PlaceholderPlanner, run_evaluation

RUN_DIR = Path('experiments/eval-runs/val-placeholder-demo')


def stop_chunk(signum, frame):
    raise KeyboardInterrupt('90-second resumable chunk limit')


def main():
    signal.signal(signal.SIGALRM, stop_chunk)
    signal.alarm(90)
    start = perf_counter()
    print(json.dumps({'pid': os.getpid(), 'estimated_wall_s': 90,
                      'cores': 1, 'estimated_peak_ram_gb': 1, 'gpu': False}), flush=True)
    completed = False
    try:
        summary = run_evaluation(
            PlaceholderPlanner(), run_dir=RUN_DIR, split='val', rungs=['tiny', 'small'],
            limits={'tiny': 9, 'small': 10}, workers=1, time_limit_s=0.2,
        )
        completed = True
        print(json.dumps({'n': summary['n_instances'], 'passed': summary['n_passed'],
                          'fallback_rate': summary['fallback_rate']}), flush=True)
    except KeyboardInterrupt:
        print('Chunk stopped; completed checkpoints are resumable.', flush=True)
    finally:
        signal.alarm(0)
        metrics = {'completed': completed, 'wall_s': perf_counter() - start,
                   'peak_ram_gb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3,
                   'checkpoints': len(list((RUN_DIR / 'instances').glob('*.json')))}
        print(json.dumps(metrics), flush=True)
        with (RUN_DIR / 'compute.jsonl').open('a') as out:
            out.write(json.dumps(metrics) + '\n')


if __name__ == '__main__':
    main()
