"""Capture the unchanged Claude deep-edge probe into frozen numeric evidence.

The original scratchpad is read and executed without modifying it. Each kept
state is checkpointed with its full synthetic physical load and mass vectors.
An empirical residual breach is distinguished from an actual necessary-row
rejection and from full-checker/legal-cargo feasibility.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import signal
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCRATCH = Path('/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/a226369b-5714-4c16-ad2d-7aeafdd61910/scratchpad')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--seed', type=int, default=49)
parser.add_argument('--samples', type=int, default=40)
parser.add_argument('--seconds', type=float, default=55.)
parser.add_argument('--skip-attempts', type=int, default=0,
                    help='replay RNG draws but skip already measured exact roots')
parser.add_argument('--historical-reserves', type=Path,
                    help='read-only prior captured state; bypass only changed policy qualification for historical residual reproduction')
parser.add_argument('--output', type=Path, default=HERE/'claude-edge-seed49')
args = parser.parse_args()
output = args.output.resolve()
if output == HERE or not output.is_relative_to(HERE) or not output.name.startswith('claude-'):
    parser.error('output must be a new claude-prefixed directory')
if output.exists():
    parser.error('never overwrite prior probe evidence')
output.mkdir()

def serial(value):
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, np.generic): return value.item()
    if hasattr(value, 'model_dump'): return value.model_dump(mode='json')
    raise TypeError(type(value).__name__)

def write(path, value):
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, default=serial, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def pins():
    from stowage.linear.cache import source_fingerprint
    from stowage.linear.dependencies import dependency_fingerprint
    return {'source_fingerprint': source_fingerprint(),
            'dependencies': {name: dependency_fingerprint(name) for name in ('geometry','coefficient','calibration','liquid_reference','instance')},
            'bank_sha256': sha(ROOT/'src/stowage/linear/margin_policy_data.json')}

before = pins()
started = time.monotonic()
records = []
witness = None

class FoundWitness(Exception): pass

def capture(v):
    global witness
    exact, affine, hull = v['exact'], v['aff'], v['hull']
    aft = float(exact['draught_m']+.5*exact['trim_m'])
    affine_aft = float(affine['draught_m']+.5*affine['trim_m'])
    reserve = float(v['res']['aft_hi'])
    point, domain = v['point'], v['domain']
    domain_checks = {
        'fixed_x': domain['fixed_x_min_m'] <= point.x_m <= domain['fixed_x_max_m'],
        'fixed_y': abs(point.y_m) <= domain['fixed_y_abs_max_m'],
        'fixed_z': domain['kg_min_m'] <= point.z_m <= domain['kg_max_m'],
        'total_kg': domain['kg_min_m'] <= exact['kg_m'] <= domain['kg_max_m'],
        'gm_floor': exact['gm_m'] >= v['model'].gm_min_m,
        'family_mean_draught': v['band'].draught_min_m <= exact['draught_m'] <= v['band'].draught_max_m,
    }
    full_modeled = {
        'gm': exact['gm_m'] >= v['current'].gm_min_m,
        'trim': max(hull.min_trim_m,-v['band'].max_abs_trim_m) <= exact['trim_m'] <= min(hull.max_trim_m,v['band'].max_abs_trim_m),
        'list': abs(exact['list_rad']) <= min(hull.max_list_rad,v['band'].max_abs_heel_rad),
        'draught': v['band'].draught_min_m <= exact['draught_m'] <= v['band'].draught_max_m,
        'draught_aft': aft <= hull.max_draught_m,
        'draught_forward': hull.min_draught_fwd_m <= exact['draught_m']-.5*exact['trim_m'] <= hull.max_draught_m,
        'displacement': max(hull.min_displacement_kg,v['band'].displacement_min_kg) <= exact['displacement_kg'] <= min(hull.max_displacement_kg,v['band'].displacement_max_kg),
        'sf': bool(np.all(exact['sf_n'] >= v['current'].sf_min_n) and np.all(exact['sf_n'] <= v['current'].sf_max_n)),
        'bm': bool(np.all(exact['bm_nm'] >= v['current'].bm_min_nm) and np.all(exact['bm_nm'] <= v['current'].bm_max_nm)),
    }
    record = {'seed': args.seed, 'attempt': v['attempts'], 'kept': v['kept'],
              'exact': exact, 'affine': affine, 'point_load': point,
              'cell_masses_kg': v['cell'], 'adjustable_tank_masses_kg': v['adj'],
              'all_tank_masses_kg': v['values'], 'tank_ids': [t.id for t in v['tanks']],
              'tank_adjustable': v['base'].tank_adjustable,
              'tank_capacity_kg': v['base'].tank_capacity_kg,
              'actual_fills': v['values']/v['base'].tank_capacity_kg,
              'target_mass_kg': v['mass'], 'target_kg_m': v['kg'],
              'target_trim_m': v['target_trim'], 'target_heel_rad': v['target_heel'],
              'family_band': asdict(v['band']), 'production_band': asdict(v['model'].band),
              'family_domain': domain, 'additional_domain_checks': domain_checks,
              'exact_modeled_feasibility': full_modeled,
              'all_exact_modeled_checks_pass': all(full_modeled.values()),
              'aft_exact_m': aft, 'aft_affine_m': affine_aft,
              'aft_upper_limit_m': hull.max_draught_m,
              'necessary_aft_reserve_m': reserve,
              'aft_optimistic_residual_m': affine_aft-aft,
              'aft_residual_excess_m': affine_aft-aft-reserve,
              'necessary_aft_row_violation_m': affine_aft-hull.max_draught_m-reserve,
              'residuals': v['obs'], 'old_reserves': v['nec'],
              'warning': 'Synthetic calibration point loads; full legal-cargo/full-checker feasibility is a separate claim'}
    records.append(record)
    write(output/f"state-{len(records):03d}.json", record)
    write(output/'progress.json', {'kept': len(records), 'attempts': v['attempts'],
        'elapsed_s': time.monotonic()-started, 'max_aft_excess_m': max(r['aft_residual_excess_m'] for r in records),
        'max_aft_row_violation_m': max(r['necessary_aft_row_violation_m'] for r in records)})
    if record['aft_residual_excess_m'] > 1e-7:
        witness = record
        write(output/'witness.json', record)
        write(output/'witness-instance.json', v['state'])
        write(output/'witness-parameters.json', v['family'].parameters)
        write(output/'witness-model.json', {**asdict(v['current']),
            'reference_cell_vcg_m': v['current'].cell_vcg_m,
            'measurement_diagnostics': v['model'].measurement_diagnostics})
        if record['necessary_aft_row_violation_m'] > 1e-7 and all(domain_checks.values()) and all(full_modeled.values()):
            raise FoundWitness()

def alarm(*_): raise TimeoutError('declared55s probe budget reached')
signal.signal(signal.SIGALRM, alarm)
signal.setitimer(signal.ITIMER_REAL, args.seconds)
source_path = SCRATCH/'edge_probe_deep.py'
source = source_path.read_text()
assert source.count('    for k, v in obs.items():') == 1
instrumented = source.replace('    for k, v in obs.items():', '    capture_probe_state(locals())\n    for k, v in obs.items():')
historical = None
if args.historical_reserves:
    historical = json.loads(args.historical_reserves.read_text())['old_reserves']
    instrumented = instrumented.replace('model = build_linear_model(inst, band=band_for_instance(inst))',
        "historical_family = coefficient_family(inst)\nmodel = assemble_instance(historical_family, inst, band_for_instance(inst), errors=historical_family.template.errors, diagnostics={'calibration_synthetic': True})")
    instrumented = instrumented.replace('nec = default_reserves(model, direction="necessary")',
        'nec = historical_reserves')
if args.skip_attempts:
    instrumented = instrumented.replace('attempts < N*8:', f'attempts < N*8+{args.skip_attempts}:')
    instrumented = instrumented.replace('    try:\n        current = assemble_instance',
        f'    if attempts <= {args.skip_attempts}: continue\n    try:\n        current = assemble_instance')
sys.argv = [str(source_path), str(args.seed), str(args.samples)]
status = 'completed'
try:
    exec(compile(instrumented, str(source_path), 'exec'), {'__name__': '__main__', 'capture_probe_state': capture,
                                                        'historical_reserves': historical})
except FoundWitness:
    status = 'all-modeled-exact-feasible necessary aft row rejection captured'
except TimeoutError:
    status = 'budget exhausted; checkpoints retained'
finally:
    signal.setitimer(signal.ITIMER_REAL, 0.)
    after = pins()
    result = {'status': status, 'seed': args.seed, 'kept': len(records),
              'witness_found': witness is not None, 'inputs_start': before, 'inputs_end': after,
              'skip_attempts': args.skip_attempts,
              'historical_policy_hook': None if args.historical_reserves is None else {
                  'source': str(args.historical_reserves), 'sha256': sha(args.historical_reserves),
                  'meaning': 'unchanged numerical coefficient/assembly path with frozen old reserves; new live policy acceptance is not claimed'},
              'inputs_unchanged': before == after,
              'scratch_probes': {p.name: sha(p) for p in SCRATCH.glob('*.py')},
              'instrumentation_source_sha256': sha(Path(__file__)),
              'elapsed_s': time.monotonic()-started,
              'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    write(output/'summary.json', result)
    print(json.dumps(result), flush=True)
