from types import SimpleNamespace
import pytest
from .work import SolverWork, WorkExhausted


def test_cumulative_work_is_single_threaded_and_wall_only_outer_guard():
    work = SolverWork(2., wall_s=900.)
    solver = SimpleNamespace(parameters=SimpleNamespace(), deterministic_time=.7)
    work.configure(solver, cap=.5)
    assert solver.parameters.max_deterministic_time == .5
    assert solver.parameters.num_search_workers == 1
    assert solver.parameters.max_time_in_seconds > 890
    work.record(solver)
    work.configure(solver)
    assert solver.parameters.max_deterministic_time == pytest.approx(1.3)
    work.record(solver)
    assert work.metadata()['used'] == 1.4


def test_zero_work_solves_are_bounded_and_wall_guard_marks_incomplete():
    work = SolverWork(1., wall_s=900., max_solves=1)
    solver = SimpleNamespace(parameters=SimpleNamespace(), deterministic_time=0.)
    work.configure(solver)
    work.record(solver)
    with pytest.raises(WorkExhausted, match='solve_count_limit'):
        work.configure(solver)
    assert work.metadata()['solve_limit_reached']
    work.deadline = 0.
    assert not work.metadata()['work_complete']
    assert work.metadata()['outer_wall_hit']


def test_exact_cutoff_and_work_budget_are_forwarded(monkeypatch):
    from .exact import certified
    from .exact.test_plain import fixture
    from .exact.bound_joint import BoundResult
    from dataclasses import asdict
    captured = {}
    def fake(instance, **kwargs):
        captured.update(kwargs)
        result = BoundResult(3., 3., 0, 'threshold_infeasible', None, 0., 0.)
        return dict(result=asdict(result), iterations=1, cuts=[], static_legal=False)
    monkeypatch.setattr(certified, '_lazy_bound', fake)
    result = certified.lower_bound(fixture(), budget_s=900, upper_bound=4, work_limit=10)
    assert captured['cost_cap'] == 3
    assert captured['work_budget'].limit == 10
    assert result.status == 'cutoff_infeasible'
    assert result.proof_details['tested_integer_improvement_target'] == 4


def test_fixed_work_bound_and_constructor_have_serializable_diagnostics():
    import json
    from dataclasses import asdict
    from .exact.test_plain import fixture
    from .exact.certified import lower_bound
    from .constructor_target import construct
    instance = fixture()
    bound = lower_bound(instance, budget_s=900., work_limit=.2, upper_bound=4.)
    assert bound.lower_bound <= 4.
    assert bound.proof_work['solves'] > 0
    assert bound.proof_work['used'] >= 0
    assert not bound.proof_work['outer_wall_hit']
    assert bound.proof_details['actual_bound_producer'] == 'independent_plain_packing'
    assert 'certified_plain_packing' in bound.proof_scope
    json.dumps(asdict(bound))
    candidate = construct(instance, budget_s=900., work_limit=.2)
    assert candidate.proof_work['solves'] > 0
    assert candidate.independent_lower_bound <= 4.
    assert not candidate.full_checker_pass
    json.dumps({k: v for k, v in asdict(candidate).items() if k not in ('actions', 'first_model_actions')})
