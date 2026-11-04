"""Read-only longitudinal authority diagnostic, not a checker acceptance claim.

Keep prescribed cargo, consumables and ballast masses. At fixed displacement,
KG and heel, solve the exact clipped-hull volume for the forward draught when
the aft draught reaches its hard limit minus 1 cm. The longitudinal waterplane
axis then gives the total LCG needed for longitudinal equilibrium. This is a
necessary target for that pose, not a proposed full 3-D liquid equilibrium.
APPROXIMATION: available transfer moments use full-tank upright centroids;
actual partial-fill/trim/list CGs and all checks must be evaluated independently.
"""
from __future__ import annotations

import json
from pathlib import Path
import resource
import signal
import time

from scipy.optimize import brentq

from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_lashing_design import evaluate_manual_condition
from stowage.vessel.fleet_manual import standard_manual_conditions
from stowage.vessel.hydrostatics import HullGeometry

HERE = Path(__file__).resolve().parent
CASES = {'feeder_500': ('C1', 'C3'), 'feeder_1000': ('C2',), 'feeder_1700': ('C2', 'C3')}


def main():
    signal.alarm(80)  # Bound this process only; root admits longer jobs.
    began = time.perf_counter()
    result = dict(estimate=dict(cores=1, ram_gb=2, gpu=False, wall_s=30, timeout_s=80),
                  no_full_checker_acceptance_claim=True, rows=[])
    for name, labels in CASES.items():
        vessel, params = load_vessel(name), load_ship_params(name)
        geometry = HullGeometry.from_params(params)
        recipes = {row['condition']: row for row in standard_manual_conditions(vessel, params)}
        for label in labels:
            recipe = recipes[label]
            row = dict(ship=name, condition=label, ballast_label=recipe['ballast_label'],
                       cargo_mass_kg=recipe['cargo_mass_kg'], phases={}, tanks=[])
            departure = recipe['departure_tank_masses_kg']
            for tank in vessel.tanks:
                if tank.adjustable:
                    capacity = tank.capacity_m3*tank.content_density_kg_m3
                    row['tanks'].append(dict(id=tank.id, mass_kg=departure.get(tank.id, 0.),
                        capacity_kg=capacity, spare_kg=capacity-departure.get(tank.id, 0.),
                        upright_full_lcg_m=tank.table[-1].lcg_m,
                        upright_full_kg_m=tank.table[-1].vcg_m))
            row['ballast_mass_kg'] = sum(tank['mass_kg'] for tank in row['tanks'])
            row['ballast_capacity_kg'] = sum(tank['capacity_kg'] for tank in row['tanks'])
            # Symmetric groups remove accidental port/starboard allocation claims.
            groups = {}
            for tank in row['tanks']:
                group = tank['id'].rsplit('-', 1)[0]
                entry = groups.setdefault(group, dict(mass_kg=0., spare_kg=0., moment_kg_m=0., capacity_kg=0.))
                for key in ('mass_kg','spare_kg','capacity_kg'):
                    entry[key] += tank[key]
                entry['moment_kg_m'] += tank['upright_full_lcg_m']*tank['capacity_kg']
            for group, entry in groups.items():
                entry['lcg_m'] = entry.pop('moment_kg_m')/entry['capacity_kg']
            row['symmetric_groups'] = groups
            for phase in ('departure', 'arrival'):
                metrics = evaluate_manual_condition(vessel, params, recipe, phase)
                mass, kg, heel = (metrics[key] for key in ('mass_kg','kg_m','list_rad'))
                limit = vessel.hull.max_draught_m
                target_aft = limit-.01
                target_fwd = brentq(lambda tf: geometry.submerged(target_aft, tf, heel,
                    metrics['water_density_kg_m3']).displacement_kg-mass, -geometry.depth_m,
                    geometry.depth_m*2, xtol=1e-10)
                submerged = geometry.submerged(target_aft, target_fwd, heel, metrics['water_density_kg_m3'])
                slope = (target_fwd-target_aft)/geometry.lpp_m
                target_lcg = submerged.lcb_m+(submerged.vcb_m-kg)*slope
                shift_moment = mass*(target_lcg-metrics['lcg_m'])
                metrics.update(max_draught_m=limit, target_aft_m=target_aft, target_fwd_m=target_fwd,
                    target_trim_m=target_aft-target_fwd, target_lcg_m=target_lcg,
                    forward_shift_required_kg_m=shift_moment,
                    aft_excess_m=metrics['draught_aft_m']-limit,
                    same_mass_kg_heel_longitudinal_target_only=True)
                row['phases'][phase] = metrics
            # Greedy relocation of existing mass into currently unused higher-x
            # symmetric groups; this demonstrates geometric authority only.
            donors = [[key, val['mass_kg'], val['lcg_m']] for key,val in groups.items() if val['mass_kg']>0]
            receivers = [[key, val['spare_kg'], val['lcg_m']] for key,val in groups.items() if val['spare_kg']>0]
            donors.sort(key=lambda val: val[2]); receivers.sort(key=lambda val: -val[2])
            transfers=[]
            for donor in donors:
                for receiver in receivers:
                    if receiver[2]<=donor[2] or donor[0]==receiver[0]:
                        continue
                    mass=min(donor[1], receiver[1])
                    if mass <= 1e-6:
                        continue
                    transfers.append(dict(source=donor[0],destination=receiver[0],mass_kg=mass,
                        moment_kg_m=mass*(receiver[2]-donor[2])))
                    donor[1]-=mass;receiver[1]-=mass
            row['approx_forward_transfer_candidates']=transfers
            row['approx_total_forward_moment_kg_m']=sum(item['moment_kg_m'] for item in transfers)
            result['rows'].append(row)
            result['elapsed_s']=time.perf_counter()-began
            result['peak_rss_gb']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9
            (HERE/'diagnose_other.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
            print(json.dumps(dict(ship=name,condition=label,ballast_kg=row['ballast_mass_kg'],
                capacity_kg=row['ballast_capacity_kg'],approx_forward_moment_kg_m=row['approx_total_forward_moment_kg_m'],
                phases={phase:{key:values[key] for key in ('aft_excess_m','target_trim_m','forward_shift_required_kg_m')}
                        for phase,values in row['phases'].items()},elapsed_s=result['elapsed_s'])),flush=True)
    signal.alarm(0)


if __name__ == '__main__':
    main()
