"""Full-check the hypothetical C2 homogeneous18t loading; no production edits.

APPROXIMATION: change the homogeneous cargo recipe only. Preserve the original
97% consumables, ballast witness, actual voyage, densities and hard rules.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import resource
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import recheck


def main():
    from slices import bounded_full_check
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.vessel.fleet import load_ship_params,load_vessel
    from stowage.vessel.fleet_manual import standard_manual_conditions,place_manual_cargo,uniform_placement_count

    began=time.perf_counter()
    vessel=load_vessel('feeder_500');params=load_ship_params('feeder_500')
    recipe=next(row for row in standard_manual_conditions(vessel,params) if row['condition']=='C2')
    mass=18000.;count=int(recipe['cargo_mass_kg']//mass)
    cargo=place_manual_cargo(vessel,[mass]*count)
    ballast={tank.id:recipe['departure_tank_masses_kg'][tank.id] for tank in vessel.tanks if tank.adjustable}
    consumables={tank.id:(0. if tank.adjustable else recipe['departure_tank_masses_kg'][tank.id]) for tank in vessel.tanks}
    harness=recheck._load_harness()
    instance=harness.h1._wrap('feeder_500',vessel,'C2_homogeneous18t',cargo,consumables)
    for box in instance.containers:
        box.max_gross_mass_kg=30480.
    instance=recheck.attach_voyage(instance)
    plan=MovePlan(id=f'{instance.id}.diagnostic',instance_id=instance.id,call_index=instance.call_index,
        ballast=BallastCondition(tank_masses_kg=ballast,source='planner'))
    source=Path(__file__).resolve()
    identity=recheck._input_identity('feeder_500','C2_homogeneous18t',instance,plan,source)
    (HERE/'diagnose_500_fullcheck.instance.json').write_text(instance.model_dump_json(indent=2)+'\n')
    (HERE/'diagnose_500_fullcheck.plan.json').write_text(plan.model_dump_json(indent=2)+'\n')
    result=dict(recipe_change='homogeneous18t, count floor(original cargo mass /18t), same hold-first placement',
        original_recipe={key:recipe[key] for key in ('boxes','uniform_mass_kg','cargo_mass_kg','cargo_budget_kg','ballast_label')},
        candidate_boxes=len(cargo),candidate_cargo_mass_kg=sum(value for _,value in cargo),
        original_scan_at_18t=dict(full_placement_boxes=uniform_placement_count(vessel,mass),
            full_placement_mass_kg=mass*uniform_placement_count(vessel,mass)),
        ballast_masses_kg=ballast,consumables_kg=consumables,input_identity=identity,
        input_fingerprint=recheck._digest(identity),voyage=instance.voyage.model_dump(mode='json'))
    result['check']=bounded_full_check(instance,plan)
    after=recheck._input_identity('feeder_500','C2_homogeneous18t',instance,plan,source)
    result['source_drift']=after!=identity
    result['phase_reports']=recheck._phase_reports(result['check'],instance.voyage)
    result['elapsed_s']=time.perf_counter()-began
    result['peak_rss_gb']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9
    (HERE/'diagnose_500_fullcheck.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({key:result[key] for key in ('candidate_boxes','candidate_cargo_mass_kg','original_scan_at_18t','elapsed_s','peak_rss_gb','source_drift')}|
        {'check':{key:value for key,value in result['check'].items() if key!='report'},
         'violations':[(row.get('rule_id'),row.get('measured'),row.get('limit')) for row in result['check'].get('report',{}).get('violations',[])]}),flush=True)


if __name__=='__main__':
    main()
