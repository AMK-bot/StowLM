"""PID-bound final verification watcher; no numerical imports while waiting.

Launch only after the frozen controller has registered. Supply the exact
controller-process.json PID and started value. This watcher never signals it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from time import sleep, time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
TESTS = tuple(sorted(p.name for p in HERE.glob('test_*.py')))
BEGIN = '<!-- C6 VERIFIED RESULTS BEGIN -->'
END = '<!-- C6 VERIFIED RESULTS END -->'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(path)


def pid_absent(pid):
    if type(pid) is not int or pid <= 0:
        raise ValueError('Expected exact positive registered controller PID')
    try:
        os.kill(pid,0)
        return False
    except ProcessLookupError:
        return True
    except OSError as error:
        raise RuntimeError(f'Cannot establish absence of exact controller PID {pid}: {error}') from error


def identity(output, pid, started):
    row = json.loads((output/'controller-process.json').read_text())
    if (row.get('pid') != pid or not math.isclose(row.get('started',-1),started,rel_tol=0,abs_tol=1e-6)
        or row.get('policy',{}).get('phase') != 'frozen'):
        raise ValueError('Controller PID/start/phase changed; watcher will not follow another controller')
    selection = row.get('selection',[])
    jobs = {(r['instance_id'],r['arm']) for r in selection}
    if len(selection) != 64 or len(jobs) != 64:
        raise ValueError('Expected complete frozen64-job controller selection')
    return row


def completion_gate(output, pid, started):
    """Controller must report successful cleanup, not just leave64 row files."""
    controller = identity(output,pid,started)
    if not pid_absent(pid):
        return False,controller
    state = json.loads((output/'controller-state.json').read_text())
    complete = json.loads((output/'controller-complete.json').read_text())
    if (state.get('pid') != pid or state.get('interrupted') or state.get('error')
        or state.get('cleanup_errors') or state.get('completed_jobs') != 64 or state.get('pending_jobs') != 0
        or state.get('finished',-1) < started or complete.get('finished',-1) < started
        or (complete.get('jobs'),complete.get('maps'),complete.get('phase')) != (64,32,'frozen')):
        raise ValueError('Recorded frozen controller ended without complete successful64-job cleanup')
    return True,controller


def local_report():
    spec = importlib.util.spec_from_file_location('_c6_final_report',HERE/'report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_record():
    names = ('FREEZE.json','cohort-manifest.json','source-manifest.json',
             'controller.py','report.py','audit.py','finalize.py',*TESTS)
    return {name:sha(HERE/name) for name in names}


def bound_rows(output):
    """Only frozen own-input bindings; never inspect live original lab sources."""
    report = local_report()
    entries = report.load_manifest(phase='frozen')
    run = report.kernel()
    configs = report.configurations(entries,run.ARMS)
    rows, hashes = {}, {}
    for arm in run.ARMS:
        expected = {e['instance_id']+'.json' for e in entries}
        actual = {p.name for p in (output/arm).glob('*.json')}
        if actual != expected:
            raise ValueError(f'{arm}: final row set differs from expected frozen32')
        for entry in entries:
            row = report.read_row(output,entry,arm,configs[entry['instance_id'],arm])
            rows[entry['instance_id'],arm] = row
            path = output/arm/f"{entry['instance_id']}.json"
            hashes[str(path.relative_to(output))] = sha(path)
    return dict(bindings={a:run.bindings(a) for a in run.ARMS},row_sha256=hashes),entries,rows


def run_stage(output, name, argv, state):
    log = output/f'final-{name}.log'
    env = {**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    controls = ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','STOWAGE_IN_PORT_WORKERS')
    for key in controls:
        env[key] = '1'
    env.pop('C6_WORKER_DEADLINE',None)
    env.pop('C6_WORKER_STARTED',None)
    command = ['/usr/bin/nice','-n','5',*argv]
    with log.open('w') as stream:
        launched = time()
        child = subprocess.Popen(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        row = dict(stage=name,pid=child.pid,parent_pid=os.getpid(),started=launched,registered=time(),
            argv=argv,command=command,cwd=str(ROOT),environment={k:env[k] for k in (*controls,'PYTHONDONTWRITEBYTECODE')},
            log=str(log.relative_to(HERE)))
        state['stages'].append(row)
        write(output/f'final-{name}-process.json',row)
        write(output/'FINALIZE.json',state)
        try:
            returncode = child.wait()
        except BaseException:
            # Only the exact Popen child started and recorded by this watcher.
            if child.poll() is None:
                import signal
                child.send_signal(signal.SIGINT)
            row.update(returncode=child.wait(),finished=time(),interrupted=True)
            write(output/f'final-{name}-process.json',row)
            raise
    row.update(returncode=returncode,finished=time())
    write(output/f'final-{name}-process.json',row)
    write(output/'FINALIZE.json',state)
    if returncode:
        raise RuntimeError(f'{name} failed ({returncode}); inspect {log}')


def results_block(output, summary, audit, passed_tests):
    """Measured tables only; parent writes the separate under200-word verdict."""
    from frozen_reporting.completion import markdown
    lines = [BEGIN,'', '## Verified frozen results', '',
        f"Verified {datetime.now(timezone.utc).isoformat()}: all64 configured arm/map rows, unchanged source/input bindings, fresh native checks and physical graph audit; {passed_tests} configured regression tests passed. Results: `{output.relative_to(HERE)}`.",
        '', 'T5 denotes a full native-checker pass. Every relaxed completion has all loads aboard; partials are not completed. Development and nondevelopment tables remain separate. No lift means combine vessels, port counts or fill cells.', '']
    for group_name, group in summary['groups'].items():
        lines += [f'### {group_name}', '',markdown(group['by_arm']), '',
            '| Ports / fill | Arm | T5 passes / measured / expected |', '|---|---|---:|']
        for cell, arms in group['by_ports_fill_counts'].items():
            for arm, counts in arms.items():
                lines.append(f"| {cell} | {arm} | {counts['T5_passes']} / {counts['measured']} / {counts['expected_maps']} |")
    lines += ['', 'Paired T5 lift/restow/time means remain within vessel×ports×fill cells in `report.md` and `summary.json`; own-pass means are labeled separately. Every relaxed hard-rule occurrence and breach is retained in `per-map.csv` and saved native reports.',
        '',f"Fresh audit: {sum(v['checked_completed'] for v in audit['summary'].values())} selected completed plans rechecked, {sum(v['full_passes'] for v in audit['summary'].values())} full passes; no-result rows receive no pass credit. Timeout results retain only pre-cap concrete checked incumbents. Cleanup time is outside the censored600-second work clock.", '',END]
    return '\n'.join(lines)


def update_notes(block):
    path = HERE/'NOTES.md'
    text = path.read_text()
    if (BEGIN in text) != (END in text) or text.count(BEGIN)>1 or text.count(END)>1:
        raise ValueError('Malformed verified-results markers; refusing NOTES edit')
    if BEGIN in text:
        text = text[:text.index(BEGIN)]+block+text[text.index(END)+len(END):]
    else:
        text = text.rstrip()+'\n\n'+block+'\n'
    text = text.replace('Status: fixed cohort generated; development smoke and runtime verification in\nprogress. No frozen comparison is claimed yet.',
        'Status: the frozen64-arm/map comparison and fresh native-check audits are verified. Results are below.')
    text = text.replace('Pending development, freeze, all-map comparison, and fresh full-check audits.',
        'The frozen comparison and fresh full-check audits are complete; verified tables follow.')
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller-pid',type=int,required=True)
    parser.add_argument('--controller-started',type=float,required=True)
    parser.add_argument('--out',default='results/main')
    parser.add_argument('--poll-s',type=float,default=30.)
    parser.add_argument('--once',action='store_true',help='check gate once; exit2 if controller still active')
    args = parser.parse_args()
    if not 1 <= args.poll_s <= 60 or not math.isfinite(args.controller_started):
        parser.error('Poll1..60 seconds; finite exact controller start required')
    output = (HERE/args.out).resolve()
    output.relative_to(HERE)
    state = dict(pid=os.getpid(),started=time(),controller_pid=args.controller_pid,
        controller_started=args.controller_started,output=str(output.relative_to(HERE)),stages=[],ok=False)
    source_before = source_record()
    write(output/'finalizer-process.json',state)
    lock = None
    try:
        while True:
            ready,controller = completion_gate(output,args.controller_pid,args.controller_started)
            state.update(status='ready' if ready else 'waiting_for_exact_controller',last_poll=time())
            write(output/'FINALIZE.json',state)
            if ready:
                break
            if args.once:
                return 2
            sleep(args.poll_s)
        lock = (HERE/'controller.lock').open('a')
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not completion_gate(output,args.controller_pid,args.controller_started)[0]:
            raise ValueError('Controller absence gate changed while acquiring lock')
        if source_record() != source_before:
            raise ValueError('Frozen inputs or verification sources changed while waiting')
        before,entries,rows = bound_rows(output)
        expected = {(e['instance_id'],a) for e in entries for a in ('engine12','port_lp')}
        if {(r['instance_id'],r['arm']) for r in controller['selection']} != expected:
            raise ValueError('Controller selection differs from exact frozen cohort')
        state.update(status='verifying',sources=source_before,preflight=before)
        write(output/'FINALIZE.json',state)
        run_stage(output,'audit',[sys.executable,str(HERE/'audit.py'),'--out',args.out],state)
        audit = json.loads((output/'AUDIT.json').read_text())
        if not audit.get('ok') or audit.get('available_rows') != 64 or audit.get('missing_rows'):
            raise ValueError('Fresh strict64-row audit did not pass')
        run_stage(output,'tests',[sys.executable,'-m','pytest','-q','-p','no:cacheprovider',*[str(HERE/name) for name in TESTS]],state)
        matched = re.findall(r'\b(\d+) passed\b',(output/'final-tests.log').read_text())
        if not matched:
            raise ValueError('Successful pytest log lacks an explicit passed-test count')
        state['tests'] = dict(files=list(TESTS),passed=int(matched[-1]))
        run_stage(output,'report',[sys.executable,str(HERE/'report.py'),'--phase','frozen','--out',args.out],state)
        after,_,_ = bound_rows(output)
        if before != after or source_record() != source_before:
            raise ValueError('Bindings, inputs, reports or verifier sources changed during finalization')
        if not completion_gate(output,args.controller_pid,args.controller_started)[0]:
            raise ValueError('Expected controller identity/absence changed during finalization')
        summary = json.loads((output/'summary.json').read_text())
        if summary.get('completed_jobs') != 64 or summary.get('expected_jobs') != 64:
            raise ValueError('Final summary does not contain64 distinct configured rows')
        update_notes(results_block(output,summary,audit,state['tests']['passed']))
        state.update(ok=True,status='verified_notes_updated',finished=time(),postflight=after,
            notes_sha256=sha(HERE/'NOTES.md'),audit_sha256=sha(output/'AUDIT.json'),summary_sha256=sha(output/'summary.json'))
        write(output/'FINALIZE.json',state)
        lock.close()
        return 0
    except BaseException as error:
        state.update(status='failed',finished=time(),error=f'{type(error).__name__}: {error}')
        write(output/'FINALIZE.json',state)
        raise
    finally:
        if lock is not None:
            lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
