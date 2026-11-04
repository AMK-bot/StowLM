"""Detached D84 queue: eval freeze first, then admitted train-first shards.

Only this job's ledger lines are changed. All subprocess PIDs and estimates are
recorded. A full ledger or load above16 queues work instead of overcommitting.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import build

TOOLS = Path(__file__).resolve().parent
LEDGER = build.ROOT / 'experiments/fleet/ledger.md'
HISTORY = build.ROOT / 'experiments/fleet/history.md'
JOB_NAME = 'mvp-corpus/d84'
PREFIX = f'| {JOB_NAME}-'
LOG_PREFIX = 'd84'
RUNTIME = TOOLS / 'd84-runtime.json'


def ledger_cores(content):
    total = 0.0
    for line in content.splitlines():
        cells = [c.strip() for c in line.split('|')]
        if len(cells) > 5 and cells[1] and not line.startswith(PREFIX):
            try:
                total += float(cells[3])
            except ValueError:
                continue
    return total


def update_owned_line(line=None):
    # Preserve every other executive's bytes, including leader annotations.
    for attempt in range(100):
        before = LEDGER.read_text()
        after = ''.join(row for row in before.splitlines(keepends=True) if not row.startswith(PREFIX))
        if line:
            after = after.rstrip() + '\n' + line + '\n'
        if LEDGER.read_text() != before:
            continue
        LEDGER.write_text(after)
        return
    raise RuntimeError('ledger changed repeatedly; no job admitted')


def admit(state, phase, cores, estimate):
    while True:
        content = LEDGER.read_text()
        other = ledger_cores(content)
        load = os.getloadavg()[0]
        capacity_path = TOOLS/'capacity.json'
        cap = int(json.loads(capacity_path.read_text())['max_workers']) if capacity_path.exists() else 4
        chosen = cores if cores is not None else min(cap, max(0, int(16-other)))
        state.update(phase=f'queued_{phase}', other_reserved_cores=other, load_1m=load,
                     requested_cores=cores or 4, estimate=estimate)
        build.atomic_json(RUNTIME, state)
        if chosen and other + chosen <= 16 and load <= 16:
            now = datetime.now(timezone.utc).isoformat()
            update_owned_line(f'| {JOB_NAME}-{phase} | 5.3 | {chosen} | {chosen*2} | no | {estimate}; native1; supervisor{os.getpid()}; resume per audit; nice attempted | {now} |')
            state.update(phase=phase, admission_at_utc=now, admitted_cores=chosen)
            build.atomic_json(RUNTIME, state)
            return chosen
        time.sleep(15)


def run(state, phase, argv, cores, estimate):
    cores = admit(state, phase, cores, estimate)
    argv = [str(cores) if arg == '{cores}' else arg for arg in argv]
    start = time.monotonic()
    log = TOOLS / f'{LOG_PREFIX}-{phase}.log'
    for attempt in range(20 if phase == 'evaluation' else 1):
        with log.open('a') as output:
            offset = output.tell()
            child = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT)
            state.setdefault('child_pids', []).append(child.pid)
            state.update(child_pid=child.pid, command=argv, log=str(log), attempt=attempt)
            build.atomic_json(RUNTIME, state)
            code = child.wait()
        tail = log.read_text()[offset:]
        if code == 0 or not any(message in tail for message in
            ('source epoch changed', 'source changed during generation', 'source changed during showcase generation')):
            break
    elapsed = time.monotonic()-start
    with HISTORY.open('a') as output:
        output.write(f'\n- {JOB_NAME}-{phase},5.3,{datetime.now(timezone.utc).isoformat()}: estimate {cores}CPU/{cores*2}GB/noGPU/{estimate}; actual wall{elapsed:.3f}s,exit{code},childPID{child.pid} reaped; RSS unmeasured. Evidence {log.relative_to(build.ROOT)}.\n')
    update_owned_line()
    state.setdefault('results', {})[phase] = dict(exit_code=code, elapsed_wall_s=elapsed)
    build.atomic_json(RUNTIME, state)
    if code:
        raise RuntimeError(f'{phase} exited{code}; resume with same command, inspect {log}')


def evaluation_report():
    root = build.CORPUS
    manifest = json.loads((root / 'manifest.json').read_text())
    hashes = {}
    for group in ('test', 'showcase'):
        for audit in (root / group).rglob('*.audit.json'):
            value = json.loads(audit.read_text())
            if value['config'].get('rung', value['config'].get('size')) == 'small':
                path = audit.with_name(audit.name.replace('.audit.json', '.json'))
                hashes[str(path.relative_to(root))] = build.sha256(path)
    report = dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(), hashes=hashes,
                  active_counts=manifest['active_counts'], pending_release_slots=manifest.get('pending_release_slots'),
                  hash_lists={g: dict(path=str(root/g/'SHA256SUMS'),
                      sha256=build.sha256(root/g/'SHA256SUMS')) for g in ('test', 'showcase')})
    build.atomic_json(TOOLS / 'EVAL-V2-REPORT.json', report)
    return report


def main():
    if (TOOLS/'STOP-D126.json').exists():
        print('D126 corpus frozen; old-epoch supervisor restart disabled',flush=True)
        raise SystemExit(75)
    os.chdir(build.ROOT)
    for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[name] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    state = dict(pid=os.getpid(), started_at_utc=datetime.now(timezone.utc).isoformat(), requested_nice=10)
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    except OSError as error:
        state['nice_error'] = str(error)
    state['effective_nice'] = os.getpriority(os.PRIO_PROCESS, 0)
    py = sys.executable
    try:
        # Study measured18.5–20.3s generation/draw. Twelve new draws ~4min;
        # root freeze+verify measured6.23min on918 tiny; reserve15min total.
        run(state, 'evaluation', [py, str(TOOLS/'regenerate.py'), '--ship-settings',
            str(TOOLS/'ship-settings-d84.json'), '--workers', '1', '--max-showcase-seeds', '100',
            '--execute', '--evaluation-only'],
            1, '<=35min; measured test~12s/draw; feature search10draws~86s scaled100/ship + prior rootQA6.23min')
        state['evaluation_report'] = evaluation_report()
        build.atomic_json(RUNTIME, state)
        # Refine the bulk estimate from our actual new evaluation draws.
        durations = [json.loads(p.read_text())['elapsed_generation_s']
                     for p in (build.CORPUS/'test/small').rglob('*.audit.json')]
        mean = sum(durations)/len(durations)
        estimate = f'~{2770*(mean+2)/4/3600:.2f}h; pilot mean{mean:.3f}s+2s startup/draw,2770/4;8GB bound'
        import campaign
        from small_batch import completed
        specs = [s for s in campaign.jobs(build.CORPUS) if s['rung'] == 'small']
        wave = 0
        while True:
            remaining = [s for s in specs if not completed(s, build.CORPUS)]
            state.update(small_requested=len(specs), small_completed=len(specs)-len(remaining),
                         small_train_completed=sum(s['split']=='train' and completed(s, build.CORPUS) for s in specs),
                         small_val_completed=sum(s['split']=='val' and completed(s, build.CORPUS) for s in specs))
            build.atomic_json(RUNTIME, state)
            if not remaining:
                break
            # Short resumable waves let gold keep its reservations; consume up
            # to four actually free cores, then re-admit before the next wave.
            run(state, f'small-{wave:04d}', [py, str(TOOLS/'small_batch.py'), '--workers', '{cores}',
                '--limit', '32'], None, estimate + '; next32only, re-admit each wave')
            wave += 1
        run(state, 'final-qa', [py, str(TOOLS/'freeze.py'), '--partial'], 1,
            '<=15min scaled prior1030artifacts3min freeze to~3710')
        run(state, 'verify', [py, str(TOOLS/'freeze.py'), '--verify'], 1,
            '<=15min scaled prior1030artifacts3.2min verify to~3710')
        state.update(phase='complete', ended_at_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        state.update(phase='failed', error=str(error))
        build.atomic_json(RUNTIME, state)
        raise
    build.atomic_json(RUNTIME, state)


if __name__ == '__main__':
    main()
