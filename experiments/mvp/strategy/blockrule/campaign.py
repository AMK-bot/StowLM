"""Supervise frozen train campaigns then exactly one validation campaign.

Only launches processes it records; report refreshes are read-only. A manual
train-freeze.json records the final rule and diagnostics before validation.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(HERE),str(ROOT)]
import study
from experiments.mvp.strategy.blockrule.report import render


def launch(name, manifest, workers):
    out = HERE/'runs'/name
    out.mkdir(parents=True,exist_ok=True)
    command = [sys.executable,str(HERE/'study.py'),'--manifest',str(manifest),'--out',str(out),'--workers',str(workers)]
    with (out/'controller.log').open('a') as log:
        child = subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    study.atomic_json(out/'launch.json',dict(pid=child.pid,command=command,started_at=time.time()))
    return child


def freeze():
    campaigns = {}
    for name in ('small-train','medium-train'):
        out = HERE/'runs'/name
        done = study.read_json(out/'completion.json')
        if done['errors']:
            raise RuntimeError(f'{name} has incomplete maps/errors')
        run = study.read_json(out/'run.json')
        changed = [p for p,h in run['sources'].items() if study.digest(ROOT/p)!=h]
        if changed:
            raise RuntimeError(f'Sources changed during {name}: {changed}')
        for job in run['jobs']:
            summary = study.read_json(out/'maps'/job['map_id']/'summary.json')
            if not summary.get('complete'):
                raise RuntimeError(f'{job["map_id"]} incomplete')
        campaigns[name] = dict(run_sha256=study.digest(out/'run.json'), completion_sha256=study.digest(out/'completion.json'), maps=len(run['jobs']))
    if campaigns['medium-train']['maps']<12:
        raise RuntimeError('At least 12 medium train maps required')
    path = HERE/'train-freeze.json'
    if path.exists():
        raise RuntimeError('Train already frozen')
    value = dict(frozen_at=time.time(),sources=study.source_snapshot(),train=campaigns,
                 validation_manifest_sha256=study.digest(HERE/'cohort-small-val.json'),
                 rule='rule.port_blocking_layout',assumption='No parameter changes after train; one validation campaign')
    study.atomic_json(path,value)
    print(json.dumps(value['train']))


def validate(workers):
    frozen = study.read_json(HERE/'train-freeze.json')
    if frozen['sources'] != study.source_snapshot():
        raise RuntimeError('Source/config changed since train freeze')
    manifest = HERE/'cohort-small-val.json'
    if frozen['validation_manifest_sha256'] != study.digest(manifest):
        raise RuntimeError('Validation manifest changed')
    _,jobs = study.jobs_from_manifest(manifest)
    if len(jobs)!=55:
        raise RuntimeError('Exactly 55 small validation maps required')
    claim = HERE/'validation-once.json'
    with claim.open('x') as stream:
        json.dump(dict(started_at=time.time(),train_freeze_sha256=study.digest(HERE/'train-freeze.json'),
                       manifest_sha256=study.digest(manifest),workers=workers),stream,indent=2)
    child = launch('small-val',manifest,workers)
    while child.poll() is None:
        render()
        time.sleep(30)
    render()
    if child.returncode:
        raise RuntimeError(f'Validation exited {child.returncode}')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('freeze','validate','monitor'))
    parser.add_argument('--workers',type=int,choices=(1,2,3),default=3)
    args=parser.parse_args()
    if args.action=='freeze':
        freeze()
    elif args.action=='validate':
        validate(args.workers)
    else:
        while True:
            render()
            time.sleep(30)
