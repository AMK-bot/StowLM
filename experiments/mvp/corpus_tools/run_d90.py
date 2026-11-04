"""D90 detached amendment, safe inventory handoff, then three-ship generation."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time

import build
import campaign
import run_d84 as queue
from small_batch import completed

TOOLS = Path(__file__).resolve().parent
queue.JOB_NAME = 'mvp-corpus/d90'
queue.PREFIX = f'| {queue.JOB_NAME}-'
queue.LOG_PREFIX = 'd90'
queue.RUNTIME = TOOLS/'d90-runtime.json'


def main():
    if (TOOLS/'STOP-D126.json').exists():
        print('D126 corpus frozen; old-epoch supervisor restart disabled',flush=True)
        raise SystemExit(75)
    os.chdir(build.ROOT)
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[key] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    state = dict(pid=os.getpid(), decision='D90', started_at_utc=datetime.now(timezone.utc).isoformat(),
                 requested_nice=10, max_cores=4)
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    except OSError as error:
        state['nice_error'] = str(error)
    state['effective_nice'] = os.getpriority(os.PRIO_PROCESS, 0)
    py = sys.executable
    try:
        queue.run(state, 'evaluation', [py, str(TOOLS/'amend_v21.py'), '--execute', '--workers', '1'],
                  1, '<=45min; measured1700study generation34.42s/draw*6 + boundedfeaturesearch + priorrootQA6.23min')
        state['evaluation_report'] = json.loads((TOOLS/'EVAL-V2-REPORT.json').read_text())
        # The old supervisor observes d90-hold.json before starting its next
        # wave. Never change inventories while its admitted writer is active.
        while True:
            old = json.loads((TOOLS/'d84-runtime.json').read_text())
            if old.get('phase') in {'complete', 'failed'}:
                break
            state.update(phase='waiting_old_wave', old_supervisor_pid=old.get('pid'))
            build.atomic_json(queue.RUNTIME, state)
            time.sleep(10)
        queue.run(state, 'rebalance', [py, str(TOOLS/'rebalance_v21.py'), '--execute'],
                  1, '<=3min; inventory-only, measured data hash scans and tiny prefix retention; no generation')
        queue.run(state, 'inventory-freeze', [py, str(TOOLS/'freeze.py'), '--partial'],
                  1, '<=8min; priorrootfreeze3min at~1000artifacts')
        queue.run(state, 'inventory-verify', [py, str(TOOLS/'freeze.py'), '--verify'],
                  1, '<=8min; priorrootverify3.2min at~1000artifacts')
        hold = TOOLS/'d90-hold.json'
        hold.unlink(missing_ok=True)
        observed = []
        old_log = TOOLS/'d84-small-0000.log'
        if old_log.exists():
            for line in old_log.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('status') == 'generated':
                    observed.append(row['seconds'])
        cold_mean = statistics.mean(observed) if observed else 22
        new_test = [json.loads(p.read_text())['elapsed_generation_s']
                    for p in (build.CORPUS/'test/small/feeder_1700').rglob('*.audit.json')]
        # APPROXIMATION: source/import overhead measured on the existing fleet
        # is applied to the new ship's measured test generation time.
        old_test = [json.loads(p.read_text())['elapsed_generation_s']
                    for v in ('feeder_500','feeder_800')
                    for p in (build.CORPUS/'test/small'/v).rglob('*.audit.json')]
        overhead = max(0, cold_mean-statistics.mean(old_test))
        per_draw = .7*cold_mean + .3*(statistics.mean(new_test)+overhead)
        wave = 0
        while True:
            specs = [s for s in campaign.jobs(build.CORPUS) if s['rung']=='small']
            pending = [s for s in specs if not completed(s, build.CORPUS)]
            state.update(small_requested=len(specs), small_completed=len(specs)-len(pending),
                small_train_completed=sum(s['split']=='train' and completed(s, build.CORPUS) for s in specs),
                small_val_completed=sum(s['split']=='val' and completed(s, build.CORPUS) for s in specs),
                measured_prior_cold_mean_s=cold_mean, measured_1700_test_mean_s=statistics.mean(new_test),
                estimated_current_draw_s=per_draw)
            build.atomic_json(queue.RUNTIME, state)
            if not pending:
                break
            estimate = f'~{len(pending)*per_draw/4/3600:.2f}h remaining at4cores; measuredcold{cold_mean:.2f}s,new1700test{statistics.mean(new_test):.2f}s; next32only'
            queue.run(state, f'small-{wave:04d}', [py,str(TOOLS/'small_batch.py'),'--workers','{cores}','--limit','32'],
                      None, estimate)
            wave += 1
        queue.run(state,'final-freeze',[py,str(TOOLS/'freeze.py'),'--partial'],1,
                  '<=15min; scaled prior3min/1000artifacts to~3700')
        queue.run(state,'final-verify',[py,str(TOOLS/'freeze.py'),'--verify'],1,
                  '<=15min; scaled prior3.2min/1000artifacts to~3700')
        state.update(phase='complete',ended_at_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        state.update(phase='failed',error=str(error),ended_at_utc=datetime.now(timezone.utc).isoformat())
        build.atomic_json(queue.RUNTIME,state)
        raise
    build.atomic_json(queue.RUNTIME,state)


if __name__=='__main__':
    main()
