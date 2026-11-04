"""Read-only C2 cargo/GM sensitivities; these are not checker acceptance claims.

APPROXIMATION: hypothetical homogeneous loading-manual alternatives. Keep the
original .97 consumables, ballast witness, dock densities and D50 arrival rule.
No production state, model, checker, planner or parameter file is changed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import resource
import time

from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_lashing_design import evaluate_manual_condition, _cargo_points
from stowage.vessel.fleet_manual import standard_manual_conditions, place_manual_cargo

HERE = Path(__file__).resolve().parent


def main():
    started = time.perf_counter()
    vessel = load_vessel('feeder_500')
    params = load_ship_params('feeder_500')
    original = next(row for row in standard_manual_conditions(vessel, params) if row['condition'] == 'C2')
    cells = {cell.id: cell for cell in vessel.cells}
    stacks = {stack.id: stack for stack in vessel.stacks}
    ranks = {cid: index for stack in vessel.stacks for index, cid in enumerate(stack.cell_ids)}
    n20 = sum(cell.accepts_20 for cell in vessel.cells)
    cases = [('original', original['cargo_cells'])]
    # Keep original total cargo where ratings permit, changing only uniform
    # box mass and corresponding count under the same hold-first placement.
    for mass in (14000., 18000., 22000., 24900.):
        requested = min(n20, int(original['cargo_mass_kg']//mass))
        cases.append((f'near_same_mass_uniform_{mass:g}', place_manual_cargo(vessel, [mass]*requested)))
    # Keep original box mass, remove upper deck tiers only. Whole-tier removal
    # preserves symmetric geometry and supports, unlike deleting arbitrary boxes.
    max_deck = max(len(stack.cell_ids) for stack in vessel.stacks if stack.on_deck)
    for tiers in range(max_deck-1, max(0,max_deck-4)-1, -1):
        cases.append((f'original_uniform_deck_max_{tiers}_tiers',
            [(cid,mass) for cid,mass in original['cargo_cells']
             if not stacks[cells[cid].stack_id].on_deck or ranks[cid] < tiers]))
    result = dict(method='read_only_500_C2_recipe_sensitivity_v1',
        unchanged=['original ballast witness','97% departure consumables','D50 arrival drawdown','route/density','checker hard limits'],
        original_recipe={key: original[key] for key in ('ballast_label','cargo_budget_kg','cargo_mass_kg','boxes','uniform_mass_kg','recipe_source')},
        no_full_checker_acceptance_claim=True, cases=[])
    out = HERE/'diagnose_500_c2.json'
    for label, cargo in cases:
        row = dict(label=label, boxes=len(cargo), cargo_mass_kg=sum(mass for _,mass in cargo),
            uniform_mass_kg=cargo[0][1] if cargo else None,
            hold_boxes=sum(not stacks[cells[cid].stack_id].on_deck for cid,_ in cargo),
            deck_boxes=sum(stacks[cells[cid].stack_id].on_deck for cid,_ in cargo),
            cargo_sha256=hashlib.sha256(json.dumps(cargo,separators=(',',':')).encode()).hexdigest(),
            cargo_cells=cargo, phases={})
        points=_cargo_points(vessel,cargo)
        row['cargo_cg_m']=[sum(point[k]*point[3] for point in points)/row['cargo_mass_kg'] for k in range(3)]
        for phase in ('departure','arrival'):
            recipe={**original,'cargo_cells':cargo}
            try:
                row['phases'][phase]=evaluate_manual_condition(vessel,params,recipe,phase)
            except ValueError as exc:
                row['phases'][phase]={'error':str(exc)}
        result['cases'].append(row)
        result['elapsed_s']=time.perf_counter()-started
        result['peak_rss_gb']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9
        out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        print(json.dumps({key:value for key,value in row.items() if key not in ('cargo_cells','cargo_sha256','phases')}|
            {'phases':{phase:{key:metrics[key] for key in ('gm_m','gm_solid_m','fsc_m','mass_kg','kg_m','lcg_m','draught_aft_m','draught_fwd_m','error') if key in metrics}
                       for phase,metrics in row['phases'].items()},'elapsed_s':result['elapsed_s']}),flush=True)


if __name__ == '__main__':
    main()
