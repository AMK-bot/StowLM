"""Render saved method-choice measurements; no geometry or benchmark is run.

Final departure files take precedence for timings. An earlier departure's paired
method comparison may be used when its parameters and physical loading match;
both artifact paths remain visible. Validation_final.json supersedes per-vessel
validation files when present. The SVG uses saved numerical curve nodes only.
"""
from __future__ import annotations

import argparse
from html import escape
import json
import math
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
FLEET = ('feeder_500', 'feeder_1000', 'feeder_1700')
AREA_KEYS = ('area_0_30_mrad', 'area_0_40_mrad', 'area_30_40_mrad', 'area_0_flood_mrad')


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def number(value, digits=6):
    return 'pending' if value is None else f'{value:.{digits}g}'


def validation_rows(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if 'validation' in payload:
        return validation_rows(payload['validation'])
    if 'density_kg_m3' in payload:
        return [payload]
    return [row for value in payload.values() if isinstance(value, (dict, list))
            for row in validation_rows(value)]


def load_departures(directory):
    result = []
    for name in FLEET:
        original_path = directory/f'departure_{name}.json'
        final_path = directory/f'departure_final_{name}.json'
        original = read(original_path)
        final = read(final_path)
        primary, path = (final, final_path) if final else (original, original_path)
        paired, pair_path = primary, path
        if primary and 'comparison' not in primary and original and 'comparison' in original:
            same = all(primary.get(key) == original.get(key) for key in (
                'parameters_sha256', 'target_displacement_kg', 'gross_mass_per_box_kg',
                'container_count', 'tank_masses_kg'))
            same &= (primary.get('method2_cold', {}).get('loading_details', {}).get('loading_centroid_m')
                     == original.get('method2_cold', {}).get('loading_details', {}).get('loading_centroid_m'))
            if same:
                paired, pair_path = original, original_path
        result.append(dict(name=name, primary=primary, path=path.name,
                           paired=paired, pair_path=pair_path.name))
    return result


def curve_svg(rows, path):
    width, height = 1140, 400
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:system-ui,sans-serif;fill:#202830} .axis{stroke:#73808c;stroke-width:1} .grid{stroke:#e6eaee;stroke-width:1}</style>',
        '<text x="24" y="25" font-size="17">Loaded departure GZ: matched small-tank exemptions</text>',
        '<text x="24" y="48" font-size="12" fill="#1f77b4">Blue: method .2 upright-inertia correction</text>',
        '<text x="480" y="48" font-size="12" fill="#d95f02">Orange dashed: method .1 fluid transfer</text>']
    for index, row in enumerate(rows):
        left, top, w, h = 48+index*375, 92, 305, 235
        paired = row['paired'] or {}
        data = [(paired.get('method2_cold', {}).get('curve'), '#1f77b4', ''),
                (paired.get('method1', {}).get('curve'), '#d95f02', ' stroke-dasharray="6 4"')]
        present = [curve for curve, _, _ in data if curve]
        parts.append(f'<text x="{left}" y="77" font-size="15">{escape(row["name"])}</text>')
        if not present:
            parts.append(f'<text x="{left}" y="130" font-size="14">Pending measurements</text>')
            continue
        xmax = max(max(c['angles_rad']) for c in present)*180/math.pi
        values = [v for c in present for v in c['gz_m']]
        low, high = min(0., min(values)), max(values)
        padding = max(.03, (high-low)*.12)
        low, high = low-padding, high+padding
        x = lambda a: left+w*a/xmax
        y = lambda g: top+h*(high-g)/(high-low)
        for j in range(5):
            gy = low+(high-low)*j/4
            parts += [f'<line class="grid" x1="{left}" y1="{y(gy):.3f}" x2="{left+w}" y2="{y(gy):.3f}"/>',
                      f'<text x="{left-7}" y="{y(gy)+4:.3f}" text-anchor="end" font-size="10">{gy:.2f}</text>']
        for gx in range(0, int(xmax)+1, 10):
            parts += [f'<line class="grid" x1="{x(gx):.3f}" y1="{top}" x2="{x(gx):.3f}" y2="{top+h}"/>',
                      f'<text x="{x(gx):.3f}" y="{top+h+18}" text-anchor="middle" font-size="10">{gx}</text>']
        parts += [f'<line class="axis" x1="{left}" y1="{y(0):.3f}" x2="{left+w}" y2="{y(0):.3f}"/>',
                  f'<text x="{left+w/2}" y="{top+h+39}" text-anchor="middle" font-size="12">Heel (degrees)</text>',
                  f'<text x="{left}" y="{top-5}" font-size="11">GZ (m)</text>']
        for curve, color, dash in data:
            if curve:
                points = ' '.join(f'{x(a*180/math.pi):.3f},{y(g):.3f}'
                                  for a, g in zip(curve['angles_rad'], curve['gz_m'], strict=True))
                parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
        if not paired.get('method1'):
            parts.append(f'<text x="{left+8}" y="{top+18}" font-size="11">Method .1 pending</text>')
    parts.append('</svg>')
    path.write_text('\n'.join(parts)+'\n')


