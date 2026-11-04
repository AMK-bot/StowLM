"""Exact lazy refinement: capacity bounds never masquerade as physical bands."""
import random

import pytest

from stowage.checker.in_port_lazy import lazy_heel_path
from stowage.checker.in_port_programme import ProgrammeParameters,heel_path


def parameters(delta=5.,initial=100.):
    # One-second lifts, density1000; heel rate converts the requested kg/lift.
    return ProgrammeParameters(1000.,delta*3.6,1,1,3600.,initial/(delta*3600.),0.,1000.)


def test_globally_ideal_feasible_path_needs_no_interval_endpoint_proofs():
    ideals=[50.,53.,55.,51.,54.]
    def refine(indices):
        pytest.fail(f'Unneeded endpoint proofs requested: {indices}')
    result=lazy_heel_path([(0.,100.)]*len(ideals),ideals,
        certified_ideals=[True]*len(ideals),refine=refine,
        arrival_split_kg=50.,parameters=parameters(),call_index=0)
    assert result.path.feasible and result.path.path_kg==tuple(ideals)
    assert result.refined_indices==()
    assert result.unrefined_indices==tuple(range(len(ideals)))


def test_refines_binding_states_then_returns_exact_eager_nearest_ideal_path():
    ideals=[50.,80.,20.,70.,50.]
    exact=[(40.,60.),(60.,85.),(15.,45.),(50.,90.),(40.,60.)]
    calls=[]
    def refine(indices):
        calls.append(indices)
        return {i:exact[i] for i in indices}
    kwargs=dict(arrival_split_kg=50.,parameters=parameters(delta=15.),call_index=0)
    lazy=lazy_heel_path([(0.,100.)]*len(ideals),ideals,
        certified_ideals=[True]*len(ideals),refine=refine,**kwargs)
    eager=heel_path(exact,ideals,**kwargs)
    assert calls
    assert lazy.path.feasible==eager.feasible
    assert lazy.path.path_kg==eager.path_kg


def test_unknown_ideal_certificate_must_be_refined_even_if_ideal_step_fits():
    calls=[]
    def refine(indices):
        calls.append(indices)
        return {i:(45.,55.) for i in indices}
    result=lazy_heel_path([(0.,100.)]*3,[50.]*3,
        certified_ideals=[True,False,True],refine=refine,
        arrival_split_kg=50.,parameters=parameters(),call_index=0)
    assert result.path.feasible
    assert calls==[(1,)]
    assert result.refined_indices==(1,)


@pytest.mark.parametrize('seed',range(40))
def test_random_tiny_bands_match_eager_complete_path_and_verdict(seed):
    rng=random.Random(seed)
    count=rng.randint(2,12)
    ideals=[float(rng.randint(0,100)) for _ in range(count)]
    exact=[(float(rng.randint(0,int(ideal))),float(rng.randint(int(ideal),100)))
           for ideal in ideals]
    initial_exact={i for i in range(count) if rng.random()<.25}
    outer=[exact[i] if i in initial_exact else (0.,100.) for i in range(count)]
    def refine(indices):
        return {i:exact[i] for i in indices}
    kwargs=dict(arrival_split_kg=50.,parameters=parameters(rng.randint(1,40)),call_index=0)
    lazy=lazy_heel_path(outer,ideals,certified_ideals=[True]*count,
        exact_indices=initial_exact,refine=refine,**kwargs)
    eager=heel_path(exact,ideals,**kwargs)
    assert lazy.path.feasible==eager.feasible
    assert lazy.path.path_kg==eager.path_kg
    if lazy.path.feasible:
        assert all(lazy.path.path_kg[i]==ideals[i] for i in lazy.unrefined_indices)


def test_refinement_errors_are_propagated_and_incomplete_proof_is_rejected():
    kwargs=dict(certified_ideals=[False],arrival_split_kg=50.,parameters=parameters(),call_index=0)
    def broken(indices):
        raise ValueError('nonfinite endpoint proof')
    with pytest.raises(ValueError,match='nonfinite'):
        lazy_heel_path([(0.,100.)],[50.],refine=broken,**kwargs)
    with pytest.raises(ValueError,match='every requested'):
        lazy_heel_path([(0.,100.)],[50.],refine=lambda indices:{},**kwargs)


