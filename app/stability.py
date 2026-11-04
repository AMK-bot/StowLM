"""Present authoritative stability evidence with its checked liquid condition.

No regulatory criterion or physics model is replaced here. PartReport currently
contains scalar evidence only, so curve sampling reuses the authoritative
builders with the method, table selection, drafts and tank state of that check.
"""
from __future__ import annotations

import math
from typing import Any


def _criteria(part) -> list[dict[str, Any]]:
    """Recover applied lower limits from the checker's measured values/margins."""
    result = []
    labels = {'area_0_30_mrad': 'GZ area, 0–30°',
              'area_0_40_mrad': 'GZ area, 0–40° or downflooding',
              'area_30_40_mrad': 'GZ area, 30–40° or downflooding',
              'area_0_flood_mrad': 'Total positive-stability GZ area',
              'gz_at_or_above_30_m': 'GZ at or beyond 30°', 'gz_max_m': 'Maximum GZ',
              'angle_gz_max_rad': 'Angle of maximum GZ',
              'weather_ratio': 'Weather: resisting / overturning work',
              'weather_steady_heel_margin_rad': 'Weather: steady-heel clearance',
              'weather_table_applicability': 'Weather tables and curve coverage'}
    for key, margin in part.margins.items():
        if key.endswith('downflooding_30_margin_rad'):
            # IS Code A 2.2.1 (PDF p12) / B 2.3.2.1 (p25): "up to
            # ... 30° angle of heel". The checker uses min(flood, curve end),
            # not the flooding angle alone, for the available integration range.
            flood, end = part.metrics.get('angle_flooding_rad'), part.metrics.get('curve_max_heel_rad')
            if flood is None or end is None:
                continue
            result.append(dict(key=key, label='Available GZ coverage, minimum 30°',
                rule_id=('iscode.b2_3.containership_second_check' if key.startswith('containership_')
                         else 'iscode.a2_2.gz_area_0_30'),
                measured=min(flood,end), limit=math.radians(30), margin=margin, unit='rad',
                passed=margin >= -1e-12))
            continue
        if key.startswith('containership_'):
            metric = key.removeprefix('containership_')
            rule = 'iscode.b2_3.containership_second_check'
        elif key in {'area_0_30_mrad', 'area_0_40_mrad', 'area_30_40_mrad'}:
            metric = key
            rule = 'iscode.a2_2.gz_' + key.removesuffix('_mrad')
        elif key == 'gm_m':
            metric, rule = key, 'iscode.loading.gm'
        elif key.startswith('weather_'):
            metric, rule = key, 'iscode.a2_3.weather'
        elif key == 'gz_at_or_above_30_m':
            metric, rule = key, 'iscode.a2_2.gz_at_30'
        elif key == 'angle_gz_max_rad':
            metric, rule = key, 'iscode.a2_2.max_gz_angle'
        else:
            continue
        measured = part.metrics.get(metric)
        if measured is None and key == 'weather_steady_heel_margin_rad':
            # MSC.267(85), A 2.3.1.2 (data/raw/is-code/msc267-85.pdf,
            # PDF p13 / printed p12): "should not exceed 16° or 80%" of deck-edge
            # immersion. Recover the raw clearance, then the applied override
            # from its checked margin; the margin itself is not the measurement.
            edge, heel = part.metrics.get('weather_deck_edge_rad'), part.metrics.get('weather_phi0_rad')
            if edge is not None and heel is not None:
                measured = min(math.radians(16), .8 * edge) - heel
        if measured is None and key == 'weather_table_applicability':
            measured = margin + 1.0
        if measured is None:
            continue
        unit = 'm*rad' if 'mrad' in metric else 'rad' if metric.endswith('_rad') else 'm' if metric.endswith('_m') else '1'
        label = labels.get(key.removeprefix('containership_'), key.replace('_', ' '))
        if key.startswith('containership_'):
            label = 'B 2.3 · ' + label
        result.append(dict(key=key, label=label,
                           rule_id=rule, measured=measured, limit=measured-margin,
                           margin=margin, unit=unit, passed=(
                               not any(v.rule_id == rule and v.severity == 'hard' for v in part.violations)
                               if key == 'gm_m' else margin >= -1e-12)))
    return result


