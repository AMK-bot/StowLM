"""CPU-idle D113 watcher; reads only ExIt-owned outputs, never corpus/test inputs."""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import time

from llm.exit.progress import write_generation_report


ROOT=Path(__file__).resolve().parents[3]


def emit(event,**fields):
    print(json.dumps(dict(event=event,utc=datetime.now(timezone.utc).isoformat(),**fields)),flush=True)


def snapshot(out):
    controller=out/'deployment-status.json'
    state=json.loads(controller.read_text()) if controller.exists() else {}
    report=write_generation_report(out)
    return state,report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'experiments/mvp/exit/deployment')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--poll-seconds',type=float,default=20.)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    with (out/'.gen0-watcher.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        (out/'gen0-watcher.pid').write_text(str(os.getpid())+'\n')
        previous=None;last_heartbeat=0.;start_reported=False
        while True:
            state,report=snapshot(out)
            phases=tuple(p['name'] for p in report['active_phases'])
            current=(state.get('state'),report['status'],phases,report.get('counters',{}).get('dpo_stage'))
            if current!=previous or time.monotonic()-last_heartbeat>=60:
                emit('status',controller=state.get('state'),generation=report['status'],
                    elapsed_s=report['elapsed_s'],active_phases=report['active_phases'],
                    counters=report.get('counters',{}))
                previous=current;last_heartbeat=time.monotonic()
            if report['started_at'] and not start_reported:
                emit('generation0_started',started_at=report['started_at'],marker=str(out/'STARTED'))
                start_reported=True
            if report['over_120_minutes'] and not (out/'generation0-OVERRUN').exists():
                (out/'generation0-OVERRUN').write_text(datetime.now(timezone.utc).isoformat()+'\n')
                emit('generation0_over120',report=report)
            if (out/'gen-000'/'summary.json').exists():
                if report['generation_1_sizing'] is not None or (out/'terminal.json').exists():
                    emit('generation0_finished',report=report)
                    return 0
            if state.get('state') in ('failed','smoke_not_passed','expired_without_launch','stopped_for_fallback'):
                emit('attention_required',report=report,controller=state)
                return 1
            if args.once:return 0
            time.sleep(max(.1,args.poll_seconds))


if __name__=='__main__':raise SystemExit(main())