def test_programme_ideal_reachable_skips_nonwindow_endpoint_proofs(monkeypatch):
    import runpy
    from stowage.rules.catalogue import rule_freeze_v1
    harness = runpy.run_path('tests/checker/test_full_in_port_programme.py')['_rate_harness']
    module, instance, plan, departure, _ = harness(monkeypatch)
    original = module.InPortEvaluator
    class NoUnneededProofs(original):
        def evaluate_intervals(self, states, reports):
            pytest.fail('globally reachable checked ideals require no endpoint proofs')
    monkeypatch.setattr(module, 'InPortEvaluator', NoUnneededProofs)
    telemetry = {}
    with rule_freeze_v1(enabled=True):
        report = module.replay(instance, plan, departure, telemetry=telemetry)
    assert report.ran and not report.violations
    assert telemetry['programme_interval_state_count'] == 0
    assert report.metrics['state:0.heel_path.heel_interval_is_outer_bound'] == 1.
    assert 'state:0.heel_path.heel_lo_kg' not in report.metrics


@pytest.mark.parametrize('changed_mass,arrival_failure,ideals,departure_ideals', [
    (0., None, [50.,70.,30.,70.,30.,50.], None),
    (1000., 4, [50.]*6, [70.,70.,70.,70.,50.,50.]),
    (1000., 4, [50.,70.,30.,70.,30.,50.], [70.]*6),
])
def test_programme_lazy_returns_eager_complete_path_and_verdict(
        monkeypatch, changed_mass, arrival_failure, ideals, departure_ideals):
    import runpy
    from stowage.checker import in_port_rates
    from stowage.rules.catalogue import rule_freeze_v1
    harness = runpy.run_path('tests/checker/test_full_in_port_programme.py')['_rate_harness']
    module, instance, plan, departure, _ = harness(monkeypatch,
        changed_mass=changed_mass, arrival_failure=arrival_failure,
        ideals=ideals, departure_ideals=departure_ideals)
    with rule_freeze_v1(enabled=True):
        lazy = module.replay(instance, plan, departure)
    original = in_port_rates.lazy_heel_path
    def eager(*args, **kwargs):
        kwargs['certified_ideals'] = [False]*len(kwargs['certified_ideals'])
        return original(*args, **kwargs)
    monkeypatch.setattr(in_port_rates, 'lazy_heel_path', eager)
    with rule_freeze_v1(enabled=True):
        full = module.replay(instance, plan, departure)
    assert lazy.ran == full.ran
    assert lazy.violations == full.violations
    assert lazy.ballast_programme == full.ballast_programme
    for i in range(6):
        key = f'state:{i}.heel_path.port_mass_kg'
        assert lazy.metrics[key] == full.metrics[key]


def test_requested_interval_numerical_failure_does_not_become_rate_failure(monkeypatch):
    import runpy
    from stowage.model import Violation
    from stowage.rules.catalogue import rule_freeze_v1
    harness = runpy.run_path('tests/checker/test_full_in_port_programme.py')['_rate_harness']
    module, instance, plan, departure, _ = harness(monkeypatch,
        ideals=[50.,70.,30.,70.,30.,50.])
    original = module.InPortEvaluator
    class Unavailable(original):
        def evaluate_intervals(self, states, reports):
            batch = super().evaluate_intervals(states, reports)
            for report in batch.reports:
                report.ran = False
                report.error = 'nonfinite requested proof'
                report.violations = [Violation(rule_id='checker.internal_error',
                    severity='hard', call_index=0, message=report.error)]
            return batch
    monkeypatch.setattr(module, 'InPortEvaluator', Unavailable)
    with rule_freeze_v1(enabled=True):
        report = module.replay(instance, plan, departure)
    assert not report.ran and report.error
    assert any(v.rule_id == 'checker.internal_error' for v in report.violations)
    assert not any(v.rule_id == 'port.ballast.transfer_rate' for v in report.violations)


def test_future_exact_constraint_refines_far_earlier_binding_states():
    ideals = [50.,50.,50.,50.,100.]
    exact = [(45.,65.),(45.,75.),(45.,85.),(45.,95.),(100.,100.)]
    requested = []
    def refine(indices):
        requested.extend(indices)
        return {i:exact[i] for i in indices}
    kwargs = dict(arrival_split_kg=50., parameters=parameters(delta=10.), call_index=0)
    result = lazy_heel_path([(0.,100.)]*4+[(100.,100.)], ideals,
        certified_ideals=[True]*5, exact_indices=(4,), refine=refine, **kwargs)
    eager = heel_path(exact, ideals, **kwargs)
    assert result.path.feasible and result.path.path_kg == eager.path_kg
    assert requested == [0,1,2,3]
    assert result.path.path_kg == (60.,70.,80.,90.,100.)
