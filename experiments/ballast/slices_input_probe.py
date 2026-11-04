"""Separate missing physics input from an unsuccessfully repaired departure.

Keep the generator's actual tank fills. Setting them explicitly in a plan is
only a checker baseline, never an assertion that the departure is feasible.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import time
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
from stowage.io import load_instance, load_plan
from stowage.model import BallastCondition
from stowage.checker.full import check
from stowage.solver.ballast_deadline import run_with_deadline, BallastDeadline


def main():
    root=Path(__file__).resolve().parent/'slices_current'
    rows=[]
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        instance=load_instance(folder/'instance.json')
        plan=load_plan(folder/'input-plan.json')
        baseline=plan.model_copy(update={'ballast':BallastCondition(source='planner',tank_masses_kg={
            t.id:instance.onboard.tank_masses_kg.get(t.id,0.) for t in instance.vessel.tanks if t.adjustable})})
        started=time.monotonic()
        try:
            report=run_with_deadline(check,(instance,baseline),deadline=started+8.)
            row=dict(id=folder.name,wall_s=time.monotonic()-started,passed=report.passed,
                     violations=[dict(rule_id=v.rule_id,message=v.message) for v in report.violations],
                     parts={n:dict(ran=p.ran,error=p.error,margins=p.margins,metrics=p.metrics,
                                   violations=[dict(rule_id=v.rule_id,message=v.message) for v in p.violations])
                            for n,p in report.parts.items()})
        except BallastDeadline:
            row=dict(id=folder.name,wall_s=time.monotonic()-started,timeout=True)
        rows.append(row)
        print(json.dumps(row),flush=True)
        (root/'initial_full_checks.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':
    main()
