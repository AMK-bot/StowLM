"""Single-core regression verification with cooperative priority fallback."""
import os

for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
            'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'RAYON_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import hashlib
import json
from pathlib import Path
import resource
import time

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def snapshot():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ('src/llm/lookahead', 'tests/llm/lookahead')
            for path in sorted((ROOT / folder).glob('*.py'))}


class Throttle:
    def __init__(self):
        self.slept = 0.0

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        before = time.process_time()
        yield
        delay = max(0.0, time.process_time() - before)
        time.sleep(delay)
        self.slept += delay


if __name__ == '__main__':
    before = snapshot()
    start, cpu = time.perf_counter(), time.process_time()
    throttle = Throttle()
    args = ['tests/llm/lookahead', 'tests/llm/securing',
            'tests/llm/instructions/test_instructions_mask_filter.py', '-q',
            '--junitxml=' + str(HERE / 'tests.xml')]
    priority = []
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
        priority.append('setpriority succeeded')
    except OSError as exc:
        priority.append(str(exc))
    code = pytest.main(args, plugins=[throttle])
    after = snapshot()
    record = dict(pid=os.getpid(), pytest_args=args, exit_code=int(code),
                  wall_s=time.perf_counter()-start, cpu_s=time.process_time()-cpu,
                  cooperative_sleep_s=throttle.slept, priority=priority,
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  source_before=before, source_after=after, source_valid=before == after,
                  native_threads=1, gpu=False)
    (HERE / 'tests-meta.json').write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    raise SystemExit(code or (0 if before == after else 1))
