"""Independent arithmetic checks of archived ordinates, curves and tables.

No production criterion integrator or saved pass flag is used to derive an
expected value. Angles are integrated in degrees segment by segment, then
converted to radians, with exact linear clipping at each integration bound.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

AREA_COLUMNS = {
    'area_0_30_mrad': 'area_0_30_mrad',
    'area_0_40_mrad': 'area_0_min40_f_mrad',
    'area_30_40_mrad': 'area_30_min40_f_mrad',
    'area_0_flood_mrad': 'area_0_f_mrad',
}


def linear_curve_area(points, lower_deg, upper_deg):
    """Integrate independently over clipped linear segments, returning m*rad."""
    curve = [(float(p['heel_deg']), float(p['gz_m'])) for p in points]
    assert len(curve) >= 2
    assert all(math.isfinite(a) and math.isfinite(g) for a, g in curve)
    assert all(a[0] < b[0] for a, b in zip(curve, curve[1:]))
    if upper_deg <= lower_deg:
        return 0.
    assert curve[0][0] <= lower_deg + 1e-10
    assert curve[-1][0] >= upper_deg - 1e-10
    area_degrees = 0.
    for (a, ga), (b, gb) in zip(curve, curve[1:]):
        left, right = max(a, lower_deg), min(b, upper_deg)
        if right <= left:
            continue
        slope = (gb - ga) / (b - a)
        gl, gr = ga + slope * (left - a), ga + slope * (right - a)
        area_degrees += (right - left) * (gl + gr) / 2.
    return math.radians(area_degrees)


def curve_area_metrics(row):
    points = row['regulatory_curve']
    flood = float(row['flooding_deg'])
    end = min(flood, float(points[-1]['heel_deg']))
    positive_seen = False
    for a, b in zip(points, points[1:]):
        positive_seen |= a['gz_m'] > 0
        if positive_seen and a['gz_m'] > 0 >= b['gz_m']:
            crossing = a['heel_deg'] + (b['heel_deg'] - a['heel_deg']) * a['gz_m'] / (a['gz_m'] - b['gz_m'])
            end = min(end, crossing)
            break
    assert abs(row['total_area_end_deg'] - end) < 1e-7
    upper = min(40., flood, float(points[-1]['heel_deg']))
    return {
        'area_0_30_mrad': linear_curve_area(points, 0., min(30., flood)),
        'area_0_40_mrad': linear_curve_area(points, 0., upper),
        'area_30_40_mrad': linear_curve_area(points, 30., upper),
        'area_0_flood_mrad': linear_curve_area(points, 0., end),
    }


def verify_curve_areas(row, *, tolerance=1e-10):
    expected = curve_area_metrics(row)
    for name, area in expected.items():
        assert abs(row['metrics'][name] - area) <= tolerance, (name, row['metrics'][name], area)
    return expected


def verify_feeder_table(path, baselines):
    with Path(path).open(newline='') as f:
        table = list(csv.DictReader(f))
    assert len(table) == len(baselines)
    assert {r['ship'] for r in table} == set(baselines)
    for record in table:
        row = baselines[record['ship']]
        expected = verify_curve_areas(row)
        for metric, column in AREA_COLUMNS.items():
            assert abs(float(record[column]) - expected[metric]) <= 1e-10, (record['ship'], column)


def verify_iges_reference(result, source_csv):
    """Re-derive errors and diagnostic flags from computed/published ordinates."""
    with Path(source_csv).open(newline='') as f:
        source = {float(r['heel_deg']): r for r in csv.DictReader(f)}
    assert len(result['rows']) == 7
    assert {r['heel_deg'] for r in result['rows']} == {20., 25., 30., 35., 40., 45., 50.}
    passes = {'kn': 0, 'gz': 0}
    errors = {'kn': [], 'gz': []}
    for row in result['rows']:
        ref = source[row['heel_deg']]
        for kind in ('kn', 'gz'):
            published = float(ref[f'kcs_{kind}_m'])
            computed = float(row['iges_upper'][f'{kind}_m'])
            tolerance = .05 if kind == 'kn' else max(.05, .05 * abs(published))
            error = computed - published
            assert abs(row[f'reference_{kind}_m'] - published) <= 1e-12
            assert abs(row[f'error_{kind}_m'] - error) <= 1e-12
            assert abs(row[f'l5_diagnostic_{kind}_tolerance_m'] - tolerance) <= 1e-12
            assert row[f'within_l5_diagnostic_{kind}'] == (abs(error) <= tolerance)
            passes[kind] += abs(error) <= tolerance
            errors[kind].append(abs(error))
        assert abs(row['iges_upper']['volume_residual_m3']) < 1e-4
    for kind in ('kn', 'gz'):
        assert result['summary'][f'{kind}_passed'] == passes[kind]
        assert abs(result['summary'][f'max_abs_error_{kind}_m'] - max(errors[kind])) <= 1e-12
    return passes
