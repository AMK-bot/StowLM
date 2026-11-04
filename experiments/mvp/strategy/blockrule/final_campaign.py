"""Drain initial study, adopt frozen v2, measure train, validate exactly once."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path[:0]=[str(HERE),str(ROOT)]
import study
from experiments.mvp.strategy.blockrule import campaign
from experiments.mvp.strategy.blockrule.report import render


def state(phase,**extra):
    study.atomic_json(HERE/'final-status.json',dict(pid=os.getpid(),phase=phase,updated_at=time.time(),**extra))


def main():
    import pydantic
    from capacity_train import ready
    state('waiting_for_initial_train',executable=sys.executable,prefix=sys.prefix,pydantic=pydantic.__version__)
    control=HERE/'runs/final-control'
    control.mkdir(parents=True,exist_ok=True)
    with study.headroom.controller_lock(control):
        if not (HERE/'capacity-status.json').exists() or study.read_json(HERE/'capacity-status.json').get('phase')!='train_complete':
            while not ready():
                render()
                time.sleep(30)
            switch=study.read_json(HERE/'epoch-switch.json')
            staged=HERE/switch['stage']
            if study.digest(staged)!=switch['final_study_sha256']:
                raise RuntimeError('Staged v2 study changed')
            current=study.digest(HERE/'study.py')
            if current==switch['expected_current_sha256']:
                temp=HERE/f'study.py.switch-{os.getpid()}'
                shutil.copyfile(staged,temp)
                temp.replace(HERE/'study.py')
            elif current!=switch['final_study_sha256']:
                raise RuntimeError('Study changed outside authorized epoch switch')
            state('capacity_train',study_sha256=study.digest(HERE/'study.py'))
            command=[sys.executable,str(HERE/'capacity_train.py'),'--workers','3']
            log_path=HERE/'runs/capacity-control/controller.log'
            log_path.parent.mkdir(parents=True,exist_ok=True)
            with log_path.open('a') as log:
                child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
            study.atomic_json(log_path.parent/'launch.json',dict(pid=child.pid,command=command,started_at=time.time()))
            while child.poll() is None:
                render()
                time.sleep(30)
            if child.returncode:
                raise RuntimeError(f'Capacity train failed {child.returncode}')
        if study.read_json(HERE/'capacity-status.json')['phase']!='train_complete':
            raise RuntimeError('Capacity train did not commit')
        state('freezing_train')
        if not (HERE/'train-freeze.json').exists():
            campaign.freeze()
        elif study.read_json(HERE/'train-freeze.json')['sources']!=study.source_snapshot():
            raise RuntimeError('Existing final train freeze has different sources')
        state('validation_once')
        if not (HERE/'validation-once.json').exists():
            campaign.validate(3)
        elif not (HERE/'runs/small-val/completion.json').exists():
            raise RuntimeError('Validation already claimed but unfinished; observe/resume the recorded campaign with identical inputs and sources. No second validation launch attempted.')
        state('verifying')
        from experiments.mvp.strategy.blockrule import verify
        verify.main()
        render()
        shutil.copyfile(HERE/'BLOCKRULE.md',HERE/'FINAL_REPORT.md')
        state('complete',verification_sha256=study.digest(HERE/'verification.json'),
              final_report_sha256=study.digest(HERE/'FINAL_REPORT.md'))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        state('error',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
        raise
