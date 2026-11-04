"""Fail-closed presentation of complete checker evidence, separate from shipping.

This does not bind reports or approve a plan. Callers still enforce complete input
binding and PlannerResult.shippable. A completed rejection is FAIL; diagnostic,
missing or unexecuted evidence is NOT VALIDATED even if its raw passed flag is true.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

REQUIRED_PARTS = frozenset({'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints'})


def _mapping(value: Any) -> Mapping:
    if isinstance(value, BaseModel):
        return {name: getattr(value, name) for name in type(value).model_fields}
    return value if isinstance(value, Mapping) else {}


def report_validation(report, *, instance=None, plan=None, planner_result=None) -> dict[str, Any]:
    """Describe evidence completeness without replacing the authoritative report."""
    reasons = []
    visited = set()

    def visit(value, path):
        if id(value) in visited:
            return
        visited.add(id(value))
        if isinstance(value, (BaseModel, Mapping)):
            fields = _mapping(value)
            stubs = fields.get('stubs')
            if stubs:
                reasons.append(f'{path}: stub-tainted evidence')
            # PartReport.metrics stores flags as floats; retain their explicit meaning.
            if (('validated' in fields and fields['validated'] == 0)
                    or ('not_validated' in fields and fields['not_validated'] == 1)):
                reasons.append(f'{path}: explicitly marked not validated')
            status = fields.get('validation_status')
            if isinstance(status, str) and status.upper().replace('_', ' ').strip() in {'NOT VALIDATED', 'UNVALIDATED'}:
                reasons.append(f'{path}: explicitly marked not validated')
            for name, child in fields.items():
                visit(child, f'{path}.{name}')
        elif isinstance(value, (list, tuple, set, frozenset)):
            for index, child in enumerate(value):
                visit(child, f'{path}[{index}]')

    for name, value in (('report', report), ('instance', instance), ('plan', plan), ('planner_result', planner_result)):
        visit(value, name)
    raw = _mapping(report)
    if not raw:
        reasons.append('No checker report')
    else:
        parts = _mapping(raw.get('parts'))
        for name in sorted(REQUIRED_PARTS - parts.keys()):
            reasons.append(f'{name}: required check is missing')
        for name, part in sorted(parts.items()):
            data = _mapping(part)
            if data.get('ran') is not True:
                reasons.append(f'{name}: check did not run')
            if data.get('error'):
                reasons.append(f'{name}: check could not complete ({data["error"]})')
        if raw.get('count') is None:
            reasons.append('Checker replay counts are unavailable')
    reasons = list(dict.fromkeys(reasons))
    validated = not reasons
    # A raw verdict must be boolean true, never a truthy imported string.
    passed = raw.get('passed') is True
    return {'validated': validated, 'status': 'NOT VALIDATED' if not validated else 'PASS' if passed else 'FAIL',
            'reasons': reasons}
