"""Complete the admitted D185 campaign and preserve a frozen first report."""
from __future__ import annotations
import json
import os
from pathlib import Path
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
    study.atomic_json(HERE/'supervisor-status.json',dict(pid=os.getpid(),phase=phase,
                      updated_at=time.time(),**extra))


def first_report():
    path=HERE/'FIRST_REPORT.md'
    if path.exists():
        return
    cohort=study.read_json(HERE/'cohort-medium.json')
    lines=['# D185 first report','',time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime()),'',
           'The following twelve medium TRAIN maps are frozen and ready as the proposer cohort. Full producer audits and input hashes verified. No medium planner outcomes were used to select them. Corrected medium generation uses explicit slack 0 and no mass fraction override; the rejected small-setting assumption is retained in COHORT.md.','',
           '| Ship | Family | Seed | Loads | Instance SHA256 |','|---|---|---:|---:|---|']
    for row in cohort['maps']:
        lines.append(f'| {row["ship"]} | {row["family"]} | {row["seed"]} | {row["n_loads"]} | `{row["sha256"]}` |')
    lines+=['','Exact paths, source bindings and producer audit hashes: [cohort-medium.json](cohort-medium.json).','',
            'The checked study snapshot follows. Pending maps are not counted as completed comparisons.','',
            (HERE/'BLOCKRULE.md').read_text()]
    path.write_text('\n'.join(lines)+'\n')
    study.atomic_json(HERE/'first-report.json',dict(written_at=time.time(),medium_manifest_sha256=study.digest(HERE/'cohort-medium.json'),
                      first_report_sha256=study.digest(path)))


def main():
    # Fail before launching workers if a caller resolved the venv interpreter
    # symlink and accidentally discarded its site-packages environment.
    import pydantic
    from stowage.model import PlannerResult
    study.atomic_json(HERE/'runtime.json',dict(executable=sys.executable,prefix=sys.prefix,
                      pydantic_version=pydantic.__version__))
    state('waiting_for_frozen_medium_and_diagnosis')
    while not (HERE/'cohort-verification.json').exists():
        render()
        time.sleep(30)
    render()
    first_report()
    while not (HERE/'diagnosis/completion.json').exists():
        state('medium_frozen_waiting_for_diagnosis')
        render()
        time.sleep(30)
    state('medium_train')
    command=[sys.executable,str(HERE/'medium_campaign.py')]
    out=HERE/'runs/medium-train'
    out.mkdir(parents=True,exist_ok=True)
    with (out/'controller.log').open('a') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    study.atomic_json(out/'launch.json',dict(pid=child.pid,command=command,started_at=time.time()))
    while child.poll() is None:
        render()
        time.sleep(30)
    if child.returncode:
        raise RuntimeError(f'Medium controller failed {child.returncode}')
    while not (HERE/'runs/small-train/completion.json').exists():
        state('waiting_for_small_train_to_drain')
        render()
        time.sleep(30)
    state('freezing_train')
    campaign.freeze()
    state('validation_once')
    campaign.validate(3)
    state('verifying')
    from experiments.mvp.strategy.blockrule import verify
    verify.main()
    render()
    state('complete',verification_sha256=study.digest(HERE/'verification.json'))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        state('error',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
        raise
