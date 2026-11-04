"""Inspect actual saved voyage response profiles, then replay promoted public rows.

The immutable saved instances and their declared voyage are preserved. Diagnose
builds coefficients only; validate additionally requires the production bank
through the public builder and both explicit margin directions.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def pin(path):
    path = Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('diagnose', 'validate'), required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = (args.output or HERE / ('saved-contexts-' + args.phase + '.json')).resolve()
    if not output.is_relative_to(HERE) or output.exists():
        parser.error('choose a new output path inside production-1028c')
    started = time.monotonic()
    from stowage.io import load_instance
    from stowage.linear import build_linear_model, linear_constraints
    from stowage.linear.bands import band_for_instance
    from stowage.linear.cache import response_calibration_identity
    from stowage.linear.dependencies import dependency_fingerprint
    from stowage.linear.family_calibration import domain_for
    from stowage.linear.physics import _prepared_params_for
    from stowage.linear.reusable import coefficient_family
    from stowage.solver.construct_physics import _contexts

    bank_path = ROOT / 'src/stowage/linear/margin_policy_data.json'
    bank_pin = pin(bank_path)
    bank = json.loads(bank_path.read_text())
    fingerprints = {name: dependency_fingerprint(name) for name in ('coefficient', 'calibration', 'liquid_reference')}
    source = pin(Path(__file__))
    audit_path = HERE / 'production-audit.json'
    audit_pin = pin(audit_path)
    cases = []
    for captured in json.loads(audit_path.read_text())['cases']:
        input_path = ROOT / captured['saved_input']['path']
        if pin(input_path) != captured['saved_input']:
            raise ValueError('saved input bytes differ from the pinned production audit')
        instance = load_instance(input_path)
        instance_digest = digest(instance.model_dump(mode='json'))
        for phase, context, fraction in _contexts(instance):
            began = time.monotonic()
            record = dict(case=captured['id'], phase=phase, remaining_consumable_fraction=fraction,
                input=pin(input_path), instance_sha256=instance_digest,
                context_sha256=digest(context.model_dump(mode='json')),
                vessel_id=context.vessel.id, slice_of=context.vessel.slice_of,
                water_density_kg_m3=context.port.water_density_kg_m3,
                port_locode=context.port.locode, cell_count=len(context.vessel.cells))
            try:
                parameter_name, parameters = _prepared_params_for(context)
                family = coefficient_family(context, parameters)
                identity = response_calibration_identity(family)
                record.update(parameter_name=parameter_name,
                    response_calibration_identity=identity, coefficient_family_cache_key=family.cache_key,
                    family_band=asdict(family.template.band), instance_band=asdict(band_for_instance(context)),
                    family_domain=domain_for(family, context),
                    bank_entry_present=identity in bank['entries'])
                if args.phase == 'validate':
                    model = build_linear_model(context)
                    certificate = model.measurement_diagnostics['margin_policy']
                    if certificate.get('production_usable') is not True:
                        raise ValueError('saved context production policy is not usable')
                    if model.measurement_diagnostics['response_calibration_identity'] != identity:
                        raise ValueError('public builder used another response profile')
                    record['public_certificate_sha256'] = certificate['sha256']
                    record['production_usable'] = True
                    record['public_band'] = asdict(model.band)
                    record['rows'] = {}
                    for direction in ('necessary', 'conservative'):
                        rows = linear_constraints(model, direction=direction)
                        record['rows'][direction] = dict(count=len(rows.names),
                            names_sha256=digest(rows.names), a_cell_shape=list(rows.a_cell.shape),
                            a_tank_shape=list(rows.a_tank.shape))
                record['passed'] = True
            except Exception as error:
                record.update(passed=False, error_type=type(error).__name__, error=str(error))
            record['seconds'] = time.monotonic() - began
            cases.append(record)
            print(json.dumps({key: record.get(key) for key in ('case', 'phase', 'water_density_kg_m3',
                'response_calibration_identity', 'bank_entry_present', 'passed', 'error', 'seconds')}), flush=True)
        if digest(instance.model_dump(mode='json')) != instance_digest or pin(input_path) != captured['saved_input']:
            raise ValueError('original saved instance was mutated during context inspection')
    after = {name: dependency_fingerprint(name) for name in fingerprints}
    stable = after == fingerprints and pin(bank_path) == bank_pin and pin(audit_path) == audit_pin and pin(Path(__file__)) == source
    result = dict(format=1, phase=args.phase, pid=os.getpid(), started_source=source,
        audit_input=audit_pin, bank=bank_pin, dependency_fingerprints=fingerprints,
        inputs_and_sources_unchanged=stable, cases=cases,
        passed=stable and all(row['passed'] for row in cases),
        all_profiles_present=all(row.get('bank_entry_present') for row in cases),
        seconds=time.monotonic()-started, peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        completed_utc=datetime.now(timezone.utc).isoformat(),
        warning='Public linear-row availability only; no plan or full-checker acceptance is claimed.')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary=output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    temporary.replace(output)
    print(json.dumps(dict(output=str(output.relative_to(ROOT)), passed=result['passed'],
        all_profiles_present=result['all_profiles_present'], seconds=result['seconds'],
        peak_rss_bytes=result['peak_rss_bytes'])), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
