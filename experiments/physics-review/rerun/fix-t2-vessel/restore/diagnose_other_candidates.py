"""Exact finite-liquid-CG sensitivities; not full-checker acceptance evidence.

No cargo or consumables change. Only prescribe a mass-preserving symmetric
ballast relocation, identical at both D50 endpoints. This is an experiment,
not a production loading-manual correction or regulatory relaxation.
"""
from __future__ import annotations

import json
from pathlib import Path
import resource
import signal
import time

from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_lashing_design import evaluate_manual_condition
from stowage.vessel.fleet_manual import standard_manual_conditions

HERE = Path(__file__).resolve().parent


def main():
    signal.alarm(60)
    start = time.perf_counter()
    vessel, params = load_vessel('feeder_1700'), load_ship_params('feeder_1700')
    recipe = next(row for row in standard_manual_conditions(vessel, params) if row['condition']=='C2')
    tanks = {tank.id:tank for tank in vessel.tanks}
    result = dict(estimate=dict(cores=1,ram_gb=2,gpu=False,wall_s=10,timeout_s=60),
                  no_full_checker_acceptance_claim=True, cargo_unchanged=True,
                  consumables_unchanged=True, total_ballast_unchanged=True, rows=[])
    for fraction in (.25,.40):
        contents = dict(recipe['departure_tank_masses_kg'])
        for side in ('P','S'):
            fpt, deep = (prefix+'-'+side for prefix in ('FPT','FWD-DEEP'))
            new_fpt=tanks[fpt].capacity_m3*tanks[fpt].content_density_kg_m3
            new_deep=fraction*tanks[deep].capacity_m3*tanks[deep].content_density_kg_m3
            delta=new_fpt+new_deep-contents[fpt]-contents[deep]
            contents[fpt],contents[deep]=new_fpt,new_deep
            aft='DB-A-'+side
            delta-=contents[aft]
            contents[aft]=0.
            contents['DB-M-'+side]-=delta
        assert all(contents[t.id]>=0. and contents[t.id]<=t.capacity_m3*t.content_density_kg_m3+1e-6
                   for t in vessel.tanks)
        assert abs(sum(contents.values())-sum(recipe['departure_tank_masses_kg'].values()))<1e-6
        alternative={**recipe,'departure_tank_masses_kg':contents}
        row=dict(label=f'FPTfull_FWD-DEEP{fraction:g}_DB-Aempty_DB-Mreduced',
                 departure_tank_masses_kg=contents,phases={})
        for phase in ('departure','arrival'):
            row['phases'][phase]=evaluate_manual_condition(vessel,params,alternative,phase)
        result['rows'].append(row)
        result['elapsed_s']=time.perf_counter()-start
        result['peak_rss_gb']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9
        (HERE/'diagnose_other_candidates.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        print(json.dumps(dict(label=row['label'], phases={phase:{key:value[key] for key in
            ('gm_m','mass_kg','lcg_m','kg_m','draught_aft_m','draught_fwd_m','list_rad')}
            for phase,value in row['phases'].items()},elapsed_s=result['elapsed_s'])),flush=True)
    signal.alarm(0)


if __name__=='__main__':
    main()
