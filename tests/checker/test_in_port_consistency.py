"""D74 sampled strength must use the selected state's floating equilibrium."""
import math

import pytest

from stowage.checker.in_port_consistency import check_strength_consistency
from stowage.model import PartReport, Violation


def report(name='strength', **changes):
    values = dict(name=name, metrics={
        'draught_aft_m': 4., 'draught_fwd_m': 3., 'list_rad': .01})
    values.update(changes)
    return PartReport(**values)


def test_identical_equilibrium_has_zero_deltas_and_does_not_mutate_inputs():
    strength, stability = report(), report('in_port')
    original = strength.model_dump_json()
    result = check_strength_consistency(strength, stability, call_index=0)
    assert result.ran and result.error is None and not result.violations
    for axis in ('ta', 'tf', 'heel'):
        assert result.metrics[f'strength_vs_stability_delta_{axis}'] == 0.
    assert strength.model_dump_json() == original
    assert result is not strength


@pytest.mark.parametrize('key,axis,delta', [
    ('draught_aft_m', 'ta', .0009),
    ('draught_fwd_m', 'tf', -.0009),
    ('list_rad', 'heel', -.00009),
])
def test_small_signed_differences_pass(key, axis, delta):
    strength, stability = report(), report('in_port')
    strength.metrics[key] += delta
    result = check_strength_consistency(strength, stability, call_index=1)
    assert result.ran and not result.violations
    assert result.metrics[f'strength_vs_stability_delta_{axis}'] == pytest.approx(delta)


@pytest.mark.parametrize('key,axis,delta', [
    ('draught_aft_m', 'ta', .00101),
    ('draught_fwd_m', 'tf', -.00101),
    ('list_rad', 'heel', .000101),
])
def test_disagreement_is_internal_error_with_numeric_evidence(key, axis, delta):
    strength, stability = report(), report('in_port')
    strength.metrics[key] += delta
    result = check_strength_consistency(strength, stability, call_index=2)
    assert not result.ran and result.error
    assert {v.rule_id for v in result.violations} == {'checker.internal_error'}
    assert result.violations[-1].call_index == 2
    assert result.violations[-1].severity == 'hard'
    assert result.metrics[f'strength_vs_stability_delta_{axis}'] == pytest.approx(delta)


@pytest.mark.parametrize('side', ['strength', 'stability'])
@pytest.mark.parametrize('key', ['draught_aft_m', 'draught_fwd_m', 'list_rad'])
@pytest.mark.parametrize('invalid', [None, math.nan, math.inf, -math.inf])
def test_missing_or_nonfinite_equilibrium_fails_closed(side, key, invalid):
    strength, stability = report(), report('in_port')
    source = strength if side == 'strength' else stability
    if invalid is None:
        del source.metrics[key]
    else:
        source.metrics[key] = invalid
    result = check_strength_consistency(strength, stability, call_index=0)
    assert not result.ran and result.error
    assert result.violations[-1].rule_id == 'checker.internal_error'


@pytest.mark.parametrize('invalid', [None,
    report('in_port', ran=False, error='numerical solve failed'),
    report('in_port', error='untrusted equilibrium')])
def test_missing_or_failed_stability_is_not_comparison_evidence(invalid):
    result = check_strength_consistency(report(), invalid, call_index=0)
    assert not result.ran
    assert result.violations[-1].rule_id == 'checker.internal_error'


def test_physical_stability_violation_still_has_comparable_equilibrium():
    physical = Violation(rule_id='port.stability.cargo_operations', severity='hard',
        call_index=0, message='HEEL pair exhausted.')
    result = check_strength_consistency(report(), report('in_port', violations=[physical]),
                                        call_index=0)
    assert result.ran and not result.violations


def test_existing_strength_failure_is_preserved_without_fabricating_deltas():
    strength = report(ran=False, error='strength solve failed', metrics={})
    result = check_strength_consistency(strength, None, call_index=0)
    assert result.model_dump_json() == strength.model_dump_json()


def test_disagreement_preserves_strength_physical_diagnostics():
    physical = Violation(rule_id='strength.bending_moment', severity='hard',
        call_index=0, message='Bending moment exceeds its limit.')
    strength = report(violations=[physical], margins={'bm_ratio': -.1})
    strength.metrics['draught_fwd_m'] += .01
    result = check_strength_consistency(strength, report('in_port'), call_index=0)
    assert not result.ran
    assert result.violations[0] == physical
    assert result.margins['bm_ratio'] == -.1


def test_exact_limits_are_inclusive():
    strength = report(metrics={'draught_aft_m': .001,
        'draught_fwd_m': -.001, 'list_rad': .0001})
    stability = report('in_port', metrics={'draught_aft_m': 0.,
        'draught_fwd_m': 0., 'list_rad': 0.})
    result = check_strength_consistency(strength, stability, call_index=0)
    assert result.ran and not result.violations


def test_finite_equilibria_with_overflowed_difference_fail_closed():
    strength, stability = report(), report('in_port')
    strength.metrics['draught_aft_m'] = 1e308
    stability.metrics['draught_aft_m'] = -1e308
    result = check_strength_consistency(strength, stability, call_index=0)
    assert not result.ran and result.error
    assert result.violations[-1].rule_id == 'checker.internal_error'
    assert 'strength_vs_stability_delta_ta' not in result.metrics


def test_stability_internal_violation_cannot_supply_trusted_evidence():
    internal = Violation(rule_id='checker.internal_error', severity='hard',
        call_index=0, message='Equilibrium solver unavailable.')
    result = check_strength_consistency(report(), report('in_port', violations=[internal]),
                                        call_index=0)
    assert not result.ran
    assert result.violations[-1].rule_id == 'checker.internal_error'
