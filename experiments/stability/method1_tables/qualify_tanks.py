"""Qualify persisted tank moments against off-grid real-geometry holdouts.

Sixteen random loaded moment profiles vary all actual fill fractions, heel and
trim across light-to-scantling physically feasible displacements. The sum of the absolute transverse projections
of the first-moment errors bounds the transfer lever at prescribed attitudes.
The raw arm retains the baseline upright KG; the moving liquid centre enters
the longitudinal equilibrium equation, not a second transverse GZ subtraction
(stability_geometry.StabilityGeometry.free_trim_curve). Therefore the regulatory
transfer correction is counted once. A longitudinal error contributes
only through the actual transverse direction's longitudinal component; it is
not counted as a full transverse lever. Full three-dimensional norms and LCG
errors remain diagnostics. Free-trim feedback is validated by separate full
curves; this component calculation alone does not bound a changed equilibrium.
Absolute errors are compared with L5's absolute floors,
without relying on a generous relative tolerance near a large GZ or area.
This component bound is supplemented by full checker free-trim curve holdouts.

Consumable full-fill envelopes have additional independently random heel/trim
holdouts against the bounded direct max_transfer reference. A difference no
larger than its stated conservative oracle excess is retained as error, not
silently subtracted. Source/parameter hashes and SHA-bound table filenames are
recorded. No table is certified if any missing grid or error target fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter
from zipfile import BadZipFile

import numpy as np

from stowage.checker import stability_liquid_tables as tables
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_loading import CONSUMABLE_KINDS
from stowage.checker.stability_sounding import sounding
from stowage.checker.stability_tank_transfer import max_transfer
from stowage.checker.stability_budget import budget,StabilityTimeout
from stowage.versions import component_versions
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry

ROOT = Path(__file__).resolve().parent
MAX_HEEL_RAD = float(np.deg2rad(70.))
MAX_TRIM_RAD = float(np.arctan(.06))


def profile_inputs(rng, index, keys, capacities_kg, lightship_mass, scantling_mass):
    """Deterministic boundary cases followed by independent random holdouts.

    Every tank, including both peaks, is empty in profile0 and pressed full
    in profile1. Later profiles include asymmetric endpoints and interior fills.
    Displacement always pays for the actual liquid mass before adding cargo.
    """
    if index == 0:
        fills = dict.fromkeys(keys, 0.)
    elif index == 1:
        fills = dict.fromkeys(keys, 1.)
    elif index == 2:
        fills = {key: float(i % 2) for i,key in enumerate(keys)}
    else:
        fills = {key: float(rng.uniform(0.,1.)) for key in keys}
    minimum = lightship_mass + sum(capacities_kg[key]*fill for key,fill in fills.items())
    if minimum > scantling_mass:
        raise ValueError("tank fills exceed scantling displacement before cargo")
    mass = (minimum if index == 0 else scantling_mass if index == 1 else
            float(rng.uniform(minimum,scantling_mass)))
    end = MAX_HEEL_RAD if index < 5 else float(rng.uniform(np.deg2rad(40.),MAX_HEEL_RAD))
    angles = np.unique(np.r_[0.,np.deg2rad(10.),np.sort(rng.uniform(0.,end,9)),end])
    if index % 2:
        angles = -angles
    trim0,trim1 = ([-MAX_TRIM_RAD,MAX_TRIM_RAD] if index < 2 else
                   rng.uniform(-MAX_TRIM_RAD,MAX_TRIM_RAD,2))
    trims = trim0+(trim1-trim0)*np.abs(angles)/end
    return fills,mass,angles,trims



def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def transfer_lever_difference(delta, volume, density, mass, heel, trim):
    """One transverse transfer correction difference at an identical attitude."""
    normal = np.array([np.tan(trim)*np.cos(heel), -np.sin(heel), np.cos(heel)])
    normal /= np.linalg.norm(normal)
    longitudinal = np.array([1., 0., -np.tan(trim)])
    longitudinal /= np.linalg.norm(longitudinal)
    transverse = np.cross(normal, longitudinal)
    return density*volume/mass*float(np.asarray(delta) @ transverse)


def reusable_envelopes(previous, current, consumables, density, *, requested):
    """Validate unchanged FO measurements; a changed ballast grid is irrelevant.

    NPZ digests bind their numerical grids AND physical provenance metadata.
    This function validates the table metadata separately at the call site;
    the retained direct-oracle source and its per-tank bound must also match.
    Invalid evidence raises ValueError, causing the caller to remeasure safely.
    """
    if previous.get('domain') != current.get('domain'):
        raise ValueError('qualification domain changed')
    if previous['parameters_sha256'] != current['parameters_sha256'] or previous['vessel'] != current['vessel']:
        raise ValueError('raw vessel parameters differ')
    if previous.get('samples', 0) < 4 or previous.get('envelope_samples', 0) < max(4, requested):
        raise ValueError('retained random sample count is insufficient')
    if len(previous['envelopes']) < max(4, requested):
        raise ValueError('retained envelope rows are incomplete')
    oracle_source = 'src/stowage/checker/stability_tank_transfer.py'
    if previous['source_sha256'][oracle_source] != current['source_sha256'][oracle_source]:
        raise ValueError('direct consumable-envelope oracle source changed')
    if previous['displacement_kg'] != current['displacement_kg']:
        raise ValueError('physical displacement or direct-oracle tolerance changed')
    mass = current['displacement_kg']
    for key in consumables:
        a, b = previous['grid_manifest'][key], current['grid_manifest'][key]
        if any(a[field] != b[field] for field in ('sha256', 'fills', 'heels', 'trims')):
            raise ValueError(f'consumable grid or bound physical metadata changed: {key}')
    selected = previous['envelopes'][:requested]
    for index, row in enumerate(selected):
        if row['index'] != index or sorted(t['id'] for t in row['tanks']) != sorted(consumables):
            raise ValueError('retained envelope tank IDs/index differ')
        aggregate = 0.
        attitudes = set()
        for tank in row['tanks']:
            key = tank['id']
            fields = ('heel_rad', 'trim_rad', 'direct_m4', 'interpolated_m4',
                      'oracle_upper_bound_excess_m4', 'gz_error_m')
            if not all(np.isfinite(tank[field]) for field in fields):
                raise ValueError('nonfinite retained direct envelope measurement')
            if tank['direct_m4'] < 0 or tank['interpolated_m4'] < 0 or tank['gz_error_m'] < 0:
                raise ValueError('negative retained envelope magnitude/error')
            attitudes.add((tank['heel_rad'], tank['trim_rad']))
            expected_tolerance = .0001*mass/max(1, len(consumables))/density[key]
            if tank['oracle_upper_bound_excess_m4'] != expected_tolerance:
                raise ValueError('direct conservative envelope tolerance changed')
            error = density[key]/mass*abs(tank['interpolated_m4']-tank['direct_m4'])
            if not np.isclose(error, tank['gz_error_m'], rtol=1e-12, atol=1e-14):
                raise ValueError('retained per-tank GZ difference is inconsistent')
            aggregate += error
        if len(attitudes) != 1 or not np.isfinite(row['aggregate_gz_error_m']) or not np.isclose(
                aggregate, row['aggregate_gz_error_m'], rtol=1e-12, atol=1e-14):
            raise ValueError('retained aggregate envelope measurement is inconsistent')
    return selected


def run(name, output, samples=16, envelope_samples=4, seed=3912, certify=False,
        reuse_envelopes=None, direct_budget_s=120.):
    if not isinstance(samples,int) or samples < 1:
        raise ValueError('positive integer sample count required')
    if not isinstance(envelope_samples,int) or envelope_samples < 0:
        raise ValueError('nonnegative integer envelope sample count required')
    if not math.isfinite(direct_budget_s) or direct_budget_s <= 0:
        raise ValueError('finite positive direct-reference budget required')
    started = perf_counter()
    params = load_ship_params(name)
    records = [r for r in params['tanks'] if r['geometry']['shape'] == 'hull_sections']
    solids = {r['id']: TankGeometry.from_params(r, ship_params=params) for r in records}
    grids = {key: tables.lookup(solid, require_qualified=False) for key, solid in solids.items()}
    missing = [key for key, grid in grids.items() if grid is None]
    if missing:
        raise ValueError(f'build tables before qualification: {missing}')
    density = {r['id']: float(r['content_density_kg_m3']) for r in records}
    lightship_mass = float(params['hull']['lightship_mass_kg'])
    hull = StabilityGeometry(params)
    scantling = float(params['hull']['scantling_draught_m'])
    scantling_mass = hull.submerged(scantling,scantling,0.,hull.density_kg_m3).displacement_kg
    # Conservative envelope normalization: no physically loaded ship is lighter.
    mass = lightship_mass
    capacities_kg = {key:solid.capacity_m3*density[key] for key,solid in solids.items()}
    sources = sorted(Path(tables.__file__).parent.glob('stability*.py'))
    result = dict(vessel=name, samples=samples, envelope_samples=envelope_samples, seed=seed,
        direct_envelope_holdout_budget_s=float(direct_budget_s),
        parameters_sha256=hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest(),
        component_versions=component_versions(), source_sha256={str(p.relative_to(ROOT.parents[2])):
            hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        displacement_kg=mass, lightship_mass_kg=lightship_mass, scantling_mass_kg=scantling_mass,
        domain=dict(heel_rad=[-MAX_HEEL_RAD,MAX_HEEL_RAD],trim_rad=[-MAX_TRIM_RAD,MAX_TRIM_RAD],fill_fraction=[0.,1.],
                    displacement_kg=[lightship_mass,scantling_mass]),
        loading_policy='each tank empty/full/asymmetric endpoints and random fills over0–1; displacement sampled from actual lightship+liquid mass to upright scantling; nonnegative remaining cargo',
        denominator_policy='L5 absolute floors only: GZ0.05m, area0.0012mrad, GM0.05m',
        grid_manifest={key: dict(path=str(tables._path(solid)),
            sha256=hashlib.sha256(tables._path(solid).read_bytes()).hexdigest(),
            fills=len(grids[key].fills), heels=len(grids[key].heels), trims=len(grids[key].trims))
            for key, solid in solids.items()}, profiles=[], envelopes=[], complete=False)
    save(output, result)
    rng = np.random.default_rng(seed)
    max_gz = max_area = max_gm = 0.
    for index in range(samples):
        fractions,mass,angles,trims = profile_inputs(rng,index,list(solids),capacities_kg,
                                                    lightship_mass,scantling_mass)
        error = np.zeros(len(angles))
        signed_correction_error = np.zeros(len(angles))
        norm_error = np.zeros(len(angles))
        total_cg_error = np.zeros((len(angles), 3))
        liquid_mass = 0.
        gm_error = 0.
        tank_rows = []
        for key, solid in solids.items():
            fraction = fractions[key]
            volume = fraction*solid.capacity_m3
            liquid_mass += density[key]*volume
            grid = grids[key]
            reference_upright = solid.sounding(volume)
            cached_upright = sounding(solid, volume)
            gm_error += density[key]*abs(reference_upright.fsm_m4-cached_upright.fsm_m4)/mass
            local_errors = []
            xyz_errors = []
            for j, (heel, trim) in enumerate(zip(angles, trims, strict=True)):
                direct = np.asarray(solid.liquid(volume, float(heel), float(trim)).centroid_m)
                fast = np.asarray(grid.centroid(volume, float(heel), float(trim)))
                delta = fast-direct
                # Raw GZ uses the original upright [LCG,0,KG]. The moving CG
                # affects free-trim balance; the explicit transverse transfer
                # correction is subtracted once, not again as a KG shift.
                # This same-attitude test does not model equilibrium feedback;
                # the separate full direct-root holdouts test that coupling.
                lever_difference = transfer_lever_difference(delta, volume, density[key], mass, heel, trim)
                bound = abs(lever_difference)
                error[j] += bound
                signed_correction_error[j] += lever_difference
                norm_error[j] += 2*density[key]*volume/lightship_mass*float(np.linalg.norm(delta))
                total_cg_error[j] += density[key]*volume/mass*delta
                local_errors.append(bound)
                xyz_errors.append(abs(delta))
            tank_rows.append(dict(id=key, fill=fraction, max_gz_error_bound_m=max(local_errors),
                maximum_centroid_error_xyz_m=np.max(xyz_errors, axis=0).tolist()))
        cargo_mass = mass-lightship_mass-liquid_mass
        if cargo_mass < 0:
            raise ValueError('random fills exceed sampled mass without cargo: not a physical loaded condition')
        gz_error = float(np.max(error))
        area_error = float(abs(np.trapezoid(error, angles)))
        max_gz, max_area, max_gm = max(max_gz, gz_error), max(max_area, area_error), max(max_gm, gm_error)
        result['profiles'].append(dict(index=index, displacement_kg=mass, liquid_mass_kg=liquid_mass,
            angles_rad=angles.tolist(), trims_rad=trims.tolist(),
            aggregate_error_bound_m=error.tolist(), maximum_error_bound_m=gz_error,
            area_error_bound_mrad=area_error, gm_error_bound_m=gm_error,
            signed_correction_difference_m=signed_correction_error.tolist(),
            signed_gz_difference_at_fixed_attitude_m=(-signed_correction_error).tolist(),
            maximum_signed_aggregate_gz_difference_m=float(np.max(abs(signed_correction_error))),
            signed_area_difference_at_fixed_attitude_mrad=float(np.trapezoid(-signed_correction_error, angles)),
            physical_cg_error_xyz_m=total_cg_error.tolist(), cargo_mass_kg=cargo_mass,
            diagnostic_lightship_3d_norm_bound_m=norm_error.tolist(),
            diagnostic_lightship_3d_norm_area_mrad=float(abs(np.trapezoid(norm_error, angles))),
            tanks=tank_rows))
        save(output, result)
    mass = lightship_mass
    consumables = [r['id'] for r in records if r['kind'] in CONSUMABLE_KINDS]
    retained_envelopes = None
    if reuse_envelopes is not None:
        try:
            previous_bytes = reuse_envelopes.read_bytes()
            previous = json.loads(previous_bytes)
            # A matching NPZ SHA includes provenance_json. Check that provenance
            # still matches the live source/geometry, independently of lookup.
            for key in consumables:
                with np.load(tables._path(solids[key]), allow_pickle=False) as saved:
                    if json.loads(str(saved['provenance_json'])) != tables._provenance(solids[key]):
                        raise ValueError(f'current physical grid metadata differs: {key}')
            retained_envelopes = reusable_envelopes(previous, result, consumables, density,
                                                    requested=envelope_samples)
            for row in retained_envelopes:
                for tank in row['tanks']:
                    interpolated = grids[tank['id']].max_transfer(tank['heel_rad'], tank['trim_rad'],
                        tolerance_m4=tank['oracle_upper_bound_excess_m4'])
                    if not np.isclose(interpolated, tank['interpolated_m4'], rtol=1e-12, atol=1e-10):
                        raise ValueError('retained interpolated envelope does not match current unchanged grid')
            result['envelope_reuse'] = dict(source=str(reuse_envelopes),
                source_sha256=hashlib.sha256(previous_bytes).hexdigest(),
                previous_seed=previous['seed'], previous_profile_samples=previous['samples'],
                reused_count=len(retained_envelopes),
                reason='same raw vessel parameters, consumable NPZ digests and physical metadata, oracle source, displacement and conservative direct tolerances; DB-M refinements do not change FO measurements',
                remeasured=False)
        except (OSError, ValueError, TypeError, KeyError, IndexError, EOFError, BadZipFile) as error:
            retained_envelopes = None
            result['envelope_reuse_rejected'] = str(error)
        save(output, result)
    if retained_envelopes is not None:
        result['envelopes'] = retained_envelopes
        max_gz = max(max_gz, max((row['aggregate_gz_error_m'] for row in retained_envelopes), default=0.))
        save(output, result)
    for index in range(0 if retained_envelopes is not None else envelope_samples):
        heel = float(rng.choice([-1, 1])*rng.uniform(np.deg2rad(2), MAX_HEEL_RAD))
        trim = float(rng.uniform(-MAX_TRIM_RAD, MAX_TRIM_RAD))
        tolerance = .0001*mass/max(1, len(consumables))
        error = 0.
        rows = []
        try:
            with budget(direct_budget_s):
                for key in consumables:
                    tolerance_m4 = tolerance/density[key]
                    direct = max_transfer(solids[key], heel, trim, tolerance_m4=tolerance_m4)
                    fast = grids[key].max_transfer(heel, trim, tolerance_m4=tolerance_m4)
                    lever_error = density[key]/mass*abs(fast-direct)
                    error += lever_error
                    rows.append(dict(id=key, heel_rad=heel, trim_rad=trim, direct_m4=direct,
                        interpolated_m4=fast, oracle_upper_bound_excess_m4=tolerance_m4,
                        gz_error_m=lever_error))
        except StabilityTimeout as timeout:
            result['reference_timeout']=dict(index=index,budget_s=direct_budget_s,
                message=str(timeout),scope='complete direct consumable-envelope holdout',
                interpretation='incomplete qualification, no interpolation-error verdict')
            save(output,result)
            raise
        max_gz = max(max_gz, error)
        # Pointwise envelope holdouts do not establish a full-curve area bound;
        # full wet departure curves supply the independent area measurement.
        result['envelopes'].append(dict(index=index, aggregate_gz_error_m=error, tanks=rows))
        save(output, result)
    result['maxima'] = dict(gz_m=max_gz, area_mrad=max_area, gm_m=max_gm,
        gz=max_gz/.05, area=max_area/.0012, gm=max_gm/.05)
    result['all_passed'] = all(result['maxima'][key] <= .2 for key in ('gz', 'area', 'gm'))
    if certify:
        if samples < 7 or (consumables and envelope_samples < 4):
            raise ValueError('certification requires three boundary plus four random profiles, and four consumable-envelope holdouts')
        if not result['all_passed']:
            raise ValueError('holdouts exceed0.2 of L5 tolerance; no certificates written')
        evidence = dict(result['maxima'], samples=samples, envelope_samples=envelope_samples,
            qualification_file=str(output), parameters_sha256=result['parameters_sha256'],
            scope='aggregate actual transverse first-moment lever bounds over light-to-scantling displacement, fills0–1, heels±70deg, trims±atan(.06), plus direct consumable-envelope holdouts; three-dimensional norm diagnostic not an acceptance criterion; free-trim feedback checked separately')
        for solid in solids.values():
            tables.qualify(solid, evidence)
        result['certified'] = True
    result.update(complete=True, elapsed_seconds=perf_counter()-started)
    save(output, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel', choices=('feeder_500','feeder_1000','feeder_1700'), required=True)
    parser.add_argument('--samples', type=int, default=16)
    parser.add_argument('--envelope-samples', type=int, default=4)
    parser.add_argument('--seed', type=int, default=3912)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--certify', action='store_true')
    parser.add_argument('--direct-budget-s',type=float,default=120.,
        help='bounded offline allowance per complete direct consumable-envelope holdout')
    parser.add_argument('--reuse-envelopes', type=Path,
        help='reuse unchanged SHA-bound FO envelope measurements; any failed provenance check safely remeasures them')
    args = parser.parse_args()
    result = run(args.vessel, args.output, args.samples, args.envelope_samples, args.seed, args.certify,
                 args.reuse_envelopes,args.direct_budget_s)
    print(json.dumps({key: result[key] for key in ('vessel','maxima','all_passed','elapsed_seconds')}, indent=2))
