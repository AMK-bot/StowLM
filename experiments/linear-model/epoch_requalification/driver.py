"""Resumable, explicitly offline qualification of a changed linear epoch.

Each hull's response calibration is measured once. Its private research path
does not make a production model usable: promotion requires separate physical
training and untouched holdout populations and the boundary LIST experiment.
All residuals are affine-minus-exact; both unsafe directions and safe bias are
retained. Source: one-sided residual validation, not a uniform error proof.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import runpy
import time

import numpy as np

HERE = Path(__file__).resolve().parent
HELPER = runpy.run_path(str(HERE.parent / 'narrow.py'))
VALIDATE = runpy.run_path(str(HERE.parent / 'd30_validate.py'))
serializable = VALIDATE['serializable']


_INSTANCE_INPUTS = {}

def cargo_instance(ship, level):
    """Reuse immutable experiment inputs while their physical content is current.

    Whole-vessel loader releases can change for unrelated rules mid-experiment.
    This process-local reuse avoids rebuilding the same light fixture twice;
    every exact case still independently snapshots parameters and reference.
    """
    inputs = HELPER['input_snapshot'](ship)
    key = (ship, level)
    cached = _INSTANCE_INPUTS.get(key)
    if cached is not None and cached[0] == inputs:
        return cached[1]
    result = HELPER['cargo_instance'](ship, level)
    if HELPER['input_snapshot'](ship) != inputs:
        raise ValueError('production inputs changed while preparing cargo fixture')
    _INSTANCE_INPUTS[key] = (inputs, result)
    return result


def write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(serializable(record), indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def snapshot(ship, parameters):
    from stowage.linear.dependencies import dependency_fingerprint
    from stowage.linear.family_calibration import reference_inputs, reference_cache_identity
    return {'parameters': HELPER['input_snapshot'](ship),
            'coefficient_builder': dependency_fingerprint('coefficient'),
            'calibration_builder': dependency_fingerprint('calibration'),
            'reference': reference_cache_identity(reference_inputs(parameters))}


def prepare(ship, output, samples=192):
    """Resume a verified raw checkpoint; never force 192 samples per case."""
    from stowage.linear.reusable import coefficient_family
    from stowage.linear.family_calibration import (calibrated_family, domain_for,
        _validated_errors, _validate_calibration_certificate)
    from stowage.linear.cache import family_calibration_key, response_calibration_identity
    from stowage.linear.physics import _prepared_params_for
    instance, _ = cargo_instance(ship, 'light')
    _, parameters = _prepared_params_for(instance)
    family = coefficient_family(instance, parameters)
    before = snapshot(ship, family.parameters)
    if before['reference'].get('qualified') is not True:
        raise ValueError('production method-1 transfer reference is not qualified')
    domain = domain_for(family, instance)
    key = family_calibration_key(family, before['reference'], domain, samples=samples)
    path = output / ship / 'raw.json'
    if path.exists():
        record = json.loads(path.read_text())
        if record['physical_inputs'] != before or record['key'] != key:
            raise ValueError(f'stale raw checkpoint: {path}; choose a new output directory')
        diagnostics = record['diagnostics']
        errors = _validated_errors(record['errors'], len(family.template.station_x_m))
        _validate_calibration_certificate(diagnostics['calibration_certificate'], key, errors, diagnostics)
        if diagnostics['response_calibration_identity'] != response_calibration_identity(family):
            raise ValueError('raw checkpoint response mismatch')
    else:
        start = time.perf_counter()
        errors, diagnostics = calibrated_family(family, instance, samples=samples, force_rebuild=True)
        if snapshot(ship, family.parameters) != before:
            raise ValueError('inputs changed during raw calibration')
        record = {'ship': ship, 'key': key, 'physical_inputs': before,
                  'errors': asdict(errors), 'diagnostics': diagnostics,
                  'seconds': time.perf_counter()-start,
                  'production_usable': False}
        write(path, record)
    return family, instance, errors, diagnostics, before


def physical_case(ship, level, phase, output, prepared, *, samples, seed, public=True):
    from stowage.linear import physics
    from stowage.linear.bands import band_for_instance
    from stowage.linear.reusable import coefficient_family, assemble_instance
    family, _, errors, diagnostics, before = prepared
    path = output / ship / f'{phase}-{level}.json'
    if path.exists():
        cached = json.loads(path.read_text())
        if (cached['physical_inputs_start'] != before or cached['samples'] != samples
                or cached['seed'] != seed or not cached['physical_inputs_unchanged']
                or (public and cached.get('public_crosscheck_completed') is not True)):
            raise ValueError(f'stale physical checkpoint: {path}')
        print(json.dumps({'reused': str(path)}), flush=True)
        return cached
    start = time.perf_counter()
    capture_driver_source = Path(__file__).read_bytes()
    capture_driver_sha256 = hashlib.sha256(capture_driver_source).hexdigest()
    from stowage.linear.dependencies import _semantic_hash
    capture_driver_semantic_hash = _semantic_hash(capture_driver_source, None)
    instance, manifest = cargo_instance(ship, level)
    # Immutable prepared parameters retain their validated canonical bytes.
    # Every case snapshot still rereads actual parameter/offset/table content.
    current_family = coefficient_family(instance, family.parameters)
    if current_family.cache_key != family.cache_key:
        raise ValueError('physical case changed hull/slice/tank coefficient geometry')
    band = band_for_instance(instance)
    model = assemble_instance(current_family, instance, band, errors=errors, diagnostics=diagnostics)
    captured = []
    sample_states = []
    original = physics.exact_condition
    def capture(*args, **kwargs):
        exact = original(*args, **kwargs)
        if (band.displacement_min_kg <= exact['displacement_kg'] <= band.displacement_max_kg
                and band.draught_min_m <= exact['draught_m'] <= band.draught_max_m
                and abs(exact['trim_m']) <= band.max_abs_trim_m
                and abs(exact['list_rad']) <= band.max_abs_heel_rad):
            cell, tank = args[2].copy(), args[3].copy()
            affine = physics._linear_arrays(model, cell, tank, instance=instance, solver=True)
            mass = model.const_mass_kg+sum(cell)+sum(tank)
            my = model.const_moment_y_kgm+model.cell_tcg_m@cell+model.tank_tcg_m@tank
            restoring = mass*affine['gm_m']
            sample_states.append({'mass_kg': mass, 'affine_restoring_kgm': restoring,
                'transverse_moment_kgm': my, 'affine_gm_m': affine['gm_m'],
                # Preserve the measured reference return itself. Future row
                # audits must never reconstruct GM from the affine predictor
                # or relabel historical states which omitted this quantity.
                'exact_gm_m': exact['gm_m'],
                'exact_displacement_kg': exact['displacement_kg'],
                'exact_list_rad': exact['list_rad'],
                'affine_trim_m': affine['trim_m'], 'exact_trim_m': exact['trim_m'],
                'affine_draught_m': affine['draught_m'], 'exact_draught_m': exact['draught_m'],
                'tank_masses_kg': tank.tolist(), 'cell_masses_kg': cell.tolist(),
                'cell_vcg_m': kwargs['cell_vcg_m'].tolist(),
                'affine_sf_n': np.asarray(affine['sf_n']).tolist(),
                'exact_sf_n': np.asarray(exact['sf_n']).tolist(),
                'affine_bm_nm': np.asarray(affine['bm_nm']).tolist(),
                'exact_bm_nm': np.asarray(exact['bm_nm']).tolist()})
            if not captured:
                captured.append((cell, tank, kwargs['cell_vcg_m'].copy(), exact, affine))
        return exact
    physics.exact_condition = capture
    try:
        measured, measured_diagnostics = physics.measure_errors(model, instance, samples=samples, seed=seed)
    finally:
        physics.exact_condition = original
    measured_at = time.perf_counter()
    comparison = None
    public_completed = False
    if public and captured:
        comparison = HELPER['public_crosscheck'](model, instance, captured[0][:4])
        comparison['captured_affine_prediction'] = captured[0][4]
        comparison['captured_sample'] = dict(cell_masses_kg=captured[0][0],
            tank_masses_kg=captured[0][1], cell_vcg_m=captured[0][2])
        # Strict numerical equality remains visible as ``passed``. Qualified
        # production KN interpolation can legitimately differ from direct
        # equilibrium: retain that signed transfer, rather than relaxing its
        # equality tolerance or confusing it with a failed checker dispatch.
        from stowage.linear.margin_policy import _zeros
        if 'stability_metrics' in comparison and 'strength_metrics' in comparison:
            comparison.update(VALIDATE['public_signed_evidence'](
                captured[0][4], comparison['stability_metrics'],
                comparison['strength_metrics'], model.station_x_m,
                _zeros(len(model.station_x_m)), model.gm_min_m))
            public_completed = True
    after = snapshot(ship, family.parameters)
    if _semantic_hash(Path(__file__).read_bytes(), None) != capture_driver_semantic_hash:
        raise ValueError('physical capture driver changed during measurement')
    record = {'ship': ship, 'level': level, 'phase': phase, 'samples': samples, 'seed': seed,
        'band': asdict(band), 'manifest': manifest, 'pid': os.getpid(),
        'physical_inputs_start': before, 'physical_inputs_end': after,
        'physical_inputs_unchanged': before == after,
        'calibration': diagnostics, 'measured_absolute_errors': asdict(measured),
        'diagnostics': measured_diagnostics, 'public_checker_comparison': comparison,
        'public_crosscheck_passed': comparison.get('passed') if comparison else None,
        'public_crosscheck_completed': public_completed,
        'list_row_samples': sample_states,
        'sample_state_exact_source': {
            'format': 1,
            'origin': 'exact_condition return captured during this fresh physical measurement',
            'driver_sha256': capture_driver_sha256,
            'recorded_quantities': ['gm_m', 'displacement_kg', 'list_rad', 'trim_m',
                                    'draught_m', 'sf_n', 'bm_nm']},
        'station_x_m': model.station_x_m,
        'timings_seconds': {'heldout': measured_at-start,
                            'public_check': time.perf_counter()-measured_at,
                            'total': time.perf_counter()-start},
        'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
        'warning': 'Offline measured evidence; not production qualified until independent promotion.'}
    if public_completed:
        transfer = runpy.run_path(str(HERE.parent/'enrich_policy_bank.py'))
        record['reference_transfer'] = transfer['point_reference_transfer'](
            record, len(model.station_x_m))
    write(path, record)
    if before != after:
        raise ValueError('physical inputs changed during qualification')
    if public and not public_completed:
        raise ValueError(f'public checker did not return complete finite metrics: {path}')
    print(json.dumps({'built': str(path), 'seconds': record['timings_seconds']['total']}), flush=True)
    return record


def run_ship(ship, *, output, phase='all', calibration_samples=192,
             physical_samples=128, holdout_samples=64, train_seed=3793,
             holdout_seed=3794, public=True):
    prepared = prepare(ship, output, calibration_samples)
    phases = ('train', 'holdout') if phase == 'all' else (phase,)
    for current in phases:
        if current not in ('train', 'holdout'):
            continue
        for level in ('light', 'medium', 'full'):
            physical_case(ship, level, current, output, prepared,
                samples=physical_samples if current == 'train' else holdout_samples,
                seed=train_seed if current == 'train' else holdout_seed, public=public)
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ship', required=True, choices=('feeder_500','feeder_1000','feeder_1700'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--phase', choices=('raw','train','holdout','all'), default='all')
    parser.add_argument('--calibration-samples', type=int, default=192)
    parser.add_argument('--samples', type=int, default=128)
    parser.add_argument('--holdout-samples', type=int, default=64)
    parser.add_argument('--train-seed', type=int, default=3793)
    parser.add_argument('--holdout-seed', type=int, default=3794)
    parser.add_argument('--no-public', action='store_true')
    args = parser.parse_args()
    run_ship(args.ship, output=args.output, phase=args.phase,
        calibration_samples=args.calibration_samples, physical_samples=args.samples,
        holdout_samples=args.holdout_samples, train_seed=args.train_seed,
        holdout_seed=args.holdout_seed, public=not args.no_public)


if __name__ == '__main__':
    main()