def _gz_curve(instance, ship, part):
    """Re-use the checker's recorded method and its exact loading settings."""
    from stowage.checker.stability import _context
    from stowage.checker.stability_loading import loading_condition, curve_correction
    from stowage.checker.stability_curve import build_stability_curve
    method = part.metrics.get('free_surface_method')
    if method not in (1,2):
        raise ValueError('Checked free-surface method is unavailable')
    method = int(method)
    geometry, params = _context(instance.vessel)
    # IS Code B 3.1.9.1/.2 (PDF p40): "actual moment of fluid transfer" /
    # "moment of inertia, calculated at 0°". B 3.1.12 (p40): small tanks
    # satisfying the specified inequality "need not be included".
    # These are the authoritative checker's settings, including for method .1.
    condition = loading_condition(instance, ship, geometry=geometry, ship_params=params,
        free_surface_method=method, precomputed_soundings=True, small_tank_exemption=True)
    if condition.errors:
        raise ValueError('; '.join(condition.errors))
    if method == 1 and part.metrics.get('liquid_table_used'):
        from stowage.checker.stability_liquid import attach_tables
        condition = attach_tables(condition, params,
            vessel_hash=getattr(geometry,'_stability_vessel_hash',None),
            _metadata_value=getattr(geometry,'_stability_liquid_metadata',None))
        if not condition.liquid_tables:
            raise ValueError('Liquid tables used by checker are no longer available')
    if part.metrics.get('geometry_table_used'):
        from stowage.checker.stability_tables import cached_geometry
        geometry = cached_geometry(geometry, params, instance.port.water_density_kg_m3)
        if geometry is None:
            raise ValueError('Geometry table used by checker is no longer available')
    curve = build_stability_curve(geometry, condition, instance.port.water_density_kg_m3,
        initial_drafts=(part.metrics['draught_aft_m'], part.metrics['draught_fwd_m']),
        correction_solver=lambda c,a,t:curve_correction(c,a,t,method=method),
        free_surface_method=method)
    return [dict(heel_rad=float(angle), gz_m=float(gz)) for angle, gz in zip(curve.angles, curve.gz)]


def stability_payload(instance, ship, part) -> dict[str, Any]:
    """Return the existing stability-panel JSON without issuing a verdict."""
    result = dict(ran=bool(part and part.ran), error=part.error if part else 'No stability report',
        metrics=part.metrics if part else {}, margins=part.margins if part else {},
        criteria=_criteria(part) if part else [], gz_curve=[], curve_error=None, criteria_set='Unavailable')
    if not part:
        return result
    result['criteria_set'] = ('IS Code B 2.3 + A 2.2.4 + A 2.3' if part.metrics.get('criteria_part_b_2_3_active')
        else 'IS Code A 2.2 + A 2.3' if part.metrics.get('criteria_part_a_2_2_1_to_3_active') else 'Unavailable')
    if 'gm_m' in part.metrics:
        gm = part.metrics['gm_m']
        # A 2.2.4 (PDF p13): "shall not be less than 0.15 m". Loading GM
        # has its separate measured margin, including the vessel-manual floor.
        minimum = float(instance.rule_overrides.get('iscode.a2_2.initial_gm', {}).get('minimum', .15))
        result['criteria'].append(dict(key='initial_gm', label='A 2.2.4 initial GM',
            rule_id='iscode.a2_2.initial_gm', measured=gm, limit=minimum,
            margin=gm-minimum, unit='m', passed=not any(
                v.rule_id == 'iscode.a2_2.initial_gm' and v.severity == 'hard' for v in part.violations)))
    for criterion in result['criteria']:
        if criterion['key'] == 'gm_m':
            criterion['label'] = 'Vessel loading minimum GM'
        if not part.ran or part.error:
            criterion['passed'] = None
    if part.ran and not part.error and 'draught_aft_m' in part.metrics:
        from stowage.checker.stability_budget import budget
        try:
            # Plot failure/unavailable numerical work must not hide the report.
            with budget(30.):
                result['gz_curve'] = _gz_curve(instance, ship, part)
        except Exception as error:
            result['curve_error'] = f'{type(error).__name__}: {error}'
    return result
