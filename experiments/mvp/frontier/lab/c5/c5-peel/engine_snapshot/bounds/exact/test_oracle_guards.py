"""An incomplete static oracle can never supply an assignment no-good."""
from types import SimpleNamespace

import pytest

from stowage.model import PartReport, Violation
from . import bound, bound_joint, lazy_bound, certified
from .test_plain import fixture


@pytest.mark.parametrize('error', [False, True])
def test_static_oracle_error_never_cuts_a_relaxation_assignment(monkeypatch, error):
    instance = fixture()
    assignment = dict(zip(instance.load_list, ['0010102', '0010104', '0010182']))
    initial = bound.BoundResult(0., 0., 0, 'feasible', None, 0., 0.)
    witness = bound_joint.BoundResult(0., 0., 0, 'feasible', assignment, 0., 0.)
    context = {'base': SimpleNamespace(weighted=0.), 'T': object()}
    monkeypatch.setattr(bound, 'build_model', lambda *a, **k: dict(context))
    monkeypatch.setattr(bound, 'solve_model', lambda *a, **k: initial)
    monkeypatch.setattr(bound_joint, 'build_model', lambda *a, **k: dict(context))
    monkeypatch.setattr(bound_joint, 'solve_model', lambda *a, **k: witness)
    report = PartReport(name='rules', ran=error, error='unknown' if error else None,
        violations=[Violation(rule_id='checker.internal_error', severity='hard',
                              call_index=0, message='no certificate')])
    monkeypatch.setattr(lazy_bound, 'check_static_rules', lambda *a, **k: report)
    def forbidden(*a, **k):
        raise AssertionError('unknown oracle must not add a cut')
    monkeypatch.setattr(lazy_bound, '_cut', forbidden)
    result = lazy_bound.lower_bound(instance, budget_s=1., eager_oog=False, max_iterations=1)
    assert result['cuts'] == []
    assert result['static_legal'] is False
    assert result['log'][-1]['status'] == 'static_oracle_unknown'


def test_active_frozen_override_is_outside_certified_domain(monkeypatch):
    from stowage import frozen
    monkeypatch.setattr(frozen, 'active_stubs', lambda: {'rule': 'stub'})
    with pytest.raises(ValueError, match='frozen-interface'):
        certified.lower_bound(fixture(), budget_s=1.)


def test_activated_rule_mode_uses_older_bound_fallback():
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=True):
        with pytest.raises(ValueError, match='rule-freeze'):
            certified.lower_bound(fixture(), budget_s=1.)


def test_exotic_geometry_does_not_get_unreviewed_absolute_error_certificate():
    instance = fixture()
    instance.vessel.cells[0].__dict__['lcg_m'] = 1.e10
    with pytest.raises(ValueError, match='ship-scale'):
        certified.lower_bound(instance, budget_s=1.)
