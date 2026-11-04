"""Independently verify restored manual input changes and D56 consumer GMs.

No full-checker acceptance is inferred here: current-results contains those
verdicts. This compares the declared current instances with the unchanged
historical cargo builder and actual visibility/lashing loading-condition path.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import math
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import recheck


def main():
    from regression import witness_masses
    from stowage.checker.visibility import _loading_condition
    from stowage.model import Instance, derive_arrival_liquid_state
    from stowage.vessel.fleet import load_ship_params

    began = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', type=Path, default=HERE/'current-results')
    parser.add_argument('--out', type=Path, default=HERE/'verify-changes.json')
    args = parser.parse_args()
    harness = recheck._load_harness()
    summary = json.loads((args.results/'summary.json').read_text())
    assert summary['passed'] == summary['completed'] == summary['total'] == 9
    rows = []
    source_drift = set()
    for saved in summary['rows']:
        name, condition = saved['ship'], saved['condition']
        folder = args.results/f'{name}_{condition}'
        instance = Instance.model_validate_json((folder/'instance.json').read_text())
        vessel = instance.vessel
        for rel, digest in saved['input_identity']['files'].items():
            if hashlib.sha256((recheck.ROOT/rel).read_bytes()).hexdigest() != digest:
                source_drift.add(rel)
        source = recheck.REVIEW/f'search_{name}_{condition}.json'
        if source.exists():
            old = json.loads(source.read_text())
            label = (old.get('witness') or old['candidates'][0])['label']
        else:
            assert (name, condition) == ('feeder_1700', 'C2')
            label = 'DB+HEEL'
        old_ballast = witness_masses(vessel, label)
        old, _ = harness.build(name, vessel, load_ship_params(name), condition, sum(old_ballast.values()))
        old_boxes = {box.id: box for box in old.containers}
        new_boxes = {box.id: box for box in instance.containers}
        before = sorted((p.slot, old_boxes[p.container_id].gross_mass_kg) for p in old.onboard.placements)
        after = sorted((p.slot, new_boxes[p.container_id].gross_mass_kg) for p in instance.onboard.placements)
        correction = (name, condition) == ('feeder_500', 'C2')
        if correction:
            assert len(before) == 516 and len(after) == 252
            assert {mass for _, mass in before} == {8800.}
            assert {mass for _, mass in after} == {18000.}
            assert sum(mass for _, mass in before)-sum(mass for _, mass in after) == 4800.
        else:
            assert before == after, (name, condition, 'cargo changed')
        fixed_ids = [tank.id for tank in vessel.tanks if not tank.adjustable]
        assert {tid:old.onboard.tank_masses_kg[tid] for tid in fixed_ids} == {
            tid:instance.onboard.tank_masses_kg[tid] for tid in fixed_ids}
        assert tuple(call.locode for call in old.rotation.calls) == tuple(call.locode for call in instance.rotation.calls)
        assert old.rule_overrides == instance.rule_overrides
        audit = json.loads(vessel.hull.max_gm_lashing_m_provenance.source)
        assert vessel.hull.max_gm_lashing_m == 1.1*max(row['gm_m'] for row in audit['conditions'])
        departure = instance.onboard
        arrival = departure.model_copy(update={'tank_masses_kg':derive_arrival_liquid_state(
            departure, instance.voyage, vessel=vessel).tank_masses_kg})
        comparisons = []
        for ship, identity in ((departure, instance.voyage.departure), (arrival, instance.voyage.arrival)):
            port = instance.port.model_copy(update={'locode':identity.locode,
                'water_density_kg_m3':identity.water_density_kg_m3})
            context = instance.model_copy(update={'port':port,'onboard':ship,'call_index':identity.call_index})
            consumer = _loading_condition(context, ship)
            declared = next(row for row in audit['conditions']
                            if row['condition']==condition and row['phase']==identity.phase)
            assert math.isclose(consumer.gm_m,declared['gm_m'],rel_tol=0.,abs_tol=1e-7)
            comparisons.append(dict(phase=identity.phase,actual_gm_m=consumer.gm_m,
                declared_gm_m=declared['gm_m'],delta_gm_m=consumer.gm_m-declared['gm_m']))
        rows.append(dict(ship=name,condition=condition,cargo_exactly_preserved=not correction,
            corrected_cargo=correction,old_cargo_mass_kg=sum(mass for _,mass in before),
            new_cargo_mass_kg=sum(mass for _,mass in after),consumables_exactly_preserved=True,
            route_and_rule_policy_exactly_preserved=True,gm_comparisons=comparisons,
            max_gm_lashing_m=vessel.hull.max_gm_lashing_m,governing=audit['governing']))
    result = dict(passed=not source_drift,rows=rows,elapsed_s=time.perf_counter()-began,
                  source_drift=sorted(source_drift),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    assert not source_drift, sorted(source_drift)
    print(json.dumps(dict(passed=True,rows=len(rows),unchanged_cargo=8,corrected_cargo=1,
                         phases=18,elapsed_s=result['elapsed_s'])),flush=True)


if __name__ == '__main__':
    main()
