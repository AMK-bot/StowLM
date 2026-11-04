"""Replay a final measured bank against immutable calibration populations.

No equilibrium or new samples run here. Coverage of the union used to enrich
the bank is calibration coverage, never an independent successful holdout or
a uniform bound. Each percentile remains its original within-population p99.
Only reserve intervals and the LIST-margin constant row are reconstructed;
other instance constant rows and full-plan legality are outside this replay.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import runpy

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SIDES = ('lower_unsafe', 'upper_unsafe')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def same(first, second, name):
    from stowage.linear.cache import _canonical
    if _canonical(first) != _canonical(second):
        raise ValueError(name+' changed')


def reconstructed_intervals(model, band, reserves):
    """Exact bound intervals, plus the canonical exhausted LIST constant row."""
    lower, upper = reserves['lower_unsafe'], reserves['upper_unsafe']
    trim = min(model.max_trim_m, band['max_abs_trim_m'])
    empty = []
    for name, lo, hi in (
        ('trim', -trim+lower['trim_m'], trim-upper['trim_m']),
        ('draught', band['draught_min_m']+lower['draught_m'], band['draught_max_m']-upper['draught_m'])):
        if lo > hi:
            empty.append(name)
    for metric, prefix, lows, highs in (('sf_n','sf',model.sf_min_n,model.sf_max_n),
                                       ('bm_nm','bm',model.bm_min_nm,model.bm_max_nm)):
        for x, lo, hi, error_lo, error_hi in zip(model.station_x_m, lows, highs,
                                               lower[metric], upper[metric]):
            if lo+error_lo > hi-error_hi:
                empty.append(f'{prefix}@{x:g}')
    heel = min(model.max_list_rad, band['max_abs_heel_rad'])
    impossible = ['list_margin'] if min(heel-lower['list_rad'], heel-upper['list_rad']) < 0 else []
    return empty, impossible


def main():
    from stowage.linear.cache import load_model, load_payload, response_calibration_identity
    from stowage.linear.dependencies import dependency_fingerprint
    from stowage.linear.family_calibration import (reference_inputs_cached,
        reference_cache_identity, _validated_errors, _validate_calibration_certificate)
    from stowage.linear.margin_policy import _BANK_PATH, _evidence, _envelope, qualify_policy, policy_inputs
    from stowage.linear.reusable import CoefficientFamily, _ARRAYS
    from stowage.vessel.fleet import load_ship_params
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, action='append', required=True,
                        help='repeat for each immutable seed/population directory')
    parser.add_argument('--output', type=Path, default=HERE/'signed-family/d30/resume/union-replay')
    args = parser.parse_args()
    bank_sha = sha(_BANK_PATH)
    bank = json.loads(_BANK_PATH.read_text())
    expected_policy = policy_inputs()
    helper = runpy.run_path(str(HERE/'narrow.py'))
    public_helper = runpy.run_path(str(HERE/'d30_validate.py'))['public_signed_evidence']
    metric_rows, cases = [], []
    inputs, families = {}, {}
    for directory in args.input:
        for path in sorted(directory.glob('*.json')):
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or 'diagnostics' not in record or 'ship' not in record:
                continue
            ship, level = record['ship'], record['level']
            if ship not in inputs:
                parameters = load_ship_params(ship)
                inputs[ship] = (parameters, dict(coefficient_builder=dependency_fingerprint('coefficient'),
                    calibration_builder=dependency_fingerprint('calibration'),
                    parameters=helper['input_snapshot'](ship),
                    reference=reference_cache_identity(reference_inputs_cached(parameters))))
            parameters, expected = inputs[ship]
            if record['physical_inputs_unchanged'] is not True:
                raise ValueError('source population changed inputs')
            same(record['physical_inputs_start'], record['physical_inputs_end'], 'source input epoch')
            # The old policy is intentionally retained in the observation.
            same({key:value for key,value in record['physical_inputs_start'].items() if key != 'margin_policy'},
                 expected, 'numerical inputs')
            diagnostics = record['calibration']
            identity = diagnostics['response_calibration_identity']
            entry = bank['entries'][identity]
            _evidence(entry['evidence'])
            relative = str(path.resolve().relative_to(ROOT))
            digest = sha(path)
            if not any(item['path'] == relative and item['sha256'] == digest for item in entry['evidence']):
                raise ValueError('final bank does not pin source population: '+relative)
            key = diagnostics['coefficient_family_cache_key']
            if key not in families:
                template = load_model(key)
                if template is None:
                    raise ValueError('coefficient cache/certificate missing')
                family = CoefficientFamily(template, *(getattr(template, '_family_'+name) for name in _ARRAYS),
                                           parameters, key)
                same(response_calibration_identity(family), identity, 'response family')
                families[key] = family
            family = families[key]
            cached = load_payload(diagnostics['family_calibration_cache_key'], namespace='payload')
            if cached is None:
                raise ValueError('calibration payload missing')
            errors = _validated_errors(cached['errors'], len(family.template.station_x_m))
            _validate_calibration_certificate(cached['certificate'], diagnostics['family_calibration_cache_key'],
                                              errors, cached['diagnostics'])
            for name in ('signed_errors','response_calibration_identity','family_domain','reference_identity'):
                same(cached['diagnostics'][name], diagnostics[name], 'calibration '+name)
            certificate = qualify_policy(family, None, diagnostics, bank=bank)
            reserves = certificate['reserves']
            measured = record['diagnostics']['signed_errors']
            _envelope(measured, len(family.template.station_x_m), name='saved population')
            covered = True
            population = f"{ship}/{level}/seed-{record['seed']}"
            for side in SIDES:
                for metric, values in measured[side].items():
                    if metric == 'gm_m' and side == 'upper_unsafe':
                        continue
                    value_array, reserve_array = np.asarray(values), np.asarray(reserves[side][metric])
                    excess = np.maximum(value_array-reserve_array, 0.)
                    covered &= bool(np.all(excess == 0))
                    for index, (value, percentile, reserve, over) in enumerate(zip(
                        value_array.reshape(-1), np.asarray(measured['p99'][side][metric]).reshape(-1),
                        reserve_array.reshape(-1), excess.reshape(-1))):
                        metric_rows.append(dict(population=population, source=relative, samples=record['samples'],
                            side=side, metric=metric, station_x_m=float(family.template.station_x_m[index])
                            if metric in ('sf_n','bm_nm') else '', maximum=float(value), p99=float(percentile),
                            reserve=float(reserve), excess=float(over)))
            public = record.get('public_checker_comparison')
            public_covered = None
            public_replay = None
            if public:
                public_replay = public_helper(public['captured_affine_prediction'], public['stability_metrics'],
                    public['strength_metrics'], family.template.station_x_m, reserves, family.template.gm_min_m)
                public_covered = all(np.max(value) == 0 for value in
                    public_replay['unsafe_excess_over_default_reserves'].values())
            empty, impossible = reconstructed_intervals(family.template, record['band'], reserves)
            gm = measured['lower_unsafe']['gm_m']
            safe = measured['lower_conservatism']['gm_m']
            cases.append(dict(population=population, source=relative, source_sha256=digest,
                samples=record['samples'], ship=ship, level=level, seed=record['seed'],
                cargo_mass_t=record['manifest']['cargo_mass_kg']/1000,
                displacement_band_t=[record['band']['displacement_min_kg']/1000, record['band']['displacement_max_kg']/1000],
                draught_band_m=[record['band']['draught_min_m'], record['band']['draught_max_m']],
                trim_range_m=[-record['band']['max_abs_trim_m'],record['band']['max_abs_trim_m']],
                gm_unsafe_max_m=gm, gm_unsafe_p99_m=measured['p99']['lower_unsafe']['gm_m'],
                gm_conservatism_max_m=safe, gm_conservatism_p99_m=measured['p99']['lower_conservatism']['gm_m'],
                original_heldout_covered=record['all_measured_unsafe_errors_covered'],
                original_structurally_feasible=record['default_structurally_feasible'],
                final_calibration_envelope_covered=covered, public_capture_covered=public_covered,
                empty_reconstructed_intervals=empty, impossible_list_rows=impossible,
                original_other_impossible_constant_rows=[name for name in record['impossible_default_constant_rows'] if name != 'list_margin'],
                final_policy_certificate=certificate, public_replay=public_replay))
    if sha(_BANK_PATH) != bank_sha or policy_inputs() != expected_policy:
        raise ValueError('bank changed during replay')
    populations = [case['population'] for case in cases]
    if len(populations) != len(set(populations)):
        raise ValueError('duplicate seed/population inputs')
    if not cases:
        raise ValueError('no source populations')
    envelopes = []
    for side, metric in sorted({(row['side'],row['metric']) for row in metric_rows}):
        selected = [row for row in metric_rows if (row['side'],row['metric']) == (side,metric)]
        maximum = max(selected, key=lambda row: row['maximum'])
        percentile = max(selected, key=lambda row: row['p99'])
        envelopes.append(dict(side=side, metric=metric, maximum=maximum['maximum'],
            maximum_population=maximum['population'], maximum_station_x_m=maximum['station_x_m'],
            largest_within_population_p99=percentile['p99'], p99_population=percentile['population'],
            p99_station_x_m=percentile['station_x_m'], maximum_excess=max(row['excess'] for row in selected)))
    result = dict(status='calibration_union_replay', bank_sha256=bank_sha,
        sampled_states=sum(case['samples'] for case in cases), cases=cases, metric_envelopes=envelopes,
        all_recorded_envelopes_covered=all(case['final_calibration_envelope_covered'] for case in cases),
        all_captured_public_states_covered=all(case['public_capture_covered'] is not False for case in cases),
        independent_holdout_pass=False,
        warning='Both populations enrich the bank: replay is calibration coverage, not independent validation or a uniform proof.',
        structural_scope='Only mean/trim/SF/BM reserve intervals and canonical LIST constant rows reconstructed; other instance constants/end-draught port limits/full-plan feasibility not reconstructed.')
    args.output.mkdir(parents=True, exist_ok=True)
    serializable = runpy.run_path(str(HERE/'d30_validate.py'))['serializable']
    (args.output/'replay.json').write_text(json.dumps(serializable(result), indent=2, allow_nan=False)+'\n')
    with (args.output/'metrics.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metric_rows[0])); writer.writeheader(); writer.writerows(metric_rows)
    lines = ['# Measured-union calibration replay', '', result['warning'], '',
        f"Recorded states: {result['sampled_states']}. Directional envelopes covered: {result['all_recorded_envelopes_covered']}.", '',
        '| Population | Cargo (t) | Displacement band (t) | Draught band (m) | Trim range (m) | GM unsafe max/p99 (m) | GM safe gap max/p99 (m) | LIST impossible |',
        '|---|---:|---|---|---|---|---|---|']
    for case in cases:
        span = lambda values: ' to '.join(f'{value:.5g}' for value in values)
        lines.append(f"| [{case['population']}]({ROOT/case['source']}) | {case['cargo_mass_t']:.5g} | "
            f"{span(case['displacement_band_t'])} | {span(case['draught_band_m'])} | {span(case['trim_range_m'])} | "
            f"{case['gm_unsafe_max_m']:.6g}/{case['gm_unsafe_p99_m']:.6g} | "
            f"{case['gm_conservatism_max_m']:.6g}/{case['gm_conservatism_p99_m']:.6g} | {bool(case['impossible_list_rows'])} |")
    lines += ['', 'Largest p99 below is a maximum of original within-population/station percentiles; it is not pooled.', '',
        '| Direction / metric | Max unsafe | Largest original p99 | Max excess over final reserve |', '|---|---:|---:|---:|']
    for item in envelopes:
        lines.append(f"| {item['side']}/{item['metric']} | {item['maximum']:.7g} | {item['largest_within_population_p99']:.7g} | {item['maximum_excess']:.7g} |")
    lines += ['', result['structural_scope'], '', 'Per-population, per-direction and per-station values: `metrics.csv`.']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({key:result[key] for key in ('status','sampled_states','all_recorded_envelopes_covered',
                                                 'all_captured_public_states_covered','independent_holdout_pass')}))
    if not result['all_recorded_envelopes_covered'] or not result['all_captured_public_states_covered']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
