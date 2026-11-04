"""Measured D74 ballast programmes and reserved D75 resequencing diagnostics.

The final checker report supplies ballast evidence; finisher trial diagnostics
are not substitutes. Missing measurements remain unknown, and the ideal HEEL
path is reported separately from the chosen, rate-constrained path.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import math
from statistics import fmean, median


_PROGRAMME_FIELDS = (
    'changeover_k', 'lifts_after_k', 'pumped_mass_t', 'abs_change_t',
    'pump_time_required_h', 'pump_time_available_h', 'pump_window_lifts',
    'ballast_pump_m3_h', 't_lift_s', 'cranes', 'gross_moves_per_hour',
    'heel_step_limit_t', 'heel_max_step_t', 'heel_max_ideal_step_t',
    'heel_path_feasible', 'heel_path_linearised_miss', 'rate_check_rule',
)
_DERIVED_FIELDS = (
    'pump_time_margin_h', 'pump_time_required_available_ratio',
    'heel_step_margin_t', 'heel_chosen_limit_ratio', 'heel_ideal_limit_ratio',
)
_NUMERIC_FIELDS = tuple(name for name in _PROGRAMME_FIELDS
                        if name not in ('heel_path_feasible', 'heel_path_linearised_miss',
                                        'rate_check_rule')) + _DERIVED_FIELDS + ('ballast_delay_h',)


def restore_check_report(value):
    """Retain the checker's typed programme block when loading saved evidence."""
    from stowage.model import CheckReport
    if isinstance(value, Mapping) and any(isinstance(part, Mapping) and 'ballast_programme' in part
                                         for part in value.get('parts', {}).values()):
        from stowage.checker.in_port_report import InPortCheckReport
        return InPortCheckReport.model_validate(value)
    return CheckReport.model_validate(value)


def _mapping(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return value if isinstance(value, Mapping) else {}


def _number(value):
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) else None)


def _difference(available, required):
    a, b = _number(available), _number(required)
    return a-b if a is not None and b is not None else None


def _ratio(numerator, denominator):
    a, b = _number(numerator), _number(denominator)
    return a/b if a is not None and b is not None and b > 0 else None


def _in_port_part(record):
    parts = _mapping(_mapping(record.get('report')).get('parts'))
    # The active typed checker uses a separate in_port part. The nested shape
    # is the documented stability in-port block; never use a finisher trial.
    if 'in_port' in parts:
        return _mapping(parts['in_port'])
    return _mapping(_mapping(parts.get('stability')).get('in_port'))


def _resequencing(record):
    metrics = _mapping(_mapping(record.get('result')).get('metrics'))
    source = _mapping(record.get('resequencing')) or _mapping(metrics.get('resequencing'))
    moved = _number(source.get('lifts_moved'))
    return {
        'lifts_moved': int(moved) if moved is not None and moved >= 0 and moved.is_integer() else None,
        'list_margin_before': _number(source.get('list_margin_before')),
        'list_margin_after': _number(source.get('list_margin_after')),
        'applied': source.get('applied') if isinstance(source.get('applied'), bool) else None,
    }


def plan_diagnostics(record: Mapping) -> dict:
    """Copy measured final-plan fields, preserving unknown and partial verdicts."""
    part = _in_port_part(record)
    raw = part.get('ballast_programme')
    programme = deepcopy(dict(_mapping(raw))) if isinstance(raw, Mapping) or hasattr(raw, 'model_dump') else None
    numerical_error = (part.get('ran', True) is not True or bool(part.get('error'))
        or any(str(_mapping(violation).get('rule_id', '')).startswith('checker.')
               for violation in part.get('violations', [])))
    if record.get('report_binding_valid') is False:
        status = 'invalid_binding'
    elif numerical_error:
        status = 'unevaluated'
    elif programme is None:
        status = 'unknown'
    elif (any(name not in programme for name in _PROGRAMME_FIELDS)
          or programme.get('heel_path_feasible') is None):
        status = 'partial'
    else:
        status = 'evaluated'
    derived = dict.fromkeys(_DERIVED_FIELDS)
    delay = None
    if programme is not None and status in ('evaluated', 'partial'):
        # D74 amendment: delay is informational, never a failed rate check.
        # Older checker programmes omit it; absence is unknown, not zero.
        measured_delay = _number(programme.get('ballast_delay_h'))
        if measured_delay is not None and measured_delay >= 0:
            delay = measured_delay
        required, available = programme.get('pump_time_required_h'), programme.get('pump_time_available_h')
        chosen, ideal, limit = (programme.get(name) for name in (
            'heel_max_step_t', 'heel_max_ideal_step_t', 'heel_step_limit_t'))
        derived.update(pump_time_margin_h=_difference(available, required),
            pump_time_required_available_ratio=_ratio(required, available),
            heel_step_margin_t=_difference(limit, chosen),
            heel_chosen_limit_ratio=_ratio(chosen, limit),
            heel_ideal_limit_ratio=_ratio(ideal, limit))
    return {'ballast_programme': programme, 'ballast_programme_status': status,
            'ballast_delay_h': delay,
            'ballast_programme_error': part.get('error'),
            'ballast_programme_derived': derived, 'resequencing': _resequencing(record)}


