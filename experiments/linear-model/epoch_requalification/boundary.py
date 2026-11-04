"""Offline empirical LIST boundary qualification with independent branch checks.

The affine coordinate is nominal-tank My, so exact capacity is anchored by the
*added* transverse moment from boundary_capacity. Independent unrestricted
liquid/hull roots verify that increasing outward moment crosses the prescribed
heel on the selected stable branch. Finite sampling is evidence, not a uniform
proof; the exact departure checker remains mandatory. Source: Biran & López-
Pulido (2014), floating-body moment equilibrium, ch. 2–4.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import math

import numpy as np

SIDES = {'lower_unsafe': -1, 'upper_unsafe': 1}


def synthetic_states(family, instance, *, seed, samples):
    """Dry total moments and every tank fill, independent of instance backgrounds."""
    from stowage.model import FixedLoad, ShipState
    from stowage.linear.reusable import assemble_instance
    from stowage.linear.physics import _tank_point, _linear_arrays
    base = family.template
    hull = instance.vessel.hull
    band = base.band
    rng = np.random.default_rng(seed)
    tanks = sorted(instance.vessel.tanks, key=lambda t: t.id)
    empty = instance.model_copy(update={'containers': [], 'load_list': [],
        'discharge_list': [], 'onboard': ShipState(vessel_id=instance.vessel.id)})
    accepted = 0
    for attempt in range(max(400, samples*60)):
        mass = float(rng.uniform(band.displacement_min_kg, band.displacement_max_kg))
        available = mass-hull.lightship_mass_kg
        if available <= 1:
            continue
        fills = rng.random(len(tanks))
        if attempt % 8 == 0:
            fills[:] = 0
        elif attempt % 9 == 0:
            fills[:] = 1
        values = fills*base.tank_capacity_kg
        if values.sum() >= available*.98:
            values *= float(rng.uniform(.1, .98))*available/max(float(values.sum()), 1.)
        dry = available-values.sum()
        # Explicitly include low GM and target operating GM, rather than mostly
        # highly stable low-KG states. This does not restrict the stated family
        # domain: it deliberately samples its solver-eligible portion.
        max_gm = (base.km_line[0]+base.km_line[1]*mass-base.tank_fsm_max_kgm.sum())/mass
        gm_lo = max(base.gm_min_m, .01)
        if max_gm <= gm_lo:
            continue
        gm_hi = min(max_gm, max(3., gm_lo+1.)) if attempt % 2 else max_gm
        gm = float(rng.uniform(gm_lo, gm_hi))
        if attempt % 7 == 0:
            gm = gm_lo*(1+1e-6)
        kg = max_gm-gm
        trim = float(rng.uniform(-.9, .9)*band.max_abs_trim_m)
        mx = (trim-base.trim_line[0]-base.trim_line[2]*mass)/base.trim_line[1]
        # Zero nominal My is merely the anchor. Boundary capacity then solves
        # the exact additional dry My at either operating limit.
        target = np.array([mx, 0., kg*mass])
        arms = np.array([base.tank_lcg_m, base.tank_tcg_m, base.tank_vcg_m]).T.copy()
        for i, tank in enumerate(tanks):
            if not tank.adjustable:
                arms[i] = _tank_point(tank, values[i])
        point_moment = target-values@arms-hull.lightship_mass_kg*np.array([
            hull.lightship_lcg_m, hull.lightship_tcg_m, hull.lightship_vcg_m])
        point = FixedLoad(x_m=float(point_moment[0]/dry), y_m=float(point_moment[1]/dry),
                          z_m=float(point_moment[2]/dry), mass_kg=float(dry))
        state = empty.model_copy(update={
            'vessel': empty.vessel.model_copy(update={'fixed_loads': [point]}),
            'onboard': ShipState(vessel_id=empty.vessel.id, tank_masses_kg={
                t.id: float(values[i]) for i, t in enumerate(tanks) if not t.adjustable})})
        current = assemble_instance(family, state, band, errors=base.errors,
                                    diagnostics={'boundary_synthetic': True})
        adjustable = values*base.tank_adjustable
        affine = _linear_arrays(current, np.zeros(len(base.cell_ids)), adjustable,
                                instance=state, solver=True)
        if affine['gm_m'] < base.gm_min_m:
            continue
        yield current, state, values, affine
        accepted += 1
        if accepted == samples:
            return
    raise ValueError(f'only {accepted}/{samples} GM-eligible boundary states')


def loading(model, instance, values, geometry, *, direct=False):
    from stowage.checker.stability_loading import loading_condition
    from stowage.checker.stability_liquid import attach_tables
    from stowage.model import ShipState
    ship = ShipState(vessel_id=instance.vessel.id, tank_masses_kg={
        t.id: float(values[i]) for i,t in enumerate(sorted(instance.vessel.tanks, key=lambda t:t.id))})
    condition = loading_condition(instance, ship, geometry=geometry,
        ship_params=model._reference_params, free_surface_method=1,
        precomputed_soundings=True, small_tank_exemption=True)
    if not direct:
        condition = attach_tables(condition, model._reference_params,
            vessel_hash=getattr(geometry, '_stability_vessel_hash', None))
    if condition.errors:
        raise ValueError('; '.join(condition.errors))
    return condition


def branch_crossing(geometry, condition, capacity, density, sign, limit):
    """Fresh unrestricted roots on both sides; no reused total-CG caches."""
    from stowage.linear.reference_equilibrium import liquid_equilibrium
    increment = max(1., condition.mass_kg*.0001)
    heels = []
    for direction in (-1, 0, 1):
        shifted = replace(condition, tcg_m=condition.tcg_m +
            (capacity.transverse_moment_shift_kgm+sign*direction*increment)/condition.mass_kg,
            cg_cache={}, upright_cache=dict(condition.upright_cache), liquid_usage={})
        solved = liquid_equilibrium(geometry, shifted, density, initial=capacity.attitude)
        heels.append(float(sign*solved.heel_rad-limit))
    if not (heels[0] < 0 < heels[2] and abs(heels[1]) < 2e-7):
        raise ValueError(f'unsupported or ambiguous boundary equilibrium branch: {heels}')
    return {'outward_heel_offset_rad': heels, 'moment_increment_kgm': increment}


def observe(family, instance, *, samples, seed, branch_checks=True, direct_transfer=True, progress_path=None):
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.linear.boundary_calibration import boundary_capacity
    from stowage.linear.cache import response_calibration_identity
    from stowage.linear.family_calibration import reference_inputs, reference_cache_identity
    geometry = StabilityGeometry(family.parameters)
    limit = min(family.template.max_list_rad, family.template.band.max_abs_heel_rad)
    context = {'response_identity':response_calibration_identity(family),
        'reference':reference_cache_identity(reference_inputs(family.parameters)),
        'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'seed':seed, 'requested':samples, 'branch_checks':branch_checks,
        'direct_transfer':direct_transfer, 'limit_rad':limit}
    observations = []
    rejections = []
    processed = 0
    checkpoint = Path(progress_path) if progress_path is not None else None
    if checkpoint is not None and checkpoint.exists():
        previous = json.loads(checkpoint.read_text())
        if previous['context'] != context:
            raise ValueError(f'stale boundary checkpoint: {checkpoint}')
        observations = previous['observations']
        rejections = previous['rejections']
        processed = previous['processed']
        if len(observations) == samples:
            return observations

    def save():
        if checkpoint is not None:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint.with_suffix(checkpoint.suffix+'.tmp')
            temporary.write_text(json.dumps({'context':context, 'processed':processed,
                'completed':len(observations), 'observations':observations,
                'rejections':rejections}, indent=2, allow_nan=False)+'\n')
            temporary.replace(checkpoint)

    candidates = max(400, samples*60)
    for index, (model, state, values, affine) in enumerate(synthetic_states(
            family, instance, seed=seed, samples=candidates)):
        if index < processed:
            continue
        processed = index+1
        try:
            condition = loading(model, state, values, geometry)
            direct = loading(model, state, values, geometry, direct=True) if direct_transfer else None
            mass = condition.mass_kg
            my = model.const_moment_y_kgm+float(model.tank_tcg_m@(values*model.tank_adjustable))
            restoring = mass*affine['gm_m']
            row = {'sample':index, 'mass_kg':mass, 'affine_gm_m':affine['gm_m'],
                   'trim_m':affine['trim_m'], 'sides':{}}
            for side, sign in SIDES.items():
                capacity = boundary_capacity(geometry, condition, model.water_density_kg_m3, sign*limit)
                attitude = capacity.attitude
                band = model.band
                mean = (attitude.t_aft_m+attitude.t_fwd_m)/2
                if not (band.draught_min_m <= mean <= band.draught_max_m and
                        abs(attitude.t_aft_m-attitude.t_fwd_m) <= band.max_abs_trim_m):
                    raise ValueError('exact boundary falls outside declared draught/trim domain')
                affine_capacity = my+capacity.transverse_moment_shift_kgm
                residual = math.tan(limit)*restoring-sign*affine_capacity
                detail = {'signed_residual_kgm':residual,
                    'unsafe_kgm':max(0., residual), 'safe_gap_kgm':max(0., -residual),
                    'exact_affine_coordinate_capacity_kgm':affine_capacity,
                    'fixed_heel_residual':capacity.residual_norm}
                if branch_checks:
                    detail['branch'] = branch_crossing(geometry, condition, capacity,
                        model.water_density_kg_m3, sign, limit)
                if direct is not None:
                    direct_capacity = boundary_capacity(geometry, direct, model.water_density_kg_m3,
                        sign*limit, initial=capacity.attitude)
                    # Only smaller direct outward capacity threatens safety.
                    transfer = sign*(capacity.transverse_moment_shift_kgm-
                                      direct_capacity.transverse_moment_shift_kgm)
                    detail['reference_transfer_unsafe_kgm'] = max(0., transfer)
                    detail['reference_transfer_safe_kgm'] = max(0., -transfer)
                row['sides'][side] = detail
        except ValueError as exc:
            rejections.append({'sample':index, 'reason':str(exc),
                'mass_kg':float(values.sum()+model.const_mass_kg),
                'affine_gm_m':float(affine['gm_m'])})
            save()
            continue
        observations.append(row)
        save()
        if len(observations) == samples:
            return observations
    raise ValueError(f'only {len(observations)}/{samples} supported boundary states; '
                     f'{len(rejections)} rejections retained in {checkpoint}')


def roundoff_reserve_kgm(family, domain):
    """Explicit arithmetic reserve: 64 eps times a domain moment-scale bound.

    Each capacity subtracts hull/liquid/affine mass moments. The bound includes
    their absolute vertical, transverse, KM and maximum-FSM contributions over
    the declared domain. This is a floating-point allowance, not fitted physics
    error or a relaxed acceptance tolerance; it is charged in the LP reserve.
    Root convergence and independent branch tolerances remain separate checks.
    """
    base = family.template
    mass = max(abs(base.band.displacement_min_kg), abs(base.band.displacement_max_kg))
    a, b = base.km_line
    kg = max(abs(domain['kg_min_m']), abs(domain['kg_max_m']))
    y = max(float(domain['fixed_y_abs_max_m']),
        float(np.max(np.abs(base.cell_tcg_m), initial=0.)),
        float(np.max(np.abs(base.tank_tcg_m), initial=0.)))
    moment_scale = abs(a)+abs(b)*mass+kg*mass+float(np.abs(base.tank_fsm_max_kgm).sum())+y*mass
    if not np.isfinite(moment_scale) or moment_scale <= 0:
        raise ValueError('positive finite family moment bound required')
    return 64*np.finfo(float).eps*moment_scale


def qualification_envelopes(calibration, holdout, *, roundoff_kgm):
    if not np.isfinite(roundoff_kgm) or roundoff_kgm < 0:
        raise ValueError('finite nonnegative numerical reserve required')
    measured = {side:max(row['sides'][side]['unsafe_kgm'] for row in calibration) for side in SIDES}
    physical_transfer = {side:max(row['sides'][side]['reference_transfer_unsafe_kgm']
        for row in calibration) for side in SIDES}
    transfer = {side:physical_transfer[side]+roundoff_kgm for side in SIDES}
    reserves = {side:1.1*(measured[side]+transfer[side]) for side in SIDES}
    # Holdout contributes its measured physical/table residual once. The
    # explicit numerical budget already enters the proposed LP reserve above.
    excess = {side:max(row['sides'][side]['unsafe_kgm']+
        row['sides'][side]['reference_transfer_unsafe_kgm']-reserves[side]
        for row in holdout) for side in SIDES}
    return measured, physical_transfer, transfer, reserves, excess


def bind_builder(record):
    """Attach the boundary primitive's own version, inputs hash and source pin.

    Does not modify measured residuals, raw evidence, seeds or qualification.
    Existing pins remain mandatory and cannot be replaced by this wrapper.
    """
    from copy import deepcopy
    from stowage.linear import boundary_calibration, margin_policy
    result = deepcopy(record)
    primitive = Path(boundary_calibration.__file__).resolve()
    primitive_sha = hashlib.sha256(primitive.read_bytes()).hexdigest()
    pins = result.setdefault('evidence', [])
    for pin in pins:
        path = Path(pin['path'])
        resolved = (margin_policy._ROOT/path).resolve()
        if resolved == primitive and pin['sha256'] != primitive_sha:
            raise ValueError('cannot rebind a changed boundary primitive')
    if not any((margin_policy._ROOT/Path(pin['path'])).resolve() == primitive for pin in pins):
        pins.append({'path':str(primitive), 'sha256':primitive_sha})
    result['builder_version'] = boundary_calibration.BOUNDARY_CALIBRATION_VERSION
    result['builder_inputs_hash'] = margin_policy._boundary_builder_inputs_hash(result)
    return result


def wrap_existing(path, *, ship, driver):
    """Verify unchanged current physics and pinned evidence before metadata binding."""
    from stowage.linear import margin_policy
    path = Path(path)
    original = json.loads(path.read_text())
    margin_policy._evidence(original.get('evidence'))
    family, _, metadata, before = prepare_boundary(ship, driver)
    bound = bind_builder(original)
    margin_policy._boundary_list(bound, family.template,
        response=metadata['response_calibration_identity'],
        reference=metadata['reference_identity'], domain=metadata['family_domain'])
    after = driver.snapshot(ship, family.parameters)
    if before != after:
        raise ValueError('inputs changed while wrapping boundary certificate')
    bound['builder_binding_verification'] = {
        'verified_existing_evidence':True, 'current_physical_inputs_unchanged':True,
        'operation':'metadata-only D30 binding; numerical observations unchanged'}
    driver.write(path, bound)
    return bound


def qualify_boundary(family, instance, *, response_identity, reference_identity,
        domain, calibration_samples=64, holdout_samples=64, calibration_seed=3811,
        holdout_seed=3812, output_path):
    """Measure two independent populations and refuse any positive holdout excess."""
    if calibration_seed == holdout_seed or min(calibration_samples, holdout_samples) <= 0:
        raise ValueError('positive independent populations required')
    path = Path(output_path)
    calibration = observe(family, instance, samples=calibration_samples, seed=calibration_seed,
        progress_path=path.with_name(path.stem+'-calibration-progress.json'))
    holdout = observe(family, instance, samples=holdout_samples, seed=holdout_seed,
        progress_path=path.with_name(path.stem+'-holdout-progress.json'))
    roundoff = roundoff_reserve_kgm(family, domain)
    measured, physical_transfer, transfer, reserves, excess = qualification_envelopes(
        calibration, holdout, roundoff_kgm=roundoff)
    passed = all(value <= 0 for value in excess.values())
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {'format':1, 'calibration_seed':calibration_seed, 'holdout_seed':holdout_seed,
        'calibration':calibration, 'holdout':holdout, 'reserves_kgm':reserves,
        'max_holdout_excess_kgm':excess, 'passed':passed,
        'roundoff_kgm':roundoff, 'physical_reference_transfer_kgm':physical_transfer,
        'warning':'finite empirical qualification; exact checker remains mandatory'}
    path.write_text(json.dumps(raw, indent=2, allow_nan=False)+'\n')
    base = family.template
    record = dict(format=1, response_identity=response_identity,
        reference_identity=reference_identity, domain=domain,
        station_x_m=np.asarray(base.station_x_m).tolist(),
        water_density_kg_m3=base.water_density_kg_m3,
        limit_rad=min(base.max_list_rad, base.band.max_abs_heel_rad),
        gm_min_m=base.gm_min_m, kind='boundary-moment-kgm',
        measured_unsafe_kgm=measured, reference_transfer_kgm=transfer,
        roundoff_kgm={side:roundoff for side in SIDES},
        physical_reference_transfer_kgm=physical_transfer,
        qualification=dict(independent_holdout_passed=passed, branch_monotonicity_checked=True,
            calibration_seed=calibration_seed, holdout_seed=holdout_seed,
            calibration_samples={side:calibration_samples for side in SIDES},
            holdout_samples={side:holdout_samples for side in SIDES},
            max_holdout_excess_kgm=excess),
        evidence=[{'path':str(path.resolve()), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}])
    if not passed:
        raise ValueError(f'boundary holdout failed; retained {path}: {excess}')
    return bind_builder(record)


def prepare_boundary(ship, driver):
    """Resolve current inert coefficients only; never fit a response envelope.

    Boundary qualification measures its own moment capacities and therefore has
    no dependency on the general 192-state calibration or its error maxima.
    """
    from stowage.linear.physics import _prepared_params_for
    from stowage.linear.reusable import coefficient_family
    from stowage.linear.family_calibration import domain_for
    from stowage.linear.cache import response_calibration_identity
    instance, _ = driver.HELPER['cargo_instance'](ship, 'light')
    _, parameters = _prepared_params_for(instance)
    family = coefficient_family(instance, parameters=parameters)
    before = driver.snapshot(ship, family.parameters)
    if before['reference'].get('qualified') is not True or before['reference'].get('method') != 1:
        raise ValueError('production method-1 transfer reference is not qualified')
    metadata = {'response_calibration_identity':response_calibration_identity(family),
        'reference_identity':before['reference'], 'family_domain':domain_for(family, instance)}
    return family, instance, metadata, before


def main():
    import argparse
    import importlib.util
    import resource
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ship', required=True, choices=('feeder_500','feeder_1000','feeder_1700'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--samples', type=int, default=64)
    parser.add_argument('--holdout-samples', type=int, default=64)
    parser.add_argument('--calibration-samples', type=int, default=None,
        help='legacy audit argument, ignored: boundary qualification never calibrates the general response')
    parser.add_argument('--seed', type=int, default=3811)
    parser.add_argument('--holdout-seed', type=int, default=3812)
    parser.add_argument('--pilot', action='store_true', help='audit-only label; never relax qualification')
    parser.add_argument('--wrap-existing', action='store_true',
        help='verify and attach D30 builder metadata to an existing current certificate')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('epoch_qualification_driver', Path(__file__).with_name('driver.py'))
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    if args.wrap_existing:
        path = args.output/args.ship/'boundary-certificate.json'
        wrap_existing(path, ship=args.ship, driver=driver)
        print(json.dumps({'wrapped_existing':str(path), 'numerical_evidence_unchanged':True}), flush=True)
        return
    start = time.perf_counter()
    before = after = None
    family = None
    failure = None
    path = args.output/args.ship/'boundary.json'
    certificate_path = path.with_name('boundary-certificate.json')
    try:
        family, instance, diagnostics, before = prepare_boundary(args.ship, driver)
        record = qualify_boundary(family, instance,
            response_identity=diagnostics['response_calibration_identity'],
            reference_identity=diagnostics['reference_identity'], domain=diagnostics['family_domain'],
            calibration_samples=args.samples, holdout_samples=args.holdout_samples,
            calibration_seed=args.seed, holdout_seed=args.holdout_seed, output_path=path)
        after = driver.snapshot(args.ship, family.parameters)
        if before != after:
            raise ValueError('physical/reference inputs changed during boundary qualification')
        driver.write(certificate_path, record)
    except BaseException as exc:
        failure = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if family is not None and after is None:
            try:
                after = driver.snapshot(args.ship, family.parameters)
            except Exception as exc:
                after = {'snapshot_error':f'{type(exc).__name__}: {exc}'}
        summary = {'ship':args.ship, 'pilot':args.pilot, 'physical_inputs_start':before,
            'physical_inputs_end':after,
            'physical_inputs_unchanged':before is not None and before==after,
            'elapsed_seconds':time.perf_counter()-start,
            'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
            'certificate':str(certificate_path) if failure is None else None,
            'evidence':str(path), 'failure':failure,
            'calibration_samples':args.samples, 'holdout_samples':args.holdout_samples,
            'general_response_calibration':False, 'legacy_calibration_samples_ignored':args.calibration_samples}
        driver.write(path.with_name('boundary-run.json'), summary)
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
