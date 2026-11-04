"""Budget and fallback invariants of the copied Frontier constructor bank."""
from types import SimpleNamespace
from unittest.mock import patch

from . import portfolio


def test_val09_budget_scales_by_six_seconds_per_box():
    for n, expected in ((3, 20.), (4, 24.), (12, 72.), (18, 108.), (22, 120.)):
        assert portfolio.baseline_search_s(SimpleNamespace(load_list=range(n))) == expected


def test_search_uses_original_two_thirds_one_third_and_keeps_distinct_stows():
    a = SimpleNamespace(box='a', slot='0010102')
    b = SimpleNamespace(box='a', slot='0030102')
    first = SimpleNamespace(actions=(a,), objective=10.)
    second = SimpleNamespace(actions=(b,), objective=11.)
    bank = portfolio.StartBank(None, [('first', first), ('second', second)], None, None, None, set(), {})
    stats = SimpleNamespace(iterations=1, accepted=1, infeasible=0, best_trace=[], operator_gains={})
    with patch.object(portfolio, 'search', side_effect=[([first], stats), ([second], stats)]) as run:
        candidates, record = portfolio.baseline_search(None, bank, search_s=120.)
    assert [call.kwargs['time_limit_s'] for call in run.call_args_list] == [80., 40.]
    assert candidates == [first, second]
    assert [row['start'] for row in record['runs']] == ['first', 'second']


def test_collector_keeps_default_tanks_pass_as_reference_and_fallback():
    plan = SimpleNamespace(ballast=None)
    action = SimpleNamespace(box='a', slot='0010102')
    scored = SimpleNamespace(plan=plan, actions=(action,), objective=8.)
    problem = SimpleNamespace(score=lambda actions: scored)
    result = SimpleNamespace(plan=plan)
    space = SimpleNamespace(from_plan=lambda instance, raw: (action,))
    absent = SimpleNamespace(status='infeasible', plan=None)
    master = SimpleNamespace(status='UNKNOWN', objective=None, bound=None, cuts=0,
                             wall_s=0., assignment=None)
    with patch.object(portfolio, 'get_action_space', return_value=space), \
            patch.object(portfolio, 'TwoStepPlanner') as two, \
            patch.object(portfolio, 'blocked_plan', return_value=absent), \
            patch.object(portfolio, '_yard_first', return_value=([], 0)), \
            patch.object(portfolio, 'solve_master', return_value=master):
        two.return_value.plan.return_value = SimpleNamespace(status='ok', plan=plan)
        bank = portfolio.collect_starts(None, problem=problem,
            finish=lambda *args, **kwargs: (result, {'passed': True, 'weighted': 8.}))
    assert bank.reference == {}
    assert bank.fallback is result
    assert bank.starts == [('twostep', scored)]
    assert bank.unfinished == set()
