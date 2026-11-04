"""Replay CONVERGENCE.md's manual conditions on current production physics.

ASSUMPTION: retain the historical loading-manual recipes, not their rounded
reported tonnes. Recover binary peak fills and map the former forward peak
onto FPT + FWD-DEEP as documented by the existing effectiveness rerun.
No rule is changed. A failed recipe is not an infeasibility proof.
ASSUMPTION: each synthetic test container has an explicitly declared 30,480 kg
CSC plate rating, independent of size. The old harness predates that mandatory
checker evidence. This supplies synthetic evidence, never a missing-data bypass.
The ninth condition (1700 C2) never had a saved passing witness. Run the
original search's first DB+HEEL candidate and retain that distinction.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import resource
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parents[4]
REVIEW = ROOT / 'experiments/physics-review/convergence/post_d30'
EFFECTIVENESS = ROOT / 'experiments/ballast/effectiveness'
sys.path[:0] = [str(REVIEW), str(EFFECTIVENESS)]


def attach_voyage(instance):
    """Retain actual route/cargo/liquids and declare its immediate-next-call leg.

    D34 densities and their source pins come from exact synthetic port profiles.
    The production helper fails closed on unknown/ambiguous LOCODEs; no current
    dock density is silently copied to a different next call. D50's default is
    consumption=None (10% remaining per declared vessel consumable group).
    """
    from stowage.generate.instances_voyage import attach_default_voyage

    return attach_default_voyage(instance)


def _load_harness():
    """Load historical recipes, removing only their known passive global spies.

    Phase evidence comes from the full checker's identity-labelled reports,
    never the old SPY dictionary whose last curve can be the arrival curve.
    Neither the production part context nor density selection is replaced.
    """
    harness = importlib.import_module('harness2')
    historical = getattr(harness, 'h1', None)
    if historical is not None:
        from stowage.checker import stability_criteria, stability_curve

        for module, function, spy, original in (
            (stability_criteria, 'evaluate_criteria', '_spy_eval', '_orig_eval'),
            (stability_curve, 'build_stability_curve', '_spy_curve', '_orig_curve'),
        ):
            if getattr(module, function) is getattr(historical, spy, None):
                setattr(module, function, getattr(historical, original))
    return harness


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _input_identity(name, condition, instance, plan, source):
    """Pin actual replay inputs and the complete local numerical source context."""
    paths = {*ROOT.joinpath('src/stowage').rglob('*.py'), *ROOT.joinpath('params').rglob('*.toml'),
             Path(__file__), REVIEW/'harness2.py', REVIEW.parent/'harness.py',
             EFFECTIVENESS/'regression.py', EFFECTIVENESS/'slices.py', source}
    params_path = ROOT/'params/fleet'/f'{name}.toml'
    params = tomllib.loads(params_path.read_text())
    offsets = params.get('hull_form', {}).get('offsets_path')
    if offsets:
        paths.add(ROOT/offsets)
    files = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths)}
    return dict(format='d56-voyage-convergence-v1', ship=name, condition=condition,
                instance_sha256=_digest(instance.model_dump(mode='json')),
                plan_sha256=_digest(plan.model_dump(mode='json')), files=files,
                runtime=dict(python=platform.python_version(),
                             libraries={key: version(key) for key in ('numpy', 'scipy', 'pydantic')},
                             physics_environment={key: value for key, value in sorted(os.environ.items())
                                                  if key.startswith('STOWAGE_')}))


def _phase_reports(check, voyage):
    parts = check.get('report', {}).get('parts', {})
    result = {}
    for identity in (voyage.departure, voyage.arrival):
        selected = {name: parts[f'condition:{identity.id}:{name}']
                    for name in ('stability', 'strength')
                    if f'condition:{identity.id}:{name}' in parts}
        available = len(selected) == 2
        result[identity.phase] = dict(identity=identity.model_dump(mode='json'), parts=selected,
            available=available, passed=(all(part.get('ran') and not any(
                v.get('severity') == 'hard' for v in part.get('violations', []))
                for part in selected.values()) if available else None))
    return result


def run(name, condition, output):
    harness2 = _load_harness()
    from regression import witness_masses
    from slices import bounded_full_check
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.vessel.fleet import load_ship_params, load_vessel
    from stowage.versions import component_versions

    source = REVIEW / f'search_{name}_{condition}.json'
    if source.exists():
        historical = json.loads(source.read_text())
    else:
        if (name, condition) != ('feeder_1700', 'C2'):
            raise FileNotFoundError(source)
        source = ROOT / 'experiments/physics-review/CONVERGENCE.md'
        historical = dict(witness=None, candidates=[dict(label='DB+HEEL', passed=False)])
    candidate = historical.get('witness') or historical['candidates'][0]
    vessel = load_vessel(name)
    masses = witness_masses(vessel, candidate['label'])
    instance, info = harness2.build(name, vessel, load_ship_params(name), condition,
                                   sum(masses.values()))
    for box in instance.containers:
        box.max_gross_mass_kg = 30480.
    info['synthetic_csc_plate_max_gross_kg'] = 30480.
    instance = attach_voyage(instance)
    plan = MovePlan(id=f'{instance.id}.fix-t2-vessel-recheck', instance_id=instance.id,
                    call_index=instance.call_index,
                    ballast=BallastCondition(tank_masses_kg=masses, source='planner'))
    identity = _input_identity(name, condition, instance, plan, source)
    fingerprint = _digest(identity)
    path = output/f'{name}_{condition}.json'
    if path.exists():
        cached = json.loads(path.read_text())
        if (cached.get('input_fingerprint') != fingerprint or cached.get('input_identity') != identity
                or cached.get('voyage') != instance.voyage.model_dump(mode='json')):
            raise ValueError(f'cached convergence inputs differ or are unbound: {path}; choose a fresh --out directory')
        if cached.get('check', {}).get('status') == 'completed':
            expected_phases = _phase_reports(cached['check'], instance.voyage)
            if cached.get('phase_reports') != expected_phases:
                raise ValueError(f'cached convergence phase evidence differs: {path}')
            return cached
    checked = bounded_full_check(instance, plan)
    if _input_identity(name, condition, instance, plan, source) != identity:
        raise ValueError('convergence inputs changed during checking; result not published')
    result = dict(ship=name, condition=condition, historical_witness=bool(historical.get('witness')),
                  historical_label=candidate['label'], historical_passed=candidate['passed'],
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  vessel_sha256=hashlib.sha256(vessel.model_dump_json().encode()).hexdigest(),
                  components=component_versions(), ballast_masses_kg=masses, info=info,
                  voyage=instance.voyage.model_dump(mode='json'),
                  input_identity=identity, input_fingerprint=fingerprint,
                  check=checked, phase_reports=_phase_reports(checked, instance.voyage))
    path.write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ships', nargs='+', default=['feeder_500', 'feeder_1000', 'feeder_1700'])
    parser.add_argument('--conditions', nargs='+', default=['C1', 'C2', 'C3'])
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent / 'd56-results')
    parser.add_argument('--current-manual', action='store_true',
                        help='check the restored current production manual, preserving the historical replay by default')
    args = parser.parse_args()
    if args.current_manual:
        sys.path.insert(0, str(Path(__file__).resolve().parent/'restore'))
        from current import run as run_current

        output = args.out if '--out' in sys.argv else Path(__file__).resolve().parent/'restore/current-results'
        output.mkdir(parents=True, exist_ok=True)
        rows = []
        started = time.perf_counter()
        for name in args.ships:
            for condition in args.conditions:
                row = run_current(name, condition, output)
                rows.append(row)
                print(json.dumps(dict(ship=name, condition=condition,
                    passed=row['check']['passed'], status=row['check']['status'],
                    wall_s=row['check']['wall_s'])), flush=True)
                for phase, evidence in row['phase_reports'].items():
                    print(json.dumps(dict(ship=name, condition=condition, phase=phase,
                        passed=evidence['passed'], available=evidence['available'],
                        condition_id=evidence['identity']['id'])), flush=True)
                (output/'summary.json').write_text(json.dumps(dict(
                    rows=rows, completed=sum(row['check']['status']=='completed' for row in rows),
                    passed=sum(row['check']['passed'] is True for row in rows),
                    total=len(rows), elapsed_s=time.perf_counter()-started), indent=2)+'\n')
        return
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    for name in args.ships:
        for condition in args.conditions:
            # Resume always rebuilds/pins current inputs before accepting a row.
            # Historical results/ and legacy 0/9 rows are never trusted by path.
            row = run(name, condition, args.out)
            rows.append(row)
            print(json.dumps(dict(ship=name, condition=condition, passed=row['check']['passed'],
                                  status=row['check']['status'], wall_s=row['check']['wall_s'])), flush=True)
            for phase, evidence in row.get('phase_reports', {}).items():
                print(json.dumps(dict(ship=name, condition=condition, phase=phase,
                    condition_id=evidence['identity']['id'], passed=evidence['passed'],
                    available=evidence['available'],
                    water_density_kg_m3=evidence['identity']['water_density_kg_m3'])), flush=True)
            summary = dict(passed=sum(r['check']['passed'] is True for r in rows), total=len(rows),
                           completed=sum(r['check']['status']=='completed' for r in rows),
                           historical_witness_passes=sum(r['historical_witness'] and r['check']['passed'] is True for r in rows),
                           elapsed_s=time.perf_counter()-started,
                           peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9,
                           rows=rows)
            (args.out / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')


if __name__ == '__main__':
    main()
