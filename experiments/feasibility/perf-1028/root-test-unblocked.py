"""Bounded CPU verification; generated integration is an explicit opt-in.

The initial historical run used a subprocess and omitted VECLIB. It remains
recorded separately in work-history.md. Future runs stay in this interpreter,
record their PID, set all three thread limits before importing numerical code,
and use a cooperative Python alarm. An interrupted run never passes the gate.
"""
from __future__ import annotations
import os
for variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
    os.environ[variable]='1'
import argparse
import json
from pathlib import Path
import signal
import time
import xml.etree.ElementTree as ET


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--include-integration',action='store_true')
    parser.add_argument('--seconds',type=int,default=300)
    args=parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    import pytest
    from bench import pins
    here=Path(__file__).resolve().parent
    stem='tests-unblocked-bounded'
    before=pins()
    command=['-q','-o','addopts=--import-mode=importlib -m "not slow and not gpu and not net"',
             '-o','faulthandler_timeout=60','tests/feasibility','tests/llm/test_mask.py',
             'tests/llm/test_mask_mlx.py',
             '--ignore=tests/feasibility/test_d49_integration.py',
             '--ignore=tests/feasibility/test_performance_equivalence.py',
             '--junitxml='+str(here/(stem+'.xml'))]
    if args.include_integration:
        command.append('tests/llm/test_mask_integration.py')
    record=dict(pid=os.getpid(),command=['pytest',*command],seconds=args.seconds,
                includes_generated_integration=args.include_integration,status='running',sources=before)
    destination=here/(stem+'.json')
    destination.write_text(json.dumps(record,indent=2)+'\n')
    def deadline(signum,frame):
        record['deadline_reached']=True
        raise KeyboardInterrupt('CPU verification reached its recorded deadline')
    previous=signal.signal(signal.SIGALRM,deadline)
    signal.alarm(args.seconds)
    started=time.perf_counter()
    try:
        result=int(pytest.main(command))
    except KeyboardInterrupt:
        result=2
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM,previous)
    after=pins()
    record.update(status='passed' if result==0 else 'incomplete-or-failed',exit_code=result,
                  wall_s=time.perf_counter()-started,sources_after=after,
                  changed_sources={k:v for k,v in after.items() if before.get(k)!=v})
    if (here/(stem+'.xml')).exists():
        record['junit']=[node.attrib for node in ET.parse(here/(stem+'.xml')).getroot().iter('testsuite')]
    destination.write_text(json.dumps(record,indent=2)+'\n')
    return result


if __name__=='__main__':
    raise SystemExit(main())
