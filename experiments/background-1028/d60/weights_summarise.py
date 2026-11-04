"""Render the saved public-provider samples; never generates demand."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent

def main():
    before = json.loads((HERE / 'weights-before.json').read_text())
    after = json.loads((HERE / 'weights-after.json').read_text())
    assert before['params_sha256'] == after['params_sha256']
    before_samples = [json.loads(line) for line in (HERE / 'weights-before.samples.jsonl').read_text().splitlines()]
    after_samples = [json.loads(line) for line in (HERE / 'weights-after.samples.jsonl').read_text().splitlines()]
    paired_keys = ['profile', 'route', 'seed', 'tare_kg', 'csc_rating_kg', 'empty', 'pod']
    assert len(before_samples) == len(after_samples) == before['n_total']
    assert all(all(old[key] == new[key] for key in paired_keys)
               for old, new in zip(before_samples, after_samples, strict=True))
    assert all(new['gross_mass_kg'] <= old['gross_mass_kg'] + 1e-8
               for old, new in zip(before_samples, after_samples, strict=True))
    assert all(new['gross_mass_kg'] <= 30_500 for new in after_samples)
    assert all(old == new for old, new in zip(before_samples, after_samples, strict=True)
               if old['profile'] in ('dry_20', 'empty_dry20'))
    pair_verification = {'n_pairs': len(before_samples), 'nonmass_fact_keys_preserved': paired_keys,
                         'all_nonmass_facts_identical': True, 'all_after_masses_no_greater': True,
                         'all_masses_within_cap': True, 'dry20_records_identical': True}
    (HERE / 'weights-pair-verification.json').write_text(json.dumps(pair_verification, indent=2) + '\n')
    rows = []
    for old, new in zip(before['profiles'], after['profiles'], strict=True):
        assert old['profile'] == new['profile']
        assert old['csc_rating_kg'] == new['csc_rating_kg']
        assert old['route_counts'] == new['route_counts']
        assert old['all']['n'] == new['all']['n'] >= 500
        assert new['all']['above_30500_count'] == 0
        rows.append({'profile': old['profile'], 'n_before': old['all']['n'], 'n_after': new['all']['n'],
                     'csc_rating_kg': old['csc_rating_kg'],
                     'before': old['all'], 'after': new['all'],
                     'loaded_before': old['loaded'], 'loaded_after': new['loaded']})
    result = {'rows': rows, 'before_source_sha256': before['source_sha256'],
              'after_source_sha256': after['source_sha256'], 'params_sha256': before['params_sha256'],
              'weighted_equipment_share_above_cap_before': sum(row['source_equipment_share'] * row['all']['above_30500_share'] for row in before['profiles'] if row['profile'] != 'empty_dry20'),
              'weighted_equipment_mean_before_kg': sum(row['source_equipment_share'] * row['all']['mean_kg'] for row in before['profiles'] if row['profile'] != 'empty_dry20'),
              'weighted_equipment_mean_after_kg': sum(row['source_equipment_share'] * row['all']['mean_kg'] for row in after['profiles'] if row['profile'] != 'empty_dry20')}
    (HERE / 'weights-comparison.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    lines = ['# D60 cargo mass distribution', '',
             'Each row contains 5,000 boxes before and 5,000 after. Mass statistics include the source empty/full mix; separate loaded-only statistics are in the JSON. Plate ratings are unchanged.', '',
             '| Equipment profile | CSC kg | Mean before kg | Mean after kg | p95 before kg | p95 after kg | Above 30.5 t before | Above after |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        old, new = row['before'], row['after']
        lines.append(f"| {row['profile']} | {row['csc_rating_kg']:,.0f} | {old['mean_kg']:,.1f} | {new['mean_kg']:,.1f} | {old['p95_kg']:,.1f} | {new['p95_kg']:,.1f} | {old['above_30500_share']:.2%} | {new['above_30500_share']:.2%} |")
    lines += ['', 'Sampling preserves each equipment profile’s source empty probability, POD probabilities and event rates. Equipment is conditioned one row at a time, and route quotas follow configured call shares (50%/30%/20%). The dry_20 row therefore also covers the dense_20ft and dry20_with_dg/tiny mass populations. The empty_dry20 diagnostic is an additional all-empty row. No vessel gate or slot filtering is applied; this measures the cargo provider before downstream selection.', '',
              'After sampling uses the exact inverse CDF of the original transformed gross law conditional on the D60 bound, holding each selected equipment/POD/empty state fixed. It does not change triangular support/mode parameters or reject whole boxes/equipment classes. One uniform draw is consumed per loaded box in both versions, allowing the same-seed comparison of non-mass shipment facts.', '',
              f"All {len(before_samples):,} paired records retain identical equipment, POD, empty/full, tare and CSC plate facts; no after mass exceeds its same-uniform before mass. Both dry20 profiles are bit-identical before/after.", '',
              'The source has seven equipment rows in the sole params/cargo profile, feeder_export.toml. No parameter file or CSC plate value was edited. p95 uses linear interpolation at rank (n−1)×0.95.', '',
              f"Using the source equipment shares to combine these strata gives an estimated pre-cap tail share of {result['weighted_equipment_share_above_cap_before']:.3%}; mean gross changes {result['weighted_equipment_mean_before_kg']:,.1f} → {result['weighted_equipment_mean_after_kg']:,.1f} kg. These are synthetic-profile estimates, not measured traffic.", '',
              'Commands (each with OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache):', '',
              '```sh',
              'uv run python experiments/background-1028/d60/weights_measure.py before --n-per-profile 100 --out experiments/background-1028/d60/weights-pilot-before.json',
              'uv run python experiments/background-1028/d60/weights_measure.py before --n-per-profile 5000',
              'uv run python experiments/background-1028/d60/weights_measure.py after --n-per-profile 5000',
              'uv run python experiments/background-1028/d60/weights_summarise.py',
              '```', '',
              'The before source is an exact local snapshot of the previously active cargo provider, captured before the D60 edits. Both sample files retain per-box gross/tare/plate/empty/class/POD facts. Source and parameter SHA256 values, PIDs, timings and peak RSS are recorded in weights-before.json and weights-after.json.', '',
              'Compute: one CPU, no GPU. The 800-box pilot took 0.286 s with 43.8 MB peak RSS; conservative scaling estimated 14.3 s for 40,000 boxes. '
              f"Before actual: {before['wall_s']:.3f} s and {before['maxrss_bytes_macos'] / 1e6:.1f} MB peak RSS. After actual: {after['wall_s']:.3f} s and {after['maxrss_bytes_macos'] / 1e6:.1f} MB peak RSS."]
    (HERE / 'weights-report.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines[:16]))

if __name__ == '__main__':
    main()
