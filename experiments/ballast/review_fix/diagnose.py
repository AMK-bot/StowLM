"""Independent exact departure/full + pure-derived arrival endpoint evidence."""
import json
import os
from pathlib import Path
import time

for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
HERE=Path(__file__).resolve().parent


def check_endpoints(instance,plan):
    from stowage.checker.full import check
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength
    from stowage.model import BallastCondition
    from stowage.model.voyage import derive_arrival_liquid_state
    from stowage.moves import apply_plan
    if plan.ballast is None:
        plan=plan.model_copy(update={'ballast':BallastCondition(source='planner',tank_masses_kg={
            t.id:instance.onboard.tank_masses_kg.get(t.id,0.) for t in instance.vessel.tanks if t.adjustable})})
    report=check(instance,plan)
    replay=apply_plan(instance,plan)
    assert replay.legal
    departure=replay.final_state.ship.model_copy(update={'tank_masses_kg':{
        **replay.final_state.ship.tank_masses_kg,**plan.ballast.tank_masses_kg}})
    liquids=derive_arrival_liquid_state(departure,instance.voyage,vessel=instance.vessel)
    arrival=departure.model_copy(update={'tank_masses_kg':liquids.tank_masses_kg})
    port=instance.port.model_copy(update={'locode':liquids.condition.locode,
        'water_density_kg_m3':liquids.condition.water_density_kg_m3,
        'max_draught_m':None,'port_rules':[]})
    arrival_instance=instance.model_copy(update={'port':port,'call_index':liquids.condition.call_index})
    parts={name:checker(arrival_instance,arrival,call_index=liquids.condition.call_index)
           for name,checker in [('stability',check_stability),('strength',check_strength)]}
    return dict(departure=report.model_dump(mode='json'),
        arrival={name:part.model_dump(mode='json') for name,part in parts.items()},
        arrival_liquids=liquids.model_dump(mode='json'),
        departure_consumables_kg={tid:departure.tank_masses_kg.get(tid,0.)
            for group in instance.vessel.voyage_policy.groups for tid in group.tank_ids},
        both_passed=report.passed and all(p.ran and not any(v.severity=='hard' for v in p.violations)
            for p in parts.values()))


if __name__=='__main__':
    import argparse
    from stowage.io import load_instance,load_plan
    from stowage.solver.ballast_deadline import run_with_deadline,BallastDeadline
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--witness',action='store_true')
    args=parser.parse_args()
    entries=([dict(case='synthetic-witness')] if args.witness else json.loads((HERE/'manifest.json').read_text()))
    results=[]
    for entry in entries:
        folder=HERE/'synthetic-witness' if args.witness else HERE/'inputs'/entry['case']
        instance=load_instance(folder/'instance.json')
        plan=load_plan(folder/('witness-plan.json' if args.witness else 'input-plan.json'))
        started=time.monotonic()
        try:
            result=run_with_deadline(check_endpoints,(instance,plan),deadline=started+15.)
        except BallastDeadline:
            result=dict(timeout=True)
        row=dict(case=entry['case'],wall_s=time.monotonic()-started,**result)
        results.append(row)
        output=HERE/('witness-diagnostics.json' if args.witness else 'diagnostics.json')
        output.write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps(dict(case=entry['case'],wall_s=row['wall_s'],
            both_passed=row.get('both_passed'),timeout=row.get('timeout',False))),flush=True)
