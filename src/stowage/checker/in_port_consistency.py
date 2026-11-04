"""Cross-check sampled strength against the same selected in-port state.

The caller supplies the stability report for the actual ballast and chosen HEEL
split, including any exact path re-evaluation. An ideal-split report or the other
pumping-window endpoint is not evidence about that state. No solve is repeated
here, and the frozen strength interface is unchanged.

Metrics ``strength_vs_stability_delta_{ta,tf,heel}`` are signed strength minus
stability, in metres for draught and radians for heel. Absolute differences above
1e-3 m or 1e-4 rad indicate inconsistent equilibrium evidence, not a physical
cargo-operation failure. Missing/nonfinite evidence also fails closed whenever
strength ran. An already unavailable strength result is preserved unchanged.
"""
from __future__ import annotations

import math

from stowage.model import PartReport, Violation


_AXES = (
    ('draught_aft_m', 'ta', 1e-3, 'm'),
    ('draught_fwd_m', 'tf', 1e-3, 'm'),
    ('list_rad', 'heel', 1e-4, 'rad'),
)


def check_strength_consistency(strength: PartReport, stability: PartReport | None,
                               *, call_index: int) -> PartReport:
    """Return a strength-report copy with agreement metrics or a hard error.

    Physical stability violations may still provide valid equilibrium values.
    Numerical errors, missing values and unexecuted stability checks cannot.
    The caller retains responsibility for the stability report's own verdict.
    """
    result = strength.model_copy(deep=True)
    if not strength.ran:
        return result

    problems = []
    if (stability is None or not stability.ran or stability.error is not None
            or any(v.rule_id.startswith('checker.') for v in stability.violations)):
        problems.append(('No valid stability equilibrium for the selected strength state.',
                         None, None, None))

    for key, axis, tolerance, unit in _AXES:
        left = strength.metrics.get(key)
        right = stability.metrics.get(key) if stability is not None else None
        if (left is None or right is None
                or not math.isfinite(left) or not math.isfinite(right)):
            problems.append((f'Missing or nonfinite {key} in strength/stability comparison.',
                             None, None, unit))
            continue
        delta = left-right
        if not math.isfinite(delta):
            problems.append((f'Nonfinite {key} strength/stability difference.',
                             None, None, unit))
            continue
        result.metrics[f'strength_vs_stability_delta_{axis}'] = delta
        if abs(delta) > tolerance:
            problems.append((f'Strength and stability {key} disagree for the selected state.',
                             abs(delta), tolerance, unit))

    if problems:
        message = ' '.join(item[0] for item in problems)
        result.ran = False
        result.error = message
        result.violations.extend(Violation(rule_id='checker.internal_error', severity='hard',
            call_index=call_index, message=description, measured=measured, limit=limit, unit=unit)
            for description, measured, limit, unit in problems)
    return result