def _distribution(values):
    values = [number for value in values if (number := _number(value)) is not None]
    return {'n': len(values), 'values': values, 'sum': sum(values) if values else None,
            'mean': fmean(values) if values else None, 'median': median(values) if values else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def _distributions(diagnostics):
    return {name: _distribution(item['ballast_delay_h'] if name == 'ballast_delay_h'
            else (item['ballast_programme'] or {}).get(name,
                item['ballast_programme_derived'].get(name)) for item in diagnostics)
            for name in _NUMERIC_FIELDS}


def _failure_share(verdicts):
    verdicts = [value for value in verdicts if isinstance(value, bool)]
    return {'n_evaluated': len(verdicts), 'n_failed': sum(verdicts),
            'share': sum(verdicts)/len(verdicts) if verdicts else None}


def summarise_plan_diagnostics(records, *, population='own'):
    """Report passing-population distributions beside all evaluated attempts.

    R2 and archived R1 checker verdicts use evaluated attempts; delay never
    invents a violation. Error/unknown evidence is excluded.
    """
    from eval.harness_metrics import _passed

    if population not in ('own', 'shipped'):
        raise ValueError("population must be 'own' or 'shipped'")
    records = list(records)
    diagnostics = [plan_diagnostics(record) for record in records]
    trusted = [item for item in diagnostics if item['ballast_programme_status'] in ('evaluated', 'partial')]
    passing = [item for record, item in zip(records, diagnostics)
               if _passed(record, population) and item['ballast_programme_status'] in ('evaluated', 'partial')]
    statuses = Counter(item['ballast_programme_status'] for item in diagnostics)
    r1, r2 = [], []
    for record, item in zip(records, diagnostics):
        if item['ballast_programme_status'] not in ('evaluated', 'partial'):
            continue
        programme = item['ballast_programme']
        required, available = (_number(programme.get(name)) for name in (
            'pump_time_required_h', 'pump_time_available_h'))
        if required is not None and available is not None and min(required, available) >= 0:
            # The checker compares exact fractions. Its structured violation
            # remains decisive when rounded report floats happen to tie.
            failure = any(_mapping(violation).get('rule_id') == 'port.ballast.transfer_rate'
                and 'changeover_time:' in str(_mapping(violation).get('message', ''))
                for violation in _in_port_part(record).get('violations', []))
            r1.append(failure)
        feasible = programme.get('heel_path_feasible')
        if isinstance(feasible, bool):
            r2.append(not feasible)
    n_plans = sum(bool(_mapping(record.get('report')))
        or _mapping(record.get('result')).get('plan') is not None for record in records)
    return {'population': population, 'n_records': len(records), 'n_plans': n_plans,
            'status_counts': dict(statuses),
            'n_unknown': statuses['unknown'], 'n_unevaluated': statuses['unevaluated'],
            'n_partial': statuses['partial'], 'n_invalid_binding': statuses['invalid_binding'],
            'distributions': _distributions(passing),
            'attempted_distributions': _distributions(trusted),
            'r1_failure': _failure_share(r1), 'r2_failure': _failure_share(r2),
            'resequencing': {name: _distribution(item['resequencing'][name] for item in diagnostics)
                for name in ('lifts_moved', 'list_margin_before', 'list_margin_after')}}
