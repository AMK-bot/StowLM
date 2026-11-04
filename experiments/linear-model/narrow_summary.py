"""Assemble completed case shards without concealing held-out counterexamples."""
from __future__ import annotations

import json
import argparse
import os
import runpy
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_KEYS = ('gm_m', 'trim_m', 'list_rad', 'draught_m', 'sf_n', 'bm_nm')
_FLOORS = dict(gm_m=1e-6, trim_m=1e-6, list_rad=1e-8, draught_m=1e-6, sf_n=.01, bm_nm=1.)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=_HERE)
    args = parser.parse_args()
    directory = args.directory
    from stowage.linear.cache import source_fingerprint
    current_source = source_fingerprint()
    experiment = runpy.run_path(str(_HERE/'narrow.py'))
    epoch_audit = runpy.run_path(str(_HERE/'audit_loader_epoch.py'))
    schema_audit = runpy.run_path(str(_HERE/'audit_schema13_epoch.py'))
    rejected = []
    cases = {}
    errors = ['# Nine fixed-manifest cases: held-out errors', '',
              'Maxima and p99 below are measured on 128 held-out conditions (seed 3712).',
              'Calibration uses 192 other conditions; every station remains in JSON.',
              'SF/BM table cells take the largest per-station maximum and largest per-station p99; these may occur at different stations.',
              'Bands and hard operating limits are recorded per case. Error measurements do not approve departures.', '',
              '| Ship | Cargo level | GM max / p99 (m) | Trim max / p99 (m) | List max / p99 (rad) | Draught max / p99 (m) | SF max / p99 (N) | BM max / p99 (N m) |',
              '|---|---|---:|---:|---:|---:|---:|---:|']
    gm_table = ['# GM calibration and held-out comparison', '',
                '| Ship | Level | Cargo mass (kg) | Calibration max, 192 (m) | Held-out max, 128 (m) | Known union max, 320 (m) | Held-out p99 (m) |',
                '|---|---|---:|---:|---:|---:|---:|']
    bands = ['# Declared bands and empirical envelope comparison', '',
             '| Ship | Level | Cargo (kg) | Displacement band (kg) | Mean draught band (m) | Estimation trim / heel bound (m / rad) | Hull trim / heel limit | Calibration / held-out count | Held-out / calibration error factor |',
             '|---|---|---:|---|---|---|---|---:|---:|']
    for ship in ('feeder_500', 'feeder_1000', 'feeder_1700'):
        for level in ('light', 'medium', 'full'):
            path = directory/f'narrow-{ship}-{level}.json'
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            direct_epoch = data.get('source_clean') and data.get('source_fingerprint_end') == current_source
            audited_epoch = epoch_audit['qualified'](path,data,current_source)
            audited_schema = schema_audit['qualified'](path,data,current_source)
            if directory != _HERE and (not (direct_epoch or audited_epoch or audited_schema) or not data.get('reference_compatible',True)
                    or data.get('stability_free_surface_method') != os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1')
                    or data.get('calibration',{}).get('samples')!=192 or data.get('diagnostics',{}).get('samples')!=128
                    or data.get('input_hashes_end') != experiment['input_snapshot'](ship)
                    or data.get('departure_manifest_identity') != experiment['current_manifest_identity'](ship,level)):
                rejected.append(f'{ship}/{level}')
                continue
            data['source_fingerprint_at_summary'] = current_source
            if audited_epoch:
                data['loader_only_epoch_audit'] = json.loads(path.with_suffix('.epoch-audit.json').read_text())
            if audited_schema:
                data['schema13_metadata_epoch_audit'] = json.loads(path.with_suffix('.schema-audit.json').read_text())
            level_label=level+' (HC under deck)' if data['manifest'].get('hc_under_deck_only') else level
            data['matches_current_summary_source'] = data.get('source_fingerprint_end') == current_source
            envelope = {key: np.maximum(np.asarray(data['model_errors'][key]),
                                        np.asarray(data['sweep_errors'][key])).tolist() for key in _KEYS}
            ratios = {key: float(np.max(np.asarray(data['sweep_errors'][key])/
                                        np.maximum(np.asarray(data['model_errors'][key]), _FLOORS[key])))
                      for key in _KEYS}
            data['known_measured_envelope_320'] = envelope
            data['heldout_over_calibration_factors'] = ratios
            cases[f'{ship}/{level}'] = data
            gm_table.append(f"| {ship} | {level_label} | {data['manifest']['cargo_mass_kg']:.3f} | "
                            f"{data['model_errors']['gm_m']:.8g} | {data['sweep_errors']['gm_m']:.8g} | "
                            f"{envelope['gm_m']:.8g} | {data['diagnostics']['p99']['gm_m']:.8g} |")
            cells = []
            for key in _KEYS:
                maximum = float(np.max(data['diagnostics']['max'][key]))
                p99 = float(np.max(data['diagnostics']['p99'][key]))
                cells.append(f'{maximum:.7g} / {p99:.7g}')
            errors.append(f'| {ship} | {level_label} | '+' | '.join(cells)+' |')
            band = data['band']; diagnostic = data['diagnostics']
            bands.append(f"| {ship} | {level_label} | {data['manifest']['cargo_mass_kg']:.3f} | "
                         f"{band['displacement_min_kg']:.3f}–{band['displacement_max_kg']:.3f} | "
                         f"{band['draught_min_m']:.6g}–{band['draught_max_m']:.6g} | "
                         f"{band['max_abs_trim_m']:.6g} / {band['max_abs_heel_rad']:.6g} | "
                         f"{data['operating_trim_limit_m']:.6g} / {data['operating_list_limit_rad']:.6g} | "
                         f"{data['calibration']['samples']} / {diagnostic['samples']} | {max(ratios.values()):.5g} |")
    bands.extend(['', 'Factors use numerical floors solely to avoid dividing station-closure roundoff by zero:',
                  '`GM/trim/draught 1e-6 m; list 1e-8 rad; SF 0.01 N; BM 1 N m`. Raw errors are preserved.',
                  'The 320-condition envelope is the componentwise maximum of calibration and held-out errors;',
                  'it does not silently overwrite the production 192-condition calibration. A larger required',
                  'margin is reported honestly when a held-out counterexample exceeds calibration.'])
    (directory/'narrow-results.json').write_text(json.dumps(cases, indent=2)+'\n')
    (directory/'narrow-errors.md').write_text('\n'.join(errors)+'\n')
    (directory/'narrow-gm.md').write_text('\n'.join(gm_table)+'\n')
    (directory/'narrow-bands.md').write_text('\n'.join(bands)+'\n')
    margin_rows=['# Held-out / calibration factors by metric','',
                 'Factors use the numerical floors stated in narrow-bands.md; raw errors are unchanged.',
                 'Default 1.1 coverage below is empirical above those floors, not a uniform guarantee.','',
                 '| Case | GM | Trim | List | Draught | SF | BM | Scale 1.1 exceeds observed factors? |',
                 '|---|---:|---:|---:|---:|---:|---:|---|']
    for key,data in cases.items():
        ratios=data['heldout_over_calibration_factors']
        margin_rows.append('| '+key+' | '+' | '.join(f'{ratios[k]:.7g}' for k in _KEYS)+
                           ' | '+('yes' if max(ratios.values())<1.1 else 'NO')+' |')
    (directory/'narrow-margin-factors.md').write_text('\n'.join(margin_rows)+'\n')
    production = ['# Production inputs and public-physics crosschecks', '',
                  f'Executable source fingerprint at aggregation: `{current_source}`.', '',
                  'Each retained case has an unchanged current epoch or a separately recorded strict loader/schema-metadata equivalence audit. Original dirty flags are preserved.',
                  'Audits require all numerical linear/hydro/stability/strength hashes, non-schema versions and raw inputs unchanged; exact or explicitly reversible schema inputs must hit the original-source cache key with identical calibration vectors.',
                  'Published parent offsets are scaled to synthetic feeder dimensions; these are not surveyed commercial ships.', '',
                  '| Ship | Cells | Lpp × beam × depth (m) | Design draught (m) | Strength stations | Strength-envelope SHA-256 | Offset parent / path | Offset SHA-256 |',
                  '|---|---:|---|---:|---:|---|---|---|']
    for ship in ('feeder_500','feeder_1000','feeder_1700'):
        matching = [value for key,value in cases.items() if key.startswith(ship+'/')]
        if not matching: continue
        proof = matching[0].get('production_proof', {})
        if proof:
            dimensions=proof['hull_dimensions_m']
            size=' × '.join(f'{dimensions[key]:.7g}' for key in ('lpp_m','beam_m','depth_m'))
            production.append(f"| {ship} | {proof['cell_count']} | {size} | {dimensions['design_draught_m']:.7g} | {proof['strength_station_count']} | `{proof['strength_envelope_sha256']}` | {proof['offsets_parent']} / `{proof['offsets_path']}` | `{proof['offsets_sha256_actual']}` |")
    production.extend(['', 'A captured accepted held-out physical loading is represented as point loads at its true box centres, including known HC heights.',
                       'This compares GM/trim/list/SF/BM against the public physics; it does not verify cargo GZ, weather, packing legality or approve a plan.', '',
                       '| Ship / case | GM difference (m) | Trim difference (m) | List difference (rad) | SF max difference (N) | BM max difference (N m) |',
                       '|---|---:|---:|---:|---:|---:|'])
    for key,data in cases.items():
        comparison = data.get('public_checker_comparison')
        if comparison and 'absolute_differences' in comparison:
            delta = comparison['absolute_differences']
            production.append('| '+key+' | '+' | '.join(f'{delta[metric]:.8g}' for metric in ('gm_m','trim_m','list_rad','sf_n','bm_nm'))+' |')
    production.extend(['', '| Case | Context (s) | Cold or cached build (s) | Held-out sweep (s) | Public checker (s) | Peak process RSS (MiB) |',
                       '|---|---:|---:|---:|---:|---:|---:|'])
    for key,data in cases.items():
        production.append('| '+key+' | '+' | '.join(f'{data.get(metric,0.):.3f}' for metric in
            ('context_seconds','build_seconds','sweep_seconds','public_checker_seconds','peak_rss_mib'))+' |')
    (directory/'narrow-production.md').write_text('\n'.join(production)+'\n')
    if rejected:
        print('Rejected dirty/stale cases:', rejected)
    print(json.dumps({key: {'gm_max_m': value['diagnostics']['max']['gm_m'],
                           'factor': max(value['heldout_over_calibration_factors'].values())}
                      for key,value in cases.items()}, indent=2))


if __name__ == '__main__':
    main()