def summarize(directory):
    rows = load_departures(directory)
    final_validation = directory/('validation_final_current.json' if (directory/'validation_final_current.json').exists() else 'validation_final.json')
    if final_validation.exists():
        vals = validation_rows(read(final_validation))
        validation_sources = [final_validation.name]
    else:
        sources = sorted(directory.glob('validation_feeder_*.json'))
        vals = [row for path in sources for row in validation_rows(read(path))]
        validation_sources = [path.name for path in sources]
    lines = ['# Production free-surface method .2', '',
        'IS Code Part B 3.1.9.2 permits a righting-lever correction based on the upright free-surface inertia. '
        'The local [Code text](../../../data/raw/is-code/msc267-85.txt) (PDF page 40, printed page 39) says:', '',
        '> correction based on the moment of inertia, calculated at 0° angle of heel, modified at each angle of heel calculated.', '',
        'The clause supplies no angular-modification formula. The selected interpretation is a virtual rise of KG: '
        '`FSC = ΣρI₀/Δ`, `δGZ(φ) = FSC sinφ`. This is the production choice allowed by method .2, not an approximation '
        'to the distinct actual-transfer method .1. Part B 3.1.8 always uses 0° inertia for the GM correction; '
        'Part B 3.1.11 calls for one selected method in the stability booklet. Method .1 remains a verification mode.', '',
        'The optional Part B 3.1.12 exemption uses the stricter sufficient test `ρI₀/lightship_mass < 0.01 m` for each tank. '
        'The Code minimum service displacement includes lightship plus 10% stores and necessary ballast, so lightship '
        'is a conservative lower bound. Using the full upright moment rather than its method-.2 value at 30° is also '
        'conservative. An exemption is applied only when this stronger test passes. Actual liquid masses and upright '
        'centroids remain in loading moments. Fixed ballast uses its stated fill; carried consumables retain whole-usable-range '
        'maxima. The synthetic fleet does not supply voyage transfer ranges or approved minimum-service conditions.', '',
        'Upright centroids, inertia and whole-fill maxima come from persisted shape-aware sounding data. The frozen model table has only quarter-fill rows; the checker therefore precomputes an exact analytic sounding table from the same 2.1 section solid, rather than interpolating those sparse rows. Against 330 provider soundings, centroid errors were at most 3.84e-13 m and FSM errors 2.10e-11 m⁴. '
        'The sounding lookup evaluates exact piecewise slice polynomials without sampled-fill interpolation error. Upstream hull-section quadrature retains its documented approximation labels; '
        'choosing method .2 introduces no rectangular tank substitution.', '',
        '## Cached production performance', '',
        'Cold means the first checker call in a fresh benchmark process with disk caches present. Vessel/instance construction '
        'is excluded. Warm means five calls in that same process. Targets: ≤0.5 s warm and approximately ≤5 s cached cold.', '',
        '| Vessel | Cold s | Warm min s | Warm median s | Warm max s | Qualified KN used |',
        '|---|---:|---:|---:|---:|---:|']
    for row in rows:
        d = row['primary'] or {}
        cold = d.get('method2_cold', {})
        times = [c['seconds'] for c in d.get('method2_warm', [])]
        stats = (min(times), median(times), max(times)) if len(times) == 5 else (None, None, None)
        lines.append('| '+row['name']+' | '+' | '.join(number(v) for v in (cold.get('seconds'), *stats,
                     cold.get('metrics', {}).get('geometry_table_used')))+' |')
    lines += ['', 'Timing artifacts: '+', '.join(f'[{r["path"]}]({r["path"]})' for r in rows)+'.', '',
        '## Loaded criteria', '',
        'Synthetic diagnostic: homogeneous boxes in all holds and the lowest two deck tiers, ballast20%, HFO50%, '
        'other liquids empty, total mass90% of design displacement. These are stability diagnostics, not approved voyages '
        'or full-checker acceptances. Lpp > 100 m selects B2.3 instead of A2.2.1–3; initial GM and weather remain active.', '',
        '| Vessel | GM m / A2.2.4 | B2.3 passed / 7 | Weather ratio / result | Omitted tanks | Loading issues |',
        '|---|---|---:|---|---:|---|']
    for row in rows:
        c = (row['primary'] or {}).get('method2_cold', {})
        m, margins = c.get('metrics', {}), c.get('margins', {})
        b = [v for k, v in margins.items() if k.startswith('containership_')]
        weather = [v for k, v in margins.items() if k.startswith('weather_')]
        issues = sorted({v['rule_id'] for v in c.get('violations', [])
                         if v['rule_id'].startswith(('iscode.loading.', 'port.call.'))})
        gm = number(m.get('gm_m'))+'/'+('pass' if margins.get('gm_m', -1) >= 0 else 'fail')
        wp = 'pass' if weather and min(weather) >= 0 else ('fail' if weather else 'pending')
        lines.append(f'| {row["name"]} | {gm} | {sum(v >= 0 for v in b)}/{len(b)} | '
                     f'{number(m.get("weather_ratio"))}/{wp} | {number(m.get("free_surface_small_tanks_omitted"))} | '
                     f'{", ".join(issues) or ("none" if c else "pending")} |')
    lines += ['', 'Per-criterion B2.3 margins (measured minus minimum; nonnegative passes):', '',
              '| Criterion | feeder_500 | feeder_1000 | feeder_1700 |',
              '|---|---:|---:|---:|']
    for key in ('downflooding_30_margin_rad', 'area_0_30_mrad', 'area_0_40_mrad',
                'area_30_40_mrad', 'gz_at_or_above_30_m', 'gz_max_m', 'area_0_flood_mrad'):
        values = []
        for row in rows:
            c = (row['primary'] or {}).get('method2_cold', {})
            value = c.get('margins', {}).get('containership_'+key)
            values.append(number(value)+('/pass' if value is not None and value >= 0 else '/fail' if value is not None else ''))
        lines.append('| '+key+' | '+' | '.join(values)+' |')
    lines += ['', '## Method .1 versus method .2', '',
        'Signed differences are method .2 minus method .1. The default controlled verification applies exactly the '
        'production omission IDs to method .1 correction tanks, retaining every actual liquid mass and physical CG. '
        'This experiment-only wrapper differs from ordinary environment-selected method .1, which retains all tanks. '
        'Thus the paired results compare method choice with matching exemptions. Any legacy-policy result is marked below. '
        'Pointwise maxima use piecewise-linear interpolation of saved curves over common coverage; criterion-area differences '
        'use the checker’s own integrals and may have different flooding/vanishing endpoints.', '',
        '| Vessel | Common end ° | Max abs ΔGZ m | Δarea0–30 m·rad | Δarea0–40 m·rad | Δarea30–40 m·rad | Δtotal B area m·rad | ΔGM m |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        paired = row['paired'] or {}
        c = paired.get('comparison', {})
        area = c.get('criterion_area_metric_differences', {})
        values = (c.get('common_end_deg'), c.get('max_abs_gz_difference_m'),
                  *(area.get(key) for key in AREA_KEYS), c.get('gm_difference_m'))
        lines.append('| '+row['name']+' | '+' | '.join(number(v) for v in values)+' |')
        if c and not paired.get('same_exemptions'):
            lines += ['', f'**{row["name"]}: legacy exemption policy; differences include tank omissions.**', '']
    lines += ['', 'Paired-comparison artifacts: '+', '.join(f'[{r["pair_path"]}]({r["pair_path"]})' for r in rows)+'. '
        'When final timing files are separate, the earlier self-contained paired result is reused only when parameter hash, '
        'cargo count/mass, displacement, tank masses and loading centroid agree. Incidental instance identifiers may differ.', '',
        '![Saved method .1 and .2 curves](curves.svg)', '', '## KN interpolation qualification', '',
        'Local tensor cubic interpolation uses four nodes per coordinate, without extrapolation. Conditions outside the '
        'qualified mass/grid domain and unresolved numerical cases use the direct reference. Cached tables require '
        'current source/parameter/version/density hashes and independent qualification. Version-only rekeys retain original SHA-bound evidence only after every physics metadata field and numerical array is verified identical. These are empirical holdout measurements, not a uniform mathematical bound.', '',
        'Each pair below is maximum absolute error / maximum fraction of its IACS UR L5 tolerance. The target fraction is 0.2. '
        'GZ tolerance is max(5%,0.05 m), area max(5%,0.0012 m·rad), GM max(1%,0.05 m). '
        'GZ includes the separate broad geometric holdout as well as complete curves.', '',
        '| Vessel | Density kg/m³ | GZ m / fraction | Area m·rad / fraction | GM m / fraction | Complete curves / broad states |',
        '|---|---:|---:|---:|---:|---:|']
    maxima = {'gz': 0., 'area': 0., 'gm': 0.}
    for v in vals:
        errors = v.get('errors', {})
        gz, area, gm = (errors.get(k, {}) for k in ('gz_m', 'area_mrad', 'gm_m'))
        broad = v.get('broad_geometric_validation', {})
        bg = broad.get('errors', {})
        gaz = max(gz.get('max_absolute', 0), bg.get('max_absolute', 0))
        gf = max(gz.get('max_l5_fraction', 0), bg.get('max_l5_fraction', 0))
        maxima['gz'] = max(maxima['gz'], gf)
        maxima['area'] = max(maxima['area'], area.get('max_l5_fraction', 0))
        maxima['gm'] = max(maxima['gm'], gm.get('max_l5_fraction', 0))
        lines.append(f'| {v.get("vessel", "unknown")} | {number(v.get("density_kg_m3"))} | '
            f'{number(gaz)} / {number(gf)} | {number(area.get("max_absolute"))} / {number(area.get("max_l5_fraction"))} | '
            f'{number(gm.get("max_absolute"))} / {number(gm.get("max_l5_fraction"))} | '
            f'{v.get("completed_samples", 0)} / {broad.get("completed_samples", 0)} |')
    lines += ['', f'Across {len(vals)} of 6 expected vessel-density rows, maximum normalized errors: '+', '.join(f'{k}={number(v)}' for k, v in maxima.items())+'.'+
        (' Remaining final validation rows are pending.' if len(vals) < 6 else ''), '',
        'Validation artifacts: '+', '.join(f'[{p}]({p})' for p in validation_sources)+'.', '',
        'Loaded method-.2 table-minus-direct check:', '',
        '| Vessel | Max abs ΔGZ m | Common absolute area error m·rad | ΔGM m | Same violation IDs |',
        '|---|---:|---:|---:|---|']
    for row in rows:
        d = row['primary'] or {}
        c = d.get('interpolation_error_loaded', {})
        same = (d.get('method2_direct', {}).get('violation_ids') == d.get('method2_cold', {}).get('violation_ids')) if c else 'pending'
        lines.append(f'| {row["name"]} | {number(c.get("max_abs_gz_difference_m"))} | '
            f'{number(c.get("common_interval_absolute_difference_area_mrad"))} | {number(c.get("gm_difference_m"))} | {same} |')
    lines += ['', '## Proposed leader-only decision text', '',
        'Production intact-stability corrections select IS Code Part B 3.1.9.2: upright inertia with the documented '
        'virtual-KG-rise sinφ interpretation. Method .1 remains independent verification. Adopt the conservative small-tank '
        'exemption using full uprightρI₀/lightship mass<0.01 m, retain actual tank mass/centres, and retain fixed-fill ballast '
        'and whole-range consumable assumptions. The selected Code-permitted method is not a physics approximation; '
        'upstream numerical tank/hull representations keep their existing approximation labels. Record the matched-exemption '
        'method differences and cache timings in evaluation. The checker owner performs any shared version bump.', '']
    (directory/'report.md').write_text('\n'.join(lines))
    curve_svg(rows, directory/'curves.svg')
    return dict(departures=len([r for r in rows if r['primary']]), validation_rows=len(vals),
                paired_comparisons=len([r for r in rows if (r['paired'] or {}).get('comparison')]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(summarize(args.directory), indent=2))
