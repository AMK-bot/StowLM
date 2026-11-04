"""D137 restart queue: exact installed patch, 8.2 parent, smoke and normal admission."""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from llm.exit.resources import ledger_totals,probe
from llm.exit.runner import source_pins,write_json
from llm.exit.smoke_once import attempt_path
from llm.exit.review_gate import check_review_gate
from llm.exit.code_epoch import RESTART_EXIT_CODE,save_boundary

ROOT=next(path for path in Path(__file__).resolve().parents if (path/'pyproject.toml').is_file())
from state import validate_installed_manifest,frozen_launch_clock,DEFAULT_MANIFEST,initialize_schedule,prior_schedule_paths
_INSTALLED_MANIFEST=None
OUT=ROOT/'experiments/mvp/exit/deployment-d137'


def root_path(value):
    path=Path(value)
    return (path if path.is_absolute() else ROOT/path).resolve()


def completion_ready(parent,marker):
    """A checkpoint or another run's completion cannot release this parent.

    run_81.sh emits DONE.json after train exits successfully. TRAIN.DONE is
    also supported for the owner wrapper, only beside this adapter directory.
    """
    parent,marker=root_path(parent),root_path(marker)
    if not (parent/'adapters.safetensors').is_file():return False
    if marker.name=='TRAIN.DONE' and marker.parent==parent.parent:
        if marker.is_file():return True
        marker=marker.with_name('DONE.json')
    if not marker.is_file():return False
    try:
        record=json.loads(marker.read_text())
        return (isinstance(record,dict) and record.get('marker')=='8.1 SFT complete'
                and isinstance(record.get('adapter'),str)
                and root_path(record['adapter'])==parent)
    except (OSError,ValueError,TypeError):return False


def available_workers(ledger,live,maximum=6):
    cores,ram,gpu=ledger_totals(ledger)
    if gpu or live['memory_free_percent'] is None:
        return 0
    # D123: load tunes concurrency; only ledger/RAM capacity veto admission.
    proposed=max(2,min(6,math.floor(18-live['load_1m'])))
    workers=min(proposed,maximum,math.floor(16-cores-2),
                math.floor((112-ram-64)/2))
    while workers>0 and live['memory_free_percent'] < (64+2*workers)/128*100:
        workers-=1
    return workers if workers>=2 else 0


def smoke_capacity(cores,ram,gpu,live):
    """D123 smoke reserves one host core; observed load may reach 18."""
    return (not gpu and cores+1<=16 and ram+66<=112 and live['load_1m']<=18
            and (live['memory_free_percent'] or 0)>=66/128*100)


def launch(command,log,pid_path):
    validate_installed_manifest(_INSTALLED_MANIFEST)
    initialize_schedule(OUT,prior_schedule_paths(out=OUT))
    with log.open('a') as stream:
        process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    pid_path.write_text(str(process.pid)+'\n')
    return process


def pilot_reserved(ledger):
    """Even a stale completion marker cannot override the pilot owner's row."""
    return any(line.startswith('|') and len(parts:=line.strip('|').split('|'))>=7
               and parts[0].strip().startswith('fix-t8-sft/') for line in ledger.splitlines())


def gpu_lock_available():
    from llm.exit.resources import execution_lock
    try:
        with execution_lock():return True
    except RuntimeError:return False


