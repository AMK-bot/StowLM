"""Map real total-area and securing margins across selected tank regimes."""
from pathlib import Path
import json
import os
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
sys.dont_write_bytecode = True
sys.path[:0] = [str(HERE),str(ROOT/'src')]
from physics import PhysicsEvaluator
from stowage.io import load_instance
from stowage.model import MovePlan


def main():
    old = ROOT/'experiments/mvp/frontier/lab/c2/c2-benders/results'
    r = json.loads((old/'ballast-exact-r5-gm0p9.json').read_text())
    instance = load_instance(ROOT/r['input'])
    plan = MovePlan.model_validate(r['candidate_plan'])
    evaluator = PhysicsEvaluator(instance,plan)
    base = dict(r['tank_masses_kg'])
    caps = dict(zip(evaluator.tank_ids,evaluator.capacity_kg))
    out = HERE/'results/regimes';out.mkdir(parents=True,exist_ok=True)
    variants = [('baseline',base)]
    for name in ('DB-M','DB-A'):
        for fraction in (.99,.975,.95,.9):
            mass = dict(base)
            for side in ('P','S'):
                mass[name+'-'+side] = float(caps[name+'-'+side]*fraction)
            variants.append((f'{name}-{fraction}',mass))
    rows = []
    for name,masses in variants:
        target=out/f'{name}.json'
        if target.exists():
            rows.append(json.loads(target.read_text()));continue
        before=monotonic()
        arrival=evaluator.evaluate(masses,phases=('arrival',),parts=('stability',))
        departure=evaluator.evaluate(masses,phases=('departure',),parts=('lashing',))
        row=dict(name=name,tank_masses_kg=masses,arrival=arrival,departure=departure,
                 wall_s=monotonic()-before)
        target.write_text(json.dumps(row,indent=2))
        rows.append(row)
        print(json.dumps(dict(name=name,s=row['wall_s'],area={k:v for k,v in arrival['raw_margins'].items() if '0_flood' in k},
                              securing=min(departure['margins'].values(),default=-1),complete=arrival['completed'] and departure['completed'])),flush=True)
    (out/'summary.json').write_text(json.dumps(rows,indent=2))


if __name__=='__main__':main()
