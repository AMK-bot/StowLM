"""Independent D56 integration comparison on actual cached production vessels.

Compare production manual recipes against the historical discrete harness,
then compare the pure design evaluator against visibility/lashing's loading
condition on the actual harness Instance and D50-derived arrival ShipState.
This is numerical/recipe consistency evidence, never a full-checker pass.
No builders/checks are stubbed, cargo regenerated, ballast optimized, or rules
relaxed. Root must admit/time a one-case pilot before running the whole set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from math import isclose
from pathlib import Path
import resource
import time

import recheck


HERE = Path(__file__).resolve().parent


def snapshot():
    paths = {*recheck.ROOT.joinpath('src/stowage').rglob('*.py'),
             *recheck.ROOT.joinpath('params').rglob('*.toml'),
             Path(__file__), HERE/'recheck.py', recheck.REVIEW/'harness2.py',
             recheck.REVIEW.parent/'harness.py', recheck.EFFECTIVENESS/'regression.py',
             recheck.ROOT/'data/raw/kcs/kcs.fix',
             *recheck.REVIEW.glob('search_feeder_*.json')}
    return {str(path.relative_to(recheck.ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def check_case(vessel, params, recipe):
    from regression import witness_masses
    from stowage.checker.visibility import _loading_condition
    from stowage.model import derive_arrival_liquid_state
    from stowage.vessel.fleet_lashing_design import evaluate_manual_condition

    name, label = params['id'], recipe['condition']
    harness = recheck._load_harness()
    source = recheck.REVIEW/f'search_{name}_{label}.json'
    if source.exists():
        old = json.loads(source.read_text())
        historical = old.get('witness') or old['candidates'][0]
    else:
        if (name, label) != ('feeder_1700', 'C2'):
            raise FileNotFoundError(source)
        historical = dict(label='DB+HEEL')
    witness = witness_masses(vessel, historical['label'])
    instance, _ = harness.build(name, vessel, params, label, sum(witness.values()))
    for box in instance.containers:
        box.max_gross_mass_kg = 30480.
    instance = recheck.attach_voyage(instance)
    boxes = {box.id: box for box in instance.containers}
    historical_cargo = sorted((placement.slot, boxes[placement.container_id].gross_mass_kg)
                              for placement in instance.onboard.placements)
    departure = instance.onboard.model_copy(deep=True, update={
        'tank_masses_kg': {**instance.onboard.tank_masses_kg, **witness}})
    row = dict(ship=name, condition=label, historical_label=historical['label'],
        production_label=recipe['ballast_label'], boxes=len(historical_cargo),
        cargo_equal=historical_cargo == sorted(recipe['cargo_cells']),
        departure_tanks_equal=departure.tank_masses_kg == recipe['departure_tank_masses_kg'],
        route_equal=tuple(call.locode for call in instance.rotation.calls) == tuple(recipe['route']),
        phases={}, no_full_checker_acceptance_claim=True)
    if not row['cargo_equal']:
        row['historical_cargo'] = historical_cargo
        row['production_cargo'] = recipe['cargo_cells']
    arrival_liquids = derive_arrival_liquid_state(departure, instance.voyage, vessel=vessel)
    arrival = departure.model_copy(deep=True, update={'tank_masses_kg': arrival_liquids.tank_masses_kg})
    for phase, ship, identity in (('departure', departure, instance.voyage.departure),
                                 ('arrival', arrival, instance.voyage.arrival)):
        # Exactly the actual route's endpoint density; the next-call port does
        # not borrow this departure berth's limits or environment identity.
        port = instance.port.model_copy(update={
            'locode': identity.locode, 'water_density_kg_m3': identity.water_density_kg_m3,
            **({'max_draught_m': None, 'port_rules': []} if phase == 'arrival' else {})})
        context = instance.model_copy(update={'call_index': identity.call_index, 'port': port, 'onboard': ship})
        began = time.perf_counter()
        actual = _loading_condition(context, ship)
        production = evaluate_manual_condition(vessel, params, recipe, phase)
        expected = dict(gm_m=actual.gm_m, mass_kg=actual.mass_kg, lcg_m=actual.lcg_m,
                        tcg_m=actual.tcg_m, kg_m=actual.kg_m, draught_aft_m=actual.t_aft_m,
                        draught_fwd_m=actual.t_fwd_m, list_rad=actual.heel_rad)
        errors = {key: production[key]-value for key, value in expected.items()}
        matched = all(isclose(production[key], value, rel_tol=0.,
                              abs_tol=1e-6 if key == 'mass_kg' else 1e-7)
                      for key, value in expected.items())
        matched = matched and production['tank_masses_kg'] == ship.tank_masses_kg
        matched = matched and production['locode'] == identity.locode
        matched = matched and production['water_density_kg_m3'] == identity.water_density_kg_m3
        row['phases'][phase] = dict(matched=matched, errors=errors, actual=expected,
            production=production, condition_identity=identity.model_dump(mode='json'),
            elapsed_s=time.perf_counter()-began)
        print(json.dumps(dict(ship=name, condition=label, phase=phase, matched=matched,
            actual_gm_m=actual.gm_m, helper_gm_m=production['gm_m'],
            delta_gm_m=errors['gm_m'], elapsed_s=row['phases'][phase]['elapsed_s'])), flush=True)
    row['matched'] = (row['cargo_equal'] and row['departure_tanks_equal'] and row['route_equal']
                      and all(value['matched'] for value in row['phases'].values()))
    return row


def main():
    from stowage.vessel.fleet import load_ship_params, load_vessel
    from stowage.vessel.fleet_manual import standard_manual_conditions

    parser = argparse.ArgumentParser()
    parser.add_argument('--ships', nargs='+', default=['feeder_500', 'feeder_1000', 'feeder_1700'])
    parser.add_argument('--conditions', nargs='+', default=['C1', 'C2', 'C3'])
    parser.add_argument('--out', type=Path, default=HERE/'d56-verify-results.json')
    args = parser.parse_args()
    before = snapshot()
    started = time.perf_counter()
    result = dict(rows=[], vessel_load_s={}, source_before=before, completed=False,
                  no_full_checker_acceptance_claim=True)
    for name in args.ships:
        began = time.perf_counter()
        vessel, params = load_vessel(name), load_ship_params(name)
        result['vessel_load_s'][name] = time.perf_counter()-began
        recipes = {recipe['condition']: recipe for recipe in standard_manual_conditions(vessel, params)}
        for label in args.conditions:
            result['rows'].append(check_case(vessel, params, recipes[label]))
            result['elapsed_s'] = time.perf_counter()-started
            result['peak_rss_gb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9
            args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    result['source_after'] = snapshot()
    result['source_drift'] = sorted(key for key in before.keys() | result['source_after'].keys()
                                  if before.get(key) != result['source_after'].get(key))
    result['completed'] = True
    result['matched'] = not result['source_drift'] and all(row['matched'] for row in result['rows'])
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(completed=True, matched=result['matched'], cases=len(result['rows']),
                         phases=2*len(result['rows']), elapsed_s=result['elapsed_s'],
                         source_drift=result['source_drift'])), flush=True)
    if not result['matched']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
