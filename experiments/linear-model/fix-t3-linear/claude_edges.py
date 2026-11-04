"""Three bounded single-thread workers for fresh deterministic edge evidence."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
if sys.argv[1:] not in ([], ['v2']):
    raise ValueError('only the original and explicit v2 edge campaigns are supported')
V2 = bool(sys.argv[1:])
SAMPLER = HERE.parent/'epoch_requalification'/('edge_grid_v2.py' if V2 else 'edge_grid.py')
PREPARED = HERE/'epoch-claude'
EPOCH = HERE/('epoch-claude-v2' if V2 else 'epoch-claude')


def run(ship):
    for phase in ('train','holdout'):
        # Ordinary independent reference-transfer observations must exist before
        # this sampler can publish a source-pinned numerical record.
        began = time.monotonic()
        while not (PREPARED/ship/f'{phase}-light.json').exists():
            if time.monotonic()-began > 1200:
                raise TimeoutError('ordinary reference evidence did not finish')
            time.sleep(5)
        prefix = HERE/f'claude-edge-{"v2-" if V2 else ""}{ship}-{phase}'
        command = [sys.executable,str(HERE/'d49_run.py'),str(prefix),sys.executable,
            str(SAMPLER),'--ship',ship,'--phase',phase,'--prepared',str(PREPARED),
            '--output',str(EPOCH)]
        print(json.dumps(dict(start=ship,phase=phase)),flush=True)
        process = subprocess.Popen(command)
        status = process.wait()
        if status:
            raise RuntimeError(f'{ship}/{phase}: edge sampler failed ({status})')


with ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(run,ship) for ship in ('feeder_500','feeder_1000','feeder_1700')]
    for future in as_completed(futures):
        future.result()
