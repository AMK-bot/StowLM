"""Gate every fresh density1000 ordinary/edge/reviewer residual.

Reuse the original N1 validators and residual comparison without changing
reserves. Historical density1025 observations remain separate evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import time

from profile_run import adapter_pin, install, load_driver

HERE = Path(__file__).resolve().parent
OWNER = HERE.parent
ORIGINAL = OWNER/'production-1028b'/'claude-prior-holdout-gate.py'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def profile(record):
    if record.get('qualification_fixture_profile') != {
            'water_density_kg_m3': 1000., 'adapter': adapter_pin()}:
        raise ValueError('fresh observation lacks the exact frozen density1000 profile')


def main():
    import stowage.linear.margin_policy as policy

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, default=HERE/'ordinary')
    parser.add_argument('--output', type=Path, default=HERE/'candidate-residual-gate.json')
    parser.add_argument('--additional-fresh-population', type=Path, action='append', default=[],
                        help='additional newly measured density1000 EDGE_KIND diagnostic')
    args = parser.parse_args()
    install()
    start = time.monotonic()
    original = module('freshwater_original_residual_arithmetic', ORIGINAL)
    promote = module('freshwater_gate_promote', OWNER/'promote.py')
    necessary = module('freshwater_gate_necessary', OWNER/'necessary.py')
    before_bank = promote.pin(args.bank)
    bank = promote.read(args.bank)
    driver = load_driver()
    cases = []
    additional_seen = set()
    for ship in promote.SHIPS:
        family, instance, _, diagnostics, before = driver.prepare(ship, args.prepared)
        if family.template.water_density_kg_m3 != 1000.:
            raise ValueError('residual gate built a non-freshwater response')
        raw = promote.read(args.prepared/ship/'raw.json')
        profile(raw)
        certificate = policy.qualify_policy(family, instance, diagnostics, bank=bank)
        model = replace(family.template,
                        measurement_diagnostics=dict(diagnostics, margin_policy=certificate))
        reserves = policy.default_reserves(model, direction='necessary')
        paths = sorted({path for pattern in ('train-*.json', 'holdout-*.json',
                                             'enrichment*-*.json', 'targeted*-*.json')
                        for path in (args.prepared/ship).glob(pattern)})
        for path in paths:
            record = promote.read(path)
            profile(record)
            if record.get('level') not in promote.LEVELS:
                raise ValueError('unexpected ordinary population level')
            promote.validate_observation(record, raw, ship, record['level'], record['phase'])
            checked = original.compare(necessary, policy, model, reserves, record)
            cases.append(dict(ship=ship, campaign='fresh ordinary', source=promote.pin(path),
                phase=record['phase'], seed=record['seed'], samples=record['samples'], **checked))
        sources = [(phase, HERE/epoch/ship/f'necessary-edges-{phase}.json')
                   for epoch in ('edges-v1', 'edges-v2', 'edges-v3')
                   for phase in ('train', 'holdout')]
        if ship == 'feeder_500':
            sources.append(('review', HERE/'edges-v3'/ship/'necessary-review.json'))
        for path in args.additional_fresh_population:
            if promote.read(path).get('ship') == ship:
                sources.append(('additional', path))
                additional_seen.add(path.resolve())
        for phase, path in sources:
            record = promote.read(path)
            profile(record)
            if phase == 'additional':
                # Optional controlled diagnostics must already use the same
                # explicit physical edge schema; no coercion or relabelling.
                reference = promote.read(necessary._checked_pin(
                    promote, record['reference_transfer_source']))
                transfer = necessary.reference_optimistic(reference, len(model.station_x_m))
                normalized = necessary.edge_population(record, raw, model, transfer=transfer)
                pins = [promote.pin(path)]
            else:
                record, normalized, pins = original.normalize(
                    necessary, promote, path, phase, raw, model)
            checked = original.compare(necessary, policy, model, reserves, normalized)
            cases.append(dict(ship=ship, campaign='fresh edge/diagnostic',
                phase=phase, seed=record['seed'], samples=normalized['samples'],
                attempts=record['attempts'], source_pins=pins, **checked))
        if driver.snapshot(ship, family.parameters) != before:
            raise ValueError('numerical inputs changed during fresh residual gate')
    if additional_seen != {path.resolve() for path in args.additional_fresh_population}:
        raise ValueError('additional fresh population has an unknown ship')
    if promote.pin(args.bank) != before_bank:
        raise ValueError('candidate bank changed during residual gate')
    result = dict(passed=all(case['passed'] for case in cases), cases=cases,
        bank=before_bank, qualification_profile=dict(water_density_kg_m3=1000., adapter=adapter_pin()),
        source_pins=[promote.pin(ORIGINAL), promote.pin(__file__), adapter_pin()],
        samples=sum(case['samples'] for case in cases),
        comparisons=sum(case['comparisons'] for case in cases),
        elapsed_s=time.monotonic()-start,
        historical_density1025='Retained original observations and canonical bank; never relabelled1000.',
        warning='Finite observed residual coverage, not a uniform proof or full-checker plan approval.')
    promote.write(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != 'cases'}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
