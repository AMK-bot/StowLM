"""Reproducible production-KCS interpolation validation; no approval inference.

Reference: direct polygon free-trim equilibrium, with the same physical KG as
interpolation. Random conditions are independent of table knots and include
asymmetric TCG; TCG is applied through the same physical transverse axis in
both arms. Signed areas use the checker's piecewise-linear quadrature. This
measures interpolation error, separately from direct quadrature/model error.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from stowage.checker.stability_geometry import StabilityGeometry
from stowage.vessel.fleet import load_ship_params, load_vessel
from error_statistics import error_statistics

ROOT = Path(__file__).resolve().parent
FLEET = ("feeder_500", "feeder_800", "feeder_1700")


def gm(geometry, drafts, lcg, kg, density):
    submerged = geometry.full_submerged(*drafts, 0., density)
    return float(np.dot(np.asarray(submerged.centroid_m)-[lcg, 0., kg],
                         geometry.waterplane_normal(*drafts, 0.))+submerged.bm_t_m)


def broad_states(direct, fast, density, rng, minimum_mass_kg, maximum_mass_kg, samples=256):
    """Hold out random states across the geometric grid, including large trim."""
    reference, interpolated, failures, cases = [], [], [], []
    bounds = fast.bounds
    attempts = 0
    below_lightship = above_scantling = 0
    while len(cases)+len(failures) < samples and attempts < 8*samples:
        attempts += 1
        q = rng.uniform(*bounds['immersion_m'])
        trim = rng.uniform(*bounds['trim_m'])
        phi = rng.uniform(*bounds['heel_rad'])
        ta, tf = q/np.cos(phi)+trim/2, q/np.cos(phi)-trim/2
        s = direct.full_submerged(ta, tf, phi, density)
        # Fully dry/immersed envelopes have no unique floating attitude. They
        # are not physical loading-instrument interpolation conditions.
        if s.volume_m3 <= 1. or s.waterplane_area_m2 <= 1.:
            continue
        if s.displacement_kg < minimum_mass_kg:
            below_lightship += 1
            continue
        if s.displacement_kg > maximum_mass_kg:
            above_scantling += 1
            continue
        kg = rng.uniform(.3, .9)*direct.depth_m
        _, along, across = direct._axes(ta, tf, phi)
        lcg = s.lcb_m+(s.vcb_m-kg)*along[2]/along[0]
        physical = [lcg, 0., kg]
        ref = float(np.dot(np.asarray(s.centroid_m)-physical, across))
        state = dict(immersion_m=q, trim_m=trim, heel_rad=phi, kg_m=kg,
                     mass_kg=s.displacement_kg, lcg_m=lcg)
        try:
            result, _ = fast.free_trim_curve([phi], s.displacement_kg, lcg, kg,
                                             density, initial_drafts=(ta, tf))
            reference.append(ref)
            interpolated.append(float(result[0]))
            state['gz_reference_m'] = ref
            state['gz_interpolated_m'] = float(result[0])
            state['absolute_error_m'] = abs(ref-float(result[0]))
            state['l5_fraction'] = state['absolute_error_m']/max(.05, .05*abs(ref))
            cases.append(state)
        except ValueError as exc:
            failures.append(dict(**state, error=str(exc)))
    return dict(requested_samples=samples, completed_samples=len(cases), failures=failures,
                rejected_below_lightship=below_lightship, minimum_mass_kg=minimum_mass_kg,
                rejected_above_scantling=above_scantling, maximum_mass_kg=maximum_mass_kg,
                rejected_dry_or_fully_immersed=attempts-len(cases)-len(failures)-below_lightship-above_scantling,
                errors=error_statistics(reference, interpolated, 'gz_m'), cases=cases)


def validate(name, density, *, samples=16, seed=31024, build=False, qualify=False):
    from stowage.checker.stability_tables import load_table_geometry
    # Curve validation needs the production offsets and operating masses, not
    # a second expensive build of unrelated HullModel strength/cross curves.
    # Full production loading/check timings below explicitly use with_hull=True.
    vessel = load_vessel(name, with_hull=False)
    params = load_ship_params(name)
    direct = StabilityGeometry(params)
    started = perf_counter()
    fast = load_table_geometry(direct, params, density, build=build)
    if fast is None:
        raise RuntimeError(f"missing table for {name}, density {density}; run with --build")
    load_seconds = perf_counter()-started
    rng = np.random.default_rng(seed)
    reference = {key: [] for key in ("gz_m", "area_mrad", "gm_m")}
    approximation = {key: [] for key in reference}
    cases, failures, outside, equilibrium_errors = [], [], [], []
    scantling = params['hull']['scantling_draught_m']
    max_mass = direct.full_submerged(scantling, scantling, 0., density).displacement_kg
    min_mass = params['hull']['lightship_mass_kg']
    # Perturb every regular angular node: no tested interior heel is a table knot.
    angles = np.r_[0., np.sort(rng.uniform(.01, np.radians(50.), 20)), np.radians(50.)]
    curve_times = []
    for number in range(8*samples):
        if len(cases) >= samples:
            break
        draft = rng.uniform(.5, 1.1)*direct.design_draught_m
        trim = rng.uniform(-.015, .015)*direct.lpp_m
        kg = rng.uniform(.5, .9)*direct.depth_m
        tcg = rng.uniform(-.10, .10)
        upright = direct.full_submerged(draft+trim/2, draft-trim/2, 0., density)
        if not min_mass <= upright.displacement_kg <= max_mass:
            outside.append(dict(number=number, reason='mass outside qualified loading domain',
                mass_kg=upright.displacement_kg, minimum_mass_kg=min_mass, maximum_mass_kg=max_mass))
            continue
        along = direct._axes(draft+trim/2, draft-trim/2, 0.)[1]
        lcg = upright.lcb_m+(upright.vcb_m-kg)*along[2]/along[0]
        args = (upright.displacement_kg, lcg, kg, density)
        try:
            ref_gz, ref_drafts = direct.free_trim_curve(angles, *args)
            q = np.mean(ref_drafts, axis=1)*np.cos(angles)
            trims = ref_drafts[:, 0]-ref_drafts[:, 1]
            if (np.any(q < fast.bounds['immersion_m'][0]) or np.any(q > fast.bounds['immersion_m'][1])
                    or np.any(trims < fast.bounds['trim_m'][0]) or np.any(trims > fast.bounds['trim_m'][1])):
                outside.append(dict(number=number, reason='direct free-trim path leaves geometric grid',
                    immersion_range_m=[float(min(q)),float(max(q))],
                    trim_range_m=[float(min(trims)),float(max(trims))]))
                continue
            start = perf_counter()
            fast_gz, fast_drafts = fast.free_trim_curve(angles, *args)
            curve_times.append(perf_counter()-start)
            # Real nonzero TCG contributes its projection on the local transverse
            # axes. Neither branch incorrectly substitutes a constant heel shift.
            ref_gz -= np.asarray([tcg*direct._axes(*d, a)[2][1] for d, a in zip(ref_drafts, angles)])
            fast_gz -= np.asarray([tcg*fast._axes(*d, a)[2][1] for d, a in zip(fast_drafts, angles)])
            reference["gz_m"].extend(ref_gz.tolist())
            approximation["gz_m"].extend(fast_gz.tolist())
            for lo, hi in ((0., 30.), (0., 40.), (30., 40.), (0., 50.)):
                phi = np.r_[np.radians(lo), angles[(angles>np.radians(lo)) & (angles<np.radians(hi))], np.radians(hi)]
                for destination, curve in ((reference, ref_gz), (approximation, fast_gz)):
                    destination["area_mrad"].append(float(np.trapezoid(np.interp(phi, angles, curve), phi)))
            reference["gm_m"].append(gm(direct, ref_drafts[0], lcg, kg, density))
            approximation["gm_m"].append(gm(fast, fast_drafts[0], lcg, kg, density))
            # Low-G stable equilibrium is tested independently from the broader
            # KG range used for curves. TCG is nonzero and varies in sign.
            equilibrium_kg = min(kg, .45*direct.depth_m)
            direct_float = direct.equilibrium(args[0], lcg, tcg, equilibrium_kg, density)
            fast_float = fast.equilibrium(args[0], lcg, tcg, equilibrium_kg, density)
            equilibrium_errors.append(dict(
                heel_rad=abs(direct_float.heel_rad-fast_float.heel_rad),
                draft_aft_m=abs(direct_float.t_aft_m-fast_float.t_aft_m),
                draft_fwd_m=abs(direct_float.t_fwd_m-fast_float.t_fwd_m),
                kg_m=equilibrium_kg, tcg_m=tcg))
            cases.append(dict(number=number, mass_kg=args[0], lcg_m=lcg, kg_m=kg, tcg_m=tcg,
                              initial_draft_m=draft, initial_trim_m=trim))
        except ValueError as exc:
            failures.append(dict(number=number, error=str(exc)))
    broad = broad_states(direct, fast, density, rng, min_mass, max_mass)
    errors = {key:error_statistics(reference[key], approximation[key], key)
              if reference[key] else None for key in reference}
    if (qualify and len(cases) == samples
            and broad['completed_samples'] == broad['requested_samples']
            and not failures and not broad['failures'] and broad['errors']['target_met']
            and all(e and e["target_met"] for e in errors.values())):
        from stowage.checker.stability_tables import qualify_table
        qualify_table(fast, dict(max_l5_fraction={
            "gz": max(errors["gz_m"]["max_l5_fraction"], broad['errors']['max_l5_fraction']),
            "area": errors["area_mrad"]["max_l5_fraction"],
            "gm": errors["gm_m"]["max_l5_fraction"]}, failure_count=0, sample_count=len(cases)+broad['completed_samples']))
    return dict(vessel=vessel.id, production_geometry='StabilityGeometry(load_ship_params(name))',
                vessel_loader='load_vessel(name, with_hull=False); no synthetic hull replacement',
                density_kg_m3=density, seed=seed, requested_samples=samples,
                completed_samples=len(cases), failures=failures, load_or_build_seconds=load_seconds,
                outside_grid_cases=outside, broad_geometric_validation=broad,
                errors=errors, cache_path=str(getattr(fast, "cache_path", "")),
                grid_bounds=fast.bounds,
                equilibrium_errors=equilibrium_errors,
                curve_seconds=dict(median=float(np.median(curve_times)), max=float(max(curve_times))) if curve_times else None,
                cases=cases)


def departure_timings(name, repeats=5):
    # Import benchmark builder without changing its production loading assumptions.
    path = ROOT.parent/"kcs_departures.py"
    spec = importlib.util.spec_from_file_location("kcs_departure_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance, _ = module.build_departure(name)
    from stowage.checker.stability import check_stability, _check_stability
    started = perf_counter()
    direct = _check_stability(instance, instance.onboard, call_index=0, _force_direct=True)
    direct_seconds = perf_counter()-started
    if not direct.ran:
        raise RuntimeError(direct.error)
    report = check_stability(instance, instance.onboard, call_index=0)
    if not report.ran:
        raise RuntimeError(report.error)
    elapsed = []
    for _ in range(repeats):
        started = perf_counter()
        report = check_stability(instance, instance.onboard, call_index=0)
        elapsed.append(perf_counter()-started)
        if not report.ran:
            raise RuntimeError(report.error)
    return dict(vessel=name, repetitions=repeats, warm_seconds=elapsed,
                median_seconds=float(np.median(elapsed)), max_seconds=max(elapsed),
                target_seconds=.3, target_met=max(elapsed)<.3, direct_seconds=direct_seconds,
                direct_violation_ids=[v.rule_id for v in direct.violations],
                interpolated_violation_ids=[v.rule_id for v in report.violations],
                metric_absolute_differences={key:abs(value-direct.metrics[key])
                    for key,value in report.metrics.items() if key in direct.metrics},
                metrics=report.metrics)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--samples", type=int, default=16)
    parser.add_argument("--vessel", choices=FLEET)
    parser.add_argument("--density", type=float)
    parser.add_argument("--timings", action="store_true")
    parser.add_argument("--qualify", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT/"results.json")
    args = parser.parse_args()
    payload = dict(validation=[], timings=[])
    for name in ([args.vessel] if args.vessel else FLEET):
        for density in ([args.density] if args.density else (1025., 1000.)):
            payload['validation'].append(validate(name, density, samples=args.samples,
                                                  build=args.build, qualify=args.qualify))
            args.output.write_text(json.dumps(payload, indent=2)+"\n")
        if args.timings:
            payload['timings'].append(departure_timings(name))
            args.output.write_text(json.dumps(payload, indent=2)+"\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
