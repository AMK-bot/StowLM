"""Qualify observed failure states as calibration evidence, never as test passes.

Default is a dry run. ``--apply`` requires all nine completed production cases
and atomically installs one enriched bank only after every prior identity,
cache certificate, input pin and observation checksum has been checked. Never
run apply while holdouts are active: the policy content hash is their input.
Uncovered observations remain unchanged and explicitly labelled failed tests
that now inform calibration. An independent new-seed holdout is still required.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SHIPS = ('feeder_500', 'feeder_1000', 'feeder_1700')
LEVELS = ('light', 'medium', 'full')
SIDES = ('lower_unsafe', 'upper_unsafe')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def equal(first, second, label):
    from stowage.linear.cache import _canonical
    if _canonical(first) != _canonical(second):
        raise ValueError(f'{label} differs')


def point_reference_transfer(record, count):
    """Direct reference-minus-public gap, not affine-minus-public residual.

    A lower-bound transfer error is core reference > public; an upper-bound
    transfer error is public > core. Maximums are retained per station.
    """
    from stowage.linear.margin_policy import _zeros
    result = _zeros(count)
    public = record.get('public_checker_comparison')
    if public is None:
        return result
    core = dict(public['exact_condition'])
    core['draught_aft_m'] = core['draught_m']+.5*core['trim_m']
    core['draught_forward_m'] = core['draught_m']-.5*core['trim_m']
    actual = public['actual_metrics']
    for side, sign in (('lower_unsafe', 1.), ('upper_unsafe', -1.)):
        for metric in result[side]:
            residual = np.asarray(core[metric])-np.asarray(actual[metric])
            if not np.isfinite(residual).all():
                raise ValueError('nonfinite measured reference-transfer residual')
            result[side][metric] = np.maximum(sign*residual, 0.).tolist()
    return result


def inspect_record(path, bank, expected_policy, snapshot, *, minimum_samples=128):
    from stowage.linear.cache import load_model, load_payload
    from stowage.linear.dependencies import dependency_fingerprint
    from stowage.linear.family_calibration import (
        reference_cache_identity, reference_inputs, _validated_errors,
        _validate_calibration_certificate)
    from stowage.linear.margin_policy import default_reserves, _envelope, _evidence
    from stowage.vessel.fleet import load_ship_params
    raw = path.read_bytes()
    record = json.loads(raw)
    ship, level = record['ship'], record['level']
    if ship not in SHIPS or level not in LEVELS or record['samples'] < minimum_samples:
        raise ValueError(f'expected production ship/level and >={minimum_samples} measured holdout states')
    if (record.get('physical_inputs_unchanged') is not True or
            record['physical_inputs_start'] != record['physical_inputs_end']):
        raise ValueError('observation inputs changed during measurement')
    expected = dict(coefficient_builder=dependency_fingerprint('coefficient'),
        calibration_builder=dependency_fingerprint('calibration'),
        parameters=snapshot(ship),
        reference=reference_cache_identity(reference_inputs(load_ship_params(ship))),
        margin_policy=expected_policy)
    equal(record['physical_inputs_start'], expected, 'current physical/policy inputs')
    calibration = record['calibration']
    identity = calibration['response_calibration_identity']
    entry = bank['entries'][identity]
    certificate = calibration['margin_policy']
    _evidence(entry['evidence'])
    equal(certificate['evidence'], entry['evidence'], 'prior bank evidence inventory')
    equal(certificate['response_identity'], identity, 'policy response identity')
    equal(calibration['reference_identity'], expected['reference'], 'calibration reference')
    for field, recorded in (('domain', calibration['family_domain']),
                            ('reference_identity', calibration['reference_identity']),
                            ('station_x_m', certificate['station_x_m'])):
        equal(entry[field], recorded, 'bank '+field)
    count = len(entry['station_x_m'])
    proxy = SimpleNamespace(measurement_diagnostics=calibration,
        water_density_kg_m3=certificate['water_density_kg_m3'],
        station_x_m=np.asarray(entry['station_x_m']))
    equal(default_reserves(proxy), record['default_reserves'], 'installed prior reserves')
    coefficient = load_model(calibration['coefficient_family_cache_key'])
    if coefficient is None:
        raise ValueError('missing/corrupt coefficient cache/certificate')
    equal(coefficient.station_x_m, entry['station_x_m'], 'coefficient stations')
    if coefficient.water_density_kg_m3 != certificate['water_density_kg_m3']:
        raise ValueError('coefficient density mismatch')
    payload = load_payload(calibration['family_calibration_cache_key'], namespace='payload')
    if payload is None:
        raise ValueError('missing/corrupt family calibration cache')
    errors = _validated_errors(payload['errors'], count)
    cached = payload['diagnostics']
    _validate_calibration_certificate(payload['certificate'], calibration['family_calibration_cache_key'], errors, cached)
    for field in ('signed_errors', 'response_calibration_identity', 'family_domain', 'reference_identity'):
        equal(cached[field], calibration[field], 'cached family '+field)
    physical = _envelope(record['diagnostics']['signed_errors'], count, name='fresh holdout')
    percentiles = _envelope(record['diagnostics']['signed_errors']['p99'], count, name='fresh holdout p99')
    if any(np.any(percentiles[side][metric] > value) for side in SIDES
           for metric, value in physical[side].items()):
        raise ValueError('holdout p99 exceeds measured maximum')
    if record['diagnostics']['samples'] != record['samples']:
        raise ValueError('measured sample count mismatch')
    # Independently reproduce stored statuses. A legitimate false result is
    # allowed: its measurements become calibration, not a claimed test pass.
    excess = {side: {metric: np.maximum(np.asarray(value)-np.asarray(record['default_reserves'][side][metric]), 0.).tolist()
                    for metric, value in physical[side].items() if metric != 'gm_m' or side == 'lower_unsafe'}
              for side in SIDES}
    equal(excess, record['unsafe_excess_over_default_reserves'], 'recorded unsafe excess')
    covered = all(np.max(value) == 0 for items in excess.values() for value in items.values())
    if record['all_measured_unsafe_errors_covered'] is not covered:
        raise ValueError('recorded coverage status inconsistent with measurements')
    # An exhausted empirical margin makes the old default infeasible, but
    # does not invalidate the sampled physics. Retain that completeness loss
    # as an original failure; enrichment must never relabel it a test pass.
    structurally_feasible = not (record['empty_default_intervals'] or record['impossible_default_constant_rows'])
    if record.get('default_structurally_feasible') is not structurally_feasible:
        raise ValueError('recorded structural feasibility status inconsistent')
    public = record.get('public_checker_comparison')
    if level == 'light' and public is None:
        raise ValueError('light production case lacks public-reference evidence')
    if public:
        helper = runpy.run_path(str(HERE/'d30_validate.py'))['public_signed_evidence']
        recalculated = helper(public['captured_affine_prediction'], public['stability_metrics'],
            public['strength_metrics'], entry['station_x_m'], record['default_reserves'],
            float(coefficient.gm_min_m))
        for field, value in recalculated.items():
            equal(value, public[field], 'public '+field)
        public_covered = all(np.max(value) == 0 for value in recalculated['unsafe_excess_over_default_reserves'].values())
        if record['captured_public_residuals_covered'] is not public_covered:
            raise ValueError('public coverage status inconsistent')
    transfer = _envelope(point_reference_transfer(record, count), count, name='fresh reference transfer')
    return record, identity, physical, transfer, hashlib.sha256(raw).hexdigest()


def main():
    from stowage.linear.margin_policy import policy_inputs, _envelope
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=HERE/'signed-family/d30/fresh')
    parser.add_argument('--bank', type=Path, default=ROOT/'src/stowage/linear/margin_policy_data.json')
    parser.add_argument('--output', type=Path, default=HERE/'signed-family/d30/resume/enrichment-dry-run.json')
    parser.add_argument('--apply', action='store_true', help='atomic bank replacement; all nine completed cases required')
    parser.add_argument('--minimum-samples', type=int, choices=(64, 128), default=128,
                        help='default128;64 must be explicitly selected for the second measured population')
    args = parser.parse_args()
    before = sha(args.bank)
    expected_policy = policy_inputs()
    if expected_policy['bank_sha256'] != before:
        raise ValueError('--bank must be the current production bank used by policy_inputs')
    bank = json.loads(args.bank.read_text())
    updated = copy.deepcopy(bank)
    snapshot = runpy.run_path(str(HERE/'narrow.py'))['input_snapshot']
    cases, changes, evidence = {}, [], []
    physical_inputs = {}
    for path in sorted(args.input.rglob('*.json')):
        candidate = json.loads(path.read_text())
        if not isinstance(candidate, dict) or candidate.get('ship') not in SHIPS or candidate.get('level') not in LEVELS or 'diagnostics' not in candidate:
            continue
        record, identity, physical, transfer, digest = inspect_record(path, bank, expected_policy, snapshot,
                                                                    minimum_samples=args.minimum_samples)
        previous = physical_inputs.setdefault(record['ship'], record['physical_inputs_start'])
        equal(previous, record['physical_inputs_start'], 'same-ship observation inputs')
        case = record['ship']+'/'+record['level']
        if case in cases:
            raise ValueError('duplicate case: '+case)
        entry = updated['entries'][identity]
        count = len(entry['station_x_m'])
        old_physical = _envelope(entry['physical_unsafe'], count, name='prior physical')
        old_transfer = _envelope(entry['reference_transfer'], count, name='prior transfer')
        for label, old, measured in (('physical_unsafe', old_physical, physical),
                                     ('reference_transfer', old_transfer, transfer)):
            for side in SIDES:
                for metric, values in measured[side].items():
                    merged = np.maximum(old[side][metric], values)
                    entry[label][side][metric] = merged.tolist()
                    increase = merged-old[side][metric]
                    if np.any(increase > 0):
                        changes.append(dict(case=case, kind=label, side=side, metric=metric,
                            max_increase=float(np.max(increase)), changed_entries=int(np.count_nonzero(increase))))
        evidence_path = str(path.resolve().relative_to(ROOT))
        pin = dict(path=evidence_path, sha256=digest,
            role='calibration enrichment from completed holdout; original pass/fail status retained',
            original_heldout_covered=record['all_measured_unsafe_errors_covered'],
            original_public_covered=record['captured_public_residuals_covered'],
            original_structurally_feasible=record['default_structurally_feasible'],
            original_empty_intervals=record['empty_default_intervals'],
            original_impossible_constant_rows=record['impossible_default_constant_rows'])
        if not any(item['path'] == pin['path'] and item['sha256'] == digest for item in entry['evidence']):
            entry['evidence'].append(pin)
        evidence.append(pin)
        cases[case] = dict(path=evidence_path, sha256=digest,
            original_covered=record['all_measured_unsafe_errors_covered'],
            original_structurally_feasible=record['default_structurally_feasible'])
    missing = [ship+'/'+level for ship in SHIPS for level in LEVELS if ship+'/'+level not in cases]
    if not cases:
        raise ValueError('no completed production holdout cases')
    if args.apply and missing:
        raise ValueError('atomic apply requires all nine cases: '+', '.join(missing))
    result = dict(mode='apply' if args.apply else 'dry_run', prior_bank_sha256=before,
        minimum_samples=args.minimum_samples,
        cases=cases, missing_cases=missing, changes=changes,
        status='observations validated as calibration evidence; independent new-seed validation required',
        reserve_formula='1.1 * (max(family unsafe, physical unsafe) + reference transfer)',
        warning='Original failed holdouts are immutable; enrichment does not turn them into independent passes.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.apply:
        # Recheck every original observation and production bank immediately
        # before replacement, preventing a mixed-epoch partial installation.
        for pin in evidence:
            if sha(ROOT/pin['path']) != pin['sha256']:
                raise ValueError('observation changed before apply')
        if sha(args.bank) != before or policy_inputs() != expected_policy:
            raise ValueError('policy bank changed during qualification')
        content = (json.dumps(updated, indent=2, allow_nan=False)+'\n').encode()
        with tempfile.NamedTemporaryFile(dir=args.bank.parent, prefix='.margin-enrichment-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        try:
            if sha(args.bank) != before:
                raise ValueError('policy bank changed before atomic installation')
            # Qualification may have overlapped another owner's physics edit.
            # Recheck numerical inputs immediately before installation, rather
            # than relying only on the last observation's earlier inspection.
            from stowage.linear.dependencies import dependency_fingerprint
            from stowage.linear.family_calibration import reference_cache_identity, reference_inputs
            from stowage.vessel.fleet import load_ship_params
            for ship, expected in physical_inputs.items():
                current = dict(coefficient_builder=dependency_fingerprint('coefficient'),
                    calibration_builder=dependency_fingerprint('calibration'),
                    parameters=snapshot(ship),
                    reference=reference_cache_identity(reference_inputs(load_ship_params(ship))),
                    margin_policy=policy_inputs())
                equal(current, expected, 'physics inputs before atomic installation')
            os.replace(temporary, args.bank)
        finally:
            temporary.unlink(missing_ok=True)
        result['installed_bank_sha256'] = sha(args.bank)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(mode=result['mode'], cases=len(cases), missing=len(missing), changes=changes)))


if __name__ == '__main__':
    main()
