"""Replace a wide slack fore tank with narrow tanks at equal mass/moment.

This deliberately explores a tank regime excluded by symmetric-pair search.
Recipes preserve total mass and native upright longitudinal moment; a HEEL
split controls actual raw arrival TCG. Full native physics/checker decide.
"""
from pathlib import Path
import json
import os
import sys
from time import monotonic

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(HERE/'results/vessel-cache')
sys.dont_write_bytecode=True
sys.path[:0]=[str(HERE),str(ROOT/'src')]
from scipy.optimize import brentq
from physics import PhysicsEvaluator
from stowage.checker import check,stability
from stowage.checker.stability_loading import loading_condition
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.solver.ballast_v4_evaluator import conditions


def main():
    print(json.dumps({'owned_pid':os.getpid()}),flush=True)
    out=HERE/'results/redistribute-both';out.mkdir(parents=True,exist_ok=True)
    instance=load_instance(ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_500/random_yard/seed-185100000.json')
    plan=MovePlan.model_validate_json((HERE/'results/optimize-cusp/candidate-plan.json').read_text())
    evaluator=PhysicsEvaluator(instance,plan)
    base=dict(zip(evaluator.tank_ids,map(float,evaluator.initial_masses_kg)))
    capacity=dict(zip(evaluator.tank_ids,evaluator.capacity_kg))
    geometry,params=stability._context(instance.vessel)

    def raw(masses,phase):
        ship=evaluator.ship.model_copy(update={'tank_masses_kg':{**evaluator.fixed_masses_kg,**masses}})
        inst,state=next((i,s)for p,i,s in conditions(instance,ship)if p==phase)
        return loading_condition(inst,state,geometry=geometry,ship_params=params,
            free_surface_method=1,precomputed_soundings=True,small_tank_exemption=True)

    target=raw(base,'departure');target_moment=target.mass_kg*target.lcg_m
    rows=[]
    for front in ('FPT','FWD-DEEP'):
        for fraction in (1.,.75,.5,.25):
            started=monotonic();amount=(base['DB-F-P']+base['DB-F-S'])*fraction
            masses=dict(base)
            for side in ('P','S'):masses['DB-F-'+side]*=1.-fraction
            for repeat in range(2):
                def fore_allocation(a):
                    trial=dict(masses)
                    for side in ('P','S'):
                        trial[front+'-'+side]=base[front+'-'+side]+a/2
                        trial['WING-A-'+side]=base['WING-A-'+side]+(amount-a)/2
                    return trial
                def residual(a):
                    c=raw(fore_allocation(a),'departure')
                    return c.mass_kg*c.lcg_m-target_moment
                a=brentq(residual,0.,amount,xtol=1e-5)
                masses=fore_allocation(a)
                before_p,before_s=masses['HEEL-P'],masses['HEEL-S']
                def transverse(delta):
                    trial=dict(masses);trial['HEEL-P']=before_p+delta;trial['HEEL-S']=before_s-delta
                    return raw(trial,'arrival').tcg_m
                low=max(-before_p,before_s-capacity['HEEL-S'])
                high=min(capacity['HEEL-P']-before_p,before_s)
                delta=brentq(transverse,low,high,xtol=1e-5)
                masses['HEEL-P']=before_p+delta;masses['HEEL-S']=before_s-delta
            name=f'{front}-{fraction}'
            result=evaluator.evaluate(masses)
            row=dict(name=name,front_mass_kg=a,removed_mass_kg=amount,
                     mass_delta_kg=sum(masses.values())-sum(base.values()),
                     departure_moment_delta_kgm=raw(masses,'departure').mass_kg*raw(masses,'departure').lcg_m-target_moment,
                     physics=result,wall_s=monotonic()-started)
            candidate=evaluator.candidate_plan(masses)
            (out/f'{name}-plan.json').write_text(candidate.model_dump_json(indent=2))
            if result['physics_feasible']:
                report=check(instance,candidate)
                row['full_checker_report']=report.model_dump(mode='json')
                row['full_checker_passed']=report.passed
            (out/f'{name}.json').write_text(json.dumps(row,indent=2,allow_nan=False))
            rows.append(row)
            print(json.dumps(dict(name=name,wall_s=row['wall_s'],physics=result['physics_feasible'],
                full_checker_passed=row.get('full_checker_passed'),
                negatives={k:v for k,v in result['raw_margins'].items()if v<0})),flush=True)
            (out/'summary.json').write_text(json.dumps([dict(name=r['name'],wall_s=r['wall_s'],
                full_checker_passed=r.get('full_checker_passed'),physics_feasible=r['physics']['physics_feasible'])for r in rows],indent=2))
            if row.get('full_checker_passed'):return


if __name__=='__main__':main()
