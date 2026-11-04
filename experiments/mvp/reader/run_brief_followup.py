"""D222: one durable controller, two shared slots for both cohorts and audits.

The original D220 runner/source/cohort are unchanged. A separate imported module
instance reuses its exact functions for the second cohort with another RUN path.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import time

from experiments.mvp.reader import run_brief as first

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN2 = HERE/'runs/brief2'
CONTROL = HERE/'runs/brief-followup'
PURPOSE = 'reader-d222-followup'
BUDGET = Decimal('1.90')  # Combined with original $0.082778, remains below $2.


def second_runner():
    spec = importlib.util.spec_from_file_location('brief2_frozen_runner', first.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RUN, module.PURPOSE = RUN2, PURPOSE
    return module


def freeze_second():
    if list((RUN2/'plans').glob('*.json')) or list((RUN2/'parser').glob('*.json')):
        second_runner().verify_sources()
        return
    first.verify_sources()
    base = json.loads((first.RUN/'implementation.json').read_text())
    names = list(base['sources']) + [str((HERE/name).relative_to(ROOT)) for name in
                                   ('brief_cohort2.py','run_brief_followup.py')]
    value = dict(sources={n:first.digest(ROOT/n) for n in sorted(set(names))},
                 cohort_sha256=first.digest(RUN2/'cohort.json'), standing=first.STANDING,
                 max_usd=str(BUDGET), purpose=PURPOSE, workers=2, native_workers=1,
                 created_utc=datetime.now(timezone.utc).isoformat(),
                 policy=base['policy'], first_cohort_sha256=base['cohort_sha256'],
                 scheduler='shared global two-worker round-robin; first and second coexist; no retries')
    path=RUN2/'implementation.json'
    if path.exists():
        prior=json.loads(path.read_text())
        for key in ('sources','cohort_sha256','first_cohort_sha256','purpose','max_usd'):
            if prior[key]!=value[key]:
                raise ValueError('Second freeze mismatch: '+key)
    else:
        first.write(path,value)


def prepare_second():
    from stowage.io import load_instance
    from experiments.mvp.reader.brief_planner import VoyageBrief, read_brief
    from experiments.mvp.reader.pipeline import SparkParser
    freeze_second()
    runner=second_runner()
    runner.verify_sources()
    parser=None
    for case in runner.cases():
        target=RUN2/'parser'/f"{case['case_id']}.json"
        if target.exists():
            continue
        if not case.get('sentence'):
            first.write(target,dict(case_id=case['case_id'],no_admissible_instruction=True,
                                    parse_wall_s=0,brief=None))
            continue
        if parser is None:
            parser=SparkParser(purpose=PURPOSE,allocation_usd=BUDGET)
        instance=load_instance(ROOT/case['input'])
        began=time.perf_counter()
        brief,_=read_brief(instance,VoyageBrief(first.STANDING,(case['sentence'],)),parser=parser)
        first.write(target,dict(case_id=case['case_id'],brief=brief,parse_wall_s=time.perf_counter()-began))
        print(json.dumps(dict(event='parsed_second',case_id=case['case_id'],
                              statuses=[s['status'] for s in brief['sentences']])),flush=True)
    first.write(RUN2/'ready.json',dict(cohort_sha256=first.digest(RUN2/'cohort.json'),
                                      parser_count=len(runner.cases()),created_utc=datetime.now(timezone.utc).isoformat()))


def recover_interrupted():
    """Preserve unreturned attempts as failures, never retry or remove claims."""
    first.verify_sources()
    recovered=[]
    for claim in sorted((first.RUN/'claims').glob('*.json')):
        target=first.RUN/'plans'/claim.name
        if target.exists():
            continue
        claimed=json.loads(claim.read_text())
        try:
            os.kill(claimed['pid'],0)
        except ProcessLookupError:
            pass
        except PermissionError:
            raise RuntimeError('Claim PID may still exist; will not recover: '+claim.stem)
        else:
            raise RuntimeError('Claim PID still exists; will not recover: '+claim.stem)
        parsed_path=first.RUN/'parser'/claim.name
        parsed=json.loads(parsed_path.read_text())
        first.write(target,dict(case_id=claim.stem,passed=False,repaired=False,
                    admission=claimed['admission'],planner_pid=claimed['pid'],
                    parser_sha256=first.digest(parsed_path),evidence=parsed['brief'],
                    error_type='InterruptedBeforeCheckpoint',interrupted=True,timing_unknown=True,
                    wall_s=0.,pipeline_wall_s=parsed['parse_wall_s'],parse_wall_s=parsed['parse_wall_s'],
                    timing_note='Actual planner wall time unavailable; zero is a storage sentinel, excluded from timing statistics',
                    recovered_utc=datetime.now(timezone.utc).isoformat()))
        recovered.append(claim.stem)
    if recovered:
        first.write(CONTROL/'interruption.json',dict(cases=recovered,previous_controller_pid=60347,
                   reason='Foreground execution session ended between turns; recorded PIDs no longer exist',
                   policy='No rerun, no repair, original cohort/parses/sources/other results unchanged'))
    return recovered


def _work(group,phase,case):
    # This optional fresh witness audit occupies this existing worker slot;
    # it never creates a third solver/checker worker.
    if (HERE/'runs/refusals/witness-request.json').exists():
        from experiments.mvp.reader.refusal_demo import audit_pending_witness
        audit_pending_witness()
    runner=first if group=='brief' else second_runner()
    return (runner.plan_one if phase=='plans' else runner.audit_one)(case)


def pending_task(group,active):
    runner=first if group=='brief' else second_runner()
    if group=='brief2' and not (RUN2/'ready.json').exists():
        return None
    cohort=runner.cases()
    phase='plans' if any(not (runner.RUN/'plans'/f"{c['case_id']}.json").exists() for c in cohort) else 'audits'
    for case in cohort:
        key=(group,phase,case['case_id'])
        if key not in active and not (runner.RUN/phase/f"{case['case_id']}.json").exists():
            return phase,case
    return None


def group_done(group):
    runner=first if group=='brief' else second_runner()
    if group=='brief2' and not (RUN2/'ready.json').exists():
        return False
    return all((runner.RUN/'audits'/f"{c['case_id']}.json").exists() for c in runner.cases())


def refresh():
    try:
        from experiments.mvp.reader.brief_report_combined import build
        return build()
    except ImportError:
        return None  # Report implementation can arrive before cohort2 is ready.


def run():
    CONTROL.mkdir(parents=True,exist_ok=True)
    # A kernel lock prevents a second scheduler from admitting duplicate workers.
    import fcntl
    with (CONTROL/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        first.verify_sources()
        recovered=recover_interrupted()
        first.write(CONTROL/'controller.json',dict(pid=os.getpid(),workers=2,native_workers=1,
                   started_utc=datetime.now(timezone.utc).isoformat(),recovered=recovered,
                   policy='global round-robin across cohorts, original per-cohort budgets and sources unchanged',
                   source_sha256=first.digest(Path(__file__))))
        turn=0
        with ProcessPoolExecutor(2,mp_context=mp.get_context('spawn'),max_tasks_per_child=1) as pool:
            active={}
            while True:
                active_keys=set(active.values())
                while len(active)<2:
                    selected=None
                    for offset in range(2):
                        group=('brief','brief2')[(turn+offset)%2]
                        job=pending_task(group,active_keys)
                        if job:
                            phase,case=job
                            selected=(group,phase,case)
                            turn=(('brief','brief2').index(group)+1)%2
                            break
                    if selected is None:
                        break
                    group,phase,case=selected
                    key=(group,phase,case['case_id'])
                    active[pool.submit(_work,group,phase,case)]=key
                    active_keys.add(key)
                    print(json.dumps(dict(event='submitted',group=group,phase=phase,case_id=case['case_id'])),flush=True)
                if not active and group_done('brief') and group_done('brief2'):
                    break
                if active:
                    done,_=wait(active,timeout=15,return_when=FIRST_COMPLETED)
                    for future in done:
                        key=active.pop(future)
                        print(json.dumps(dict(event='finished',key=key,result=future.result())),flush=True)
                else:
                    time.sleep(15)
                refresh()
        # Let the original monitor finish its final first-only write, then own the combined report.
        for _ in range(4):
            if (first.RUN/'completion.json').exists():
                break
            time.sleep(15)
        summary=refresh()
        first.write(CONTROL/'completion.json',dict(status='complete_with_recorded_interruptions' if recovered else 'complete',
                   completed_utc=datetime.now(timezone.utc).isoformat(),summary=summary,first_interrupted=recovered))
        print('Both cohorts have saved plans/failures and fresh audits.',flush=True)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode',choices=('prepare-second','run','recover'))
    args=cli.parse_args()
    if args.mode=='prepare-second': prepare_second()
    elif args.mode=='recover': print(recover_interrupted())
    else: run()
