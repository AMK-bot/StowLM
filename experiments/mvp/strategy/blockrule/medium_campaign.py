"""Use one medium map slot alongside small train, then three after it drains.

Run only after generation and diagnosis stop. Runtime/budgets are exactly
study.run_map; this controller changes admission only, never plan selection.
"""
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path[:0]=[str(HERE),str(ROOT)]
import study
from experiments.mvp.strategy.blockrule.report import render


def initialize():
    study.atomic_json(HERE/'runs/medium-train/owned'/f'{os.getpid()}.json',
                      dict(pid=os.getpid(),started_at=time.time(),role='map worker; idle until assigned'))


def main():
    manifest=HERE/'cohort-medium.json'
    metadata,jobs=study.jobs_from_manifest(manifest)
    small=HERE/'runs/small-train'
    small_run=study.read_json(small/'run.json')
    config=small_run['config']
    sources=study.source_snapshot()
    if small_run['sources']!=sources:
        raise RuntimeError('Small train source epoch changed')
    out=HERE/'runs/medium-train'
    out.mkdir(parents=True,exist_ok=True)
    record=dict(config=config,sources=sources,jobs=jobs,split=metadata['split'],
                manifest_sha256=study.digest(manifest),rule_freeze_v1=False,
                feasibility_extra={'global_screen':0},cpu_only=True,no_training=True)
    with study.headroom.controller_lock(out):
        if (out/'run.json').exists() and study.read_json(out/'run.json')!=record:
            raise RuntimeError('Medium source/input/config changed')
        study.atomic_json(out/'run.json',record)
        study.atomic_json(out/'controller.json',dict(pid=os.getpid(),started_at=time.time(),
            max_workers=3,initial_active_limit=1,expand_when=str(small/'completion.json'),
            controller_sha256=study.digest(__file__)))
        errors=[]
        pending=list(jobs)
        with ProcessPoolExecutor(max_workers=3,mp_context=mp.get_context('spawn'),initializer=initialize) as pool:
            futures={}
            while pending or futures:
                limit=3 if (small/'completion.json').exists() else 1
                while pending and len(futures)<limit:
                    job=pending.pop(0)
                    futures[pool.submit(study.run_map,job,out,config)]=job
                study.atomic_json(out/'admission.json',dict(active_medium=len(futures),active_limit=limit,
                    active_small_cap=0 if limit==3 else 2,max_active_maps_combined=3,
                    max_compute_processes_combined=6,pending=len(pending),updated_at=time.time()))
                completed,_=wait(futures,timeout=30,return_when=FIRST_COMPLETED)
                for future in completed:
                    job=futures.pop(future)
                    try:
                        result=future.result()
                        if result.get('error'):
                            errors.append(dict(map_id=job['map_id'],error=result['error']))
                        print(json.dumps(dict(map_id=job['map_id'],complete=result.get('complete'),error=result.get('error'))),flush=True)
                    except Exception as exc:
                        errors.append(dict(map_id=job['map_id'],error=str(exc)))
                render()
        study.atomic_json(out/'completion.json',dict(maps=len(jobs),errors=errors,ended_at=time.time()))
    render()
    if errors:
        raise RuntimeError(f'Medium study errors: {errors}')


if __name__=='__main__':
    main()