def main():
    global OUT,_INSTALLED_MANIFEST
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=OUT,help='independent deployment output directory')
    parser.add_argument('--recover-prepared',type=Path,default=None,
                        help='D129 epoch-reverified prepared generation manifest')
    parser.add_argument('--parent',default='experiments/mvp/sft/stowlm-v2/adapter')
    parser.add_argument('--completion-marker',default='experiments/mvp/sft/stowlm-v2/DONE.json',
                        help='parent completion; sibling validated DONE.json is accepted for TRAIN.DONE')
    parser.add_argument('--not-before',default=None,help='optional extra time gate; D113 default is immediate')
    parser.add_argument('--gens',type=int,default=8)
    parser.add_argument('--batch-size','--B',type=int,default=8)
    parser.add_argument('--k',type=int,choices=range(6,9),default=6)
    parser.add_argument('--population',type=int,choices=(2,3),default=2)
    parser.add_argument('--maps',type=int,default=12)
    parser.add_argument('--steps',type=int,default=100)
    parser.add_argument('--cached-activations',action='store_true')
    parser.add_argument('--installed-manifest',type=Path,default=DEFAULT_MANIFEST)
    parser.add_argument('--max-workers',type=int,choices=(2,3,4),default=4)
    args=parser.parse_args()
    _INSTALLED_MANIFEST=root_path(args.installed_manifest)
    validate_installed_manifest(_INSTALLED_MANIFEST)
    OUT=root_path(args.out)
    if args.recover_prepared is not None:args.recover_prepared=root_path(args.recover_prepared)
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'.deployment.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        (OUT/'controller.pid').write_text(str(os.getpid())+'\n')
        parent=root_path(args.parent)
        completion=root_path(args.completion_marker)
        not_before=datetime.fromisoformat(args.not_before) if args.not_before else None
        expires=time.monotonic()+8*3600
        while time.monotonic()<expires:
            status=dict(utc=datetime.now(timezone.utc).isoformat(),not_before=args.not_before,
                        parent=str(parent),completion_marker=str(completion))
            if (OUT/'deployment.cancel').exists():
                write_json(OUT/'deployment-status.json',{**status,'state':'cancelled_before_launch'})
                return 0
            if (OUT/'terminal.json').exists():
                write_json(OUT/'deployment-status.json',{**status,'state':'stopped_for_fallback',
                    'terminal':json.loads((OUT/'terminal.json').read_text())})
                return 0
            try:
                installed=validate_installed_manifest(_INSTALLED_MANIFEST)
                initialize_schedule(OUT,prior_schedule_paths(out=OUT))
            except (OSError,ValueError,RuntimeError,TypeError) as error:
                write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_d137_installation',
                    'reason':str(error)})
                time.sleep(30);continue
            status['installed_manifest']=installed
            reviews=check_review_gate(root=ROOT)
            if not reviews.ready:
                write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_reviews',
                    'reasons':reviews.reasons})
                time.sleep(30);continue
            if (not_before is not None and datetime.now(timezone.utc)<not_before
                    or not completion_ready(parent,completion)):
                write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_parent_completion'})
                time.sleep(30);continue
            ledger=(ROOT/'experiments/fleet/ledger.md').read_text()
            if pilot_reserved(ledger) or not gpu_lock_available():
                write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_pilot_gpu_release'})
                time.sleep(30);continue
            if not (OUT/'READY').exists():
                write_json(OUT/'READY',dict(utc=datetime.now(timezone.utc).isoformat(),
                    state='parent_handoff_complete',parent=str(parent),
                    completion_marker=str(completion),generation_started=False))
            pins=source_pins()
            boundary_path=OUT/'epoch-boundary.json'
            if boundary_path.exists():
                boundary=json.loads(boundary_path.read_text())
                if boundary['current_pins']!=pins:
                    save_boundary(boundary_path,number=boundary['next_generation'],population=boundary['population'],
                        args=boundary['args'],loaded_pins=boundary['loaded_pins'],current_pins=pins)
            smoke=attempt_path(parent,pins)
            if not smoke.exists():
                # At a completed-generation boundary the resident process has
                # exited and released its lock. A fresh smoke imports new code.
                try:cores,ram,gpu=ledger_totals((ROOT/'experiments/fleet/ledger.md').read_text())
                except RuntimeError as error:
                    write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_numeric_ledger',
                        'reason':str(error)})
                    time.sleep(30);continue
                live=probe()
                if smoke_capacity(cores,ram,gpu,live):
                    smoke_out=ROOT/'experiments/mvp/exit/epoch-smoke'/smoke.stem
                    smoke_out.mkdir(parents=True,exist_ok=True)
                    smoke_process=launch([sys.executable,'-m','llm.exit.runner','--parent',str(parent),
                        '--gpu-smoke','--workers','1','--out',str(smoke_out)],
                        smoke_out/'smoke.log',smoke_out/'smoke.pid')
                    while smoke_process.poll() is None:
                        write_json(OUT/'deployment-status.json',{**status,'state':'between_generation_smoke',
                            'marker':str(smoke),'pid':smoke_process.pid})
                        time.sleep(30)
                else:
                    write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_smoke_capacity',
                        'marker':str(smoke),'live':live})
                time.sleep(30);continue
            if json.loads(smoke.read_text()).get('status')!='done':
                write_json(OUT/'deployment-status.json',{**status,'state':'smoke_not_passed','marker':str(smoke)})
                time.sleep(30);continue
            live=probe()
            try:workers=available_workers((ROOT/'experiments/fleet/ledger.md').read_text(),live,maximum=args.max_workers)
            except RuntimeError as error:
                write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_numeric_ledger',
                    'reason':str(error)})
                time.sleep(30);continue
            if not workers:
                write_json(OUT/'deployment-status.json',{**status,'state':'waiting_for_capacity','live':live})
                time.sleep(30);continue
            audit_clock=frozen_launch_clock(OUT)
            command=[sys.executable,'-m','llm.exit.runner','--parent',str(parent),'--out',str(OUT),
                '--gens',str(args.gens),'--maps',str(args.maps),'--k',str(args.k),
                '--population',str(args.population),'--steps',str(args.steps),
                '--workers',str(workers),'--dpo','--audit-every','2',
                '--audit-list',str(ROOT/'experiments/mvp/valsplit/audit_small30.txt'),
                '--audit-batch-size','4','--audit-clock',audit_clock,
                '--baseline-label','8.2','--gold-map-fraction','.5',
                '--teacher-planning-s','120','--ballast-s','120',
                '--target-generation-minutes','75','--no-early-pruning',
                '--stop-file',str(OUT/'deployment.cancel')]
            if args.batch_size is not None:command+=['--batch-size',str(args.batch_size)]
            if args.cached_activations:command+=['--cached-activations']
            if args.recover_prepared is not None:command+=['--recover-prepared',str(args.recover_prepared)]
            if boundary_path.exists():command+=['--resume-boundary',str(boundary_path)]
            write_json(OUT/'deployment-command.json',dict(command=command,workers=workers))
            (OUT/'admission.json').unlink(missing_ok=True)
            process=launch(command,OUT/'generation.log',OUT/'generation.pid')
            while process.poll() is None:
                completed=sorted(OUT.glob('gen-*/summary.json'))
                write_json(OUT/'deployment-status.json',{**status,'state':'running','pid':process.pid,
                    'workers':workers,'completed_generations':len(completed),
                    'latest_summary':str(completed[-1]) if completed else None})
                time.sleep(30)
            if process.returncode==RESTART_EXIT_CODE:
                write_json(OUT/'deployment-status.json',{**status,'state':'awaiting_new_code_smoke'})
                expires=time.monotonic()+8*3600
                continue
            if (OUT/'terminal.json').exists():
                write_json(OUT/'deployment-status.json',{**status,'state':'stopped_for_fallback',
                    'terminal':json.loads((OUT/'terminal.json').read_text())})
                return 0
            if (OUT/'admission.json').exists() and json.loads((OUT/'admission.json').read_text()).get('admitted'):
                write_json(OUT/'deployment-result.json',dict(returncode=process.returncode,
                    completed_generations=len(list(OUT.glob('gen-*/summary.json')))))
                write_json(OUT/'deployment-status.json',{**status,'state':'completed' if process.returncode==0 else 'failed',
                    'returncode':process.returncode})
                return process.returncode
            time.sleep(30)  # Capacity race before admission; no heavy work started.
        write_json(OUT/'deployment-status.json',dict(state='expired_without_launch'))
        return 1


if __name__=='__main__':raise SystemExit(main())
