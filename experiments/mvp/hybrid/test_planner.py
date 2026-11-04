"""Decision parity with frozen frontier, plus exact candidate provenance."""
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.hybrid import planner as hybrid
from experiments.mvp.frontier import planner as frontier


@dataclass(frozen=True)
class Action:
    box: str
    slot: str


def scored(name, cost, actions=None):
    actions = tuple(actions or (Action(name, name),))
    plan = NS(name=name, actions=actions, ballast=NS(tank_masses_kg={'tank': 123}))
    return NS(actions=actions, objective=cost, plan=plan)


def run(monkeypatch, module, *, two=None, blocked=None, yard=(), master=None,
        outputs=None, failed=(), refused=(), extra=(), load_boxes=None, finished=None, visited=None):
    """Replace costly solvers with deterministic decisions and record their calls."""
    trace = []
    candidates = [x for x in (two, blocked, *yard, master, *[s for _, s in extra]) if x is not None]
    registry = {s.actions: s for s in candidates}
    for values in (outputs or {}).values():
        registry.update((s.actions, s) for s in values)
    for values in (visited or {}).values():
        registry.update((s.actions, s) for s in values)
    registry.update((s.actions, s) for s in (finished or {}).values())
    instance = NS(load_list=load_boxes if load_boxes is not None else
                  sorted({action.box for actions in registry for action in actions}))

    class Problem:
        def __init__(self, instance):
            pass

        def score(self, actions):
            actions = tuple(actions)
            trace.append(('score', actions))
            return registry.get(actions)

    class TwoStep:
        def __init__(self, **kwargs):
            pass

        def plan(self, instance, **kwargs):
            trace.append(('twostep', kwargs))
            return NS(status='ok', plan=two.plan if two else None)

    def finish(instance, plan, **kwargs):
        trace.append(('finish', plan.name, kwargs))
        passed = plan.name not in failed
        row = dict(label=kwargs['label'], passed=passed, weighted=registry[plan.actions].objective,
                   finalize_status='ok' if passed else 'timeout')
        final_plan = (finished or {}).get(plan.name)
        return (NS(plan=final_plan.plan if final_plan else plan) if passed else None), row

    def block(instance, **kwargs):
        trace.append(('blocked', kwargs))
        return NS(status='ok', plan=blocked.plan if blocked else None)

    def construct(problem, **kwargs):
        trace.append(('yard_first', kwargs))
        return list(yard), 40

    def solve(problem, **kwargs):
        trace.append(('master', kwargs))
        return NS(status='ok', objective=1, bound=0, cuts=0, wall_s=0,
                  assignment=master.actions if master else None)

    def legal(problem, assignment):
        trace.append(('order_legal', assignment))
        return assignment, None

    def sight(instance, plan, reference):
        trace.append(('sight', plan.name, reference))
        return plan.name not in refused, None

    def search(instance, actions, **kwargs):
        trace.append(('search', tuple(actions), {k: v for k, v in kwargs.items() if k not in ('problem', 'gate')}))
        best = (outputs or {}).get(tuple(actions), [registry[tuple(actions)]])
        for candidate in (visited or {}).get(tuple(actions), []):
            kwargs['problem'].score(candidate.actions)
        stats = NS(iterations=1, accepted=1, infeasible=0, best_trace=[], operator_gains={})
        return best, stats

    with monkeypatch.context() as m:
        for name, value in dict(Problem=Problem, TwoStepPlanner=TwoStep, _finish=finish,
                                blocked_plan=block, compacted=lambda instance, plan: plan,
                                _yard_first=construct, solve_master=solve, order_legal=legal,
                                sight_clear=sight, search=search, perf_counter=lambda: 0,
                                get_action_space=lambda name: NS(from_plan=lambda instance, plan: plan.actions)).items():
            m.setattr(module, name, value)
        kwargs = dict(search_s=60, finalize_s=300, seed=0)
        if module is hybrid:
            kwargs['extra_starts'] = extra
            from experiments.mvp.hybrid.contribution import decision_contribution
            m.setattr(module, 'for_plan', lambda instance, plan, origin, seed:
                      decision_contribution(origin, instance.load_list, seed,
                                            {a.box: a.slot for a in plan.actions}))
        result, record = module.plan_and_check(instance, **kwargs)
    return result.plan.name if result else None, record, trace


@pytest.mark.parametrize('case', ['ranked', 'ties', 'single', 'empty', 'failed_start'])
def test_no_extra_starts_matches_frontier_decisions(monkeypatch, case):
    """All starts, tie-breaks, budgets, master workers, and finishes match 0.9.0."""
    two, blocked = scored('two', 30), scored('blocked', 20)
    yard = [scored('yard-a', 15), scored('yard-b', 16), scored('yard-c', 17)]
    master = scored('master', 10)
    first, second = scored('first-result', 9), scored('second-result', 8)
    kwargs = dict(two=two, blocked=blocked, yard=yard, master=master,
                  outputs={master.actions: [first], yard[0].actions: [second]},
                  visited={master.actions: [first], yard[0].actions: [second]}, failed=('second-result',))
    if case == 'ties':
        kwargs = dict(two=scored('two', 10), blocked=scored('blocked', 10),
                      yard=[scored('yard', 10)], master=scored('master', 10))
    elif case == 'single':
        kwargs = dict(yard=[scored('only', 10)], failed=('only',))
    elif case == 'empty':
        kwargs = {}
    elif case == 'failed_start':
        kwargs = dict(two=two, blocked=blocked, yard=yard, master=master,
                      outputs={master.actions: [two], yard[0].actions: [blocked]}, failed=('two',))
    old_result, old_record, old_trace = run(monkeypatch, frontier, **kwargs)
    new_result, new_record, new_trace = run(monkeypatch, hybrid, **kwargs)
    assert new_result == old_result
    assert new_trace == old_trace
    assert [r['label'] for r in new_record['tries']] == [r['label'] for r in old_record['tries']]
    assert new_record.get('masters') == old_record.get('masters')
    shares = [event[2]['time_limit_s'] for event in new_trace if event[0] == 'search']
    assert shares == ([] if case == 'empty' else [60] if case == 'single' else [40, 20])
    if case == 'ranked':
        assert [e[1] for e in new_trace if e[0] == 'finish'] == ['two', 'blocked', 'second-result', 'first-result']
        assert new_record['final_origin'] == 'master'
        assert new_record['final_search_changed'] is True
    if case == 'ties':
        assert new_record['final_origin'] == 'twostep'
        assert new_record['final_search_changed'] is False


def test_extras_compete_on_replayed_objective_and_sight_without_extra_budget(monkeypatch):
    two, blocked = scored('two', 30), scored('blocked', 20)
    bad, winner, other = scored('refused', 1), scored('llm', 5), scored('random', 6)
    result, record, trace = run(monkeypatch, hybrid, two=two, blocked=blocked,
                                extra=[('llm:bad', bad), ('llm:sample-1', winner), ('random:sample-2', other)],
                                refused=('refused',))
    assert result == 'llm'
    assert record['final_origin'] == 'llm:sample-1'
    assert record['final_search_changed'] is False
    assert [r['origin'] for r in record['starts']] == ['llm:sample-1', 'random:sample-2', 'blocked', 'twostep']
    assert record['extra_starts'][0]['sight_clear'] is False
    assert record['extra_starts'][0]['seed_decisions'] == {'refused': 'refused'}
    assert record['final_seed_decisions'] == {'llm': 'llm'}
    assert record['final_search_origin'] == 'llm:sample-1'
    assert [e[2]['time_limit_s'] for e in trace if e[0] == 'search'] == [40, 20]
    assert [e[1] for e in trace if e[0] == 'finish'] == ['two', 'blocked', 'llm']


def test_pool_collision_keeps_actual_winner_lineage_and_action_order(monkeypatch):
    """Equal stows may have different costs and order; provenance follows overwrite."""
    actions = (Action('a', '1'), Action('b', '2'))
    start = scored('master', 10, actions)
    other = scored('yard', 11)
    overwritten = scored('first-run', 8, actions)
    retained = scored('second-run', 7, tuple(reversed(actions)))
    result, record, _ = run(monkeypatch, hybrid, yard=[other], master=start,
                            outputs={start.actions: [overwritten], other.actions: [retained]})
    assert result == 'second-run'
    assert record['final_origin'] == 'yard_first'
    assert record['final_search_changed'] is True
    assert record['tries'][0]['origin'] == 'yard_first'
    assert record['final_seed_decisions'] == {'yard': 'yard'}
    assert record['runs'][0]['candidates'][0]['seed_decisions'] == {'a': '1', 'b': '2'}
    assert record['runs'][0]['candidates'][0]['search_changed'] is False
    assert record['runs'][1]['candidates'][0]['search_changed'] is True
    assert record['final_shared_stow'] is True
    assert [(r['run'], r['origin']) for r in record['final_reaching_runs']] == [(0, 'master'), (1, 'yard_first')]


def test_changed_actions_at_equal_cost_count_as_search_change(monkeypatch):
    start, changed = scored('master', 10), scored('equal-cost-change', 10)
    result, record, _ = run(monkeypatch, hybrid, master=start, outputs={start.actions: [changed]})
    assert result == 'equal-cost-change'
    assert record['final_search_changed'] is True


def test_equal_cost_extra_does_not_displace_baseline_starts(monkeypatch):
    two, blocked, extra = scored('two', 10), scored('blocked', 10), scored('llm', 10)
    result, record, trace = run(monkeypatch, hybrid, two=two, blocked=blocked, extra=[('llm:tie', extra)])
    assert result == 'two'
    assert record['final_origin'] == 'twostep'
    assert [r['origin'] for r in record['starts']] == ['twostep', 'blocked', 'llm:tie']
    assert [e[1] for e in trace if e[0] == 'search'] == [two.actions, blocked.actions]


def test_zero_retention_keeps_search_origin_but_records_no_retained_contribution(monkeypatch):
    """Review P1 reproduction: swapped seed choices must never establish H1."""
    seed = scored('seed', 10, (Action('a', '1'), Action('b', '2')))
    final = scored('rebuilt', 5, (Action('a', '2'), Action('b', '1')))
    result, record, _ = run(monkeypatch, hybrid, extra=[('llm:seed', seed)],
                            outputs={seed.actions: [final]}, load_boxes=['a', 'b'])
    assert result == 'rebuilt'
    assert record['final_search_origin'] == 'llm:seed'
    assert record['final_search_changed'] is True
    evidence = record['final_contribution']
    assert evidence['seed_decisions'] == {'a': '1', 'b': '2'}
    assert evidence['final_decisions'] == {'a': '2', 'b': '1'}
    assert evidence['retained_pairs'] == []
    assert evidence['retained_count'] == evidence['retained_fraction'] == 0
    assert evidence['total_load_boxes'] == 2


def test_shared_stow_includes_run_that_scored_but_did_not_keep_it(monkeypatch):
    baseline = scored('master', 10, (Action('a', '1'), Action('b', '2')))
    seed = scored('llm-seed', 11, (Action('a', '2'), Action('b', '1')))
    final = scored('final', 8, (Action('a', '3'), Action('b', '4')))
    rejected = scored('cheaper-failed-finish', 7, (Action('a', '4'), Action('b', '3')))
    result, record, _ = run(monkeypatch, hybrid, master=baseline, extra=[('llm:seed', seed)],
                            outputs={baseline.actions: [rejected], seed.actions: [final]},
                            visited={baseline.actions: [final]}, failed=('cheaper-failed-finish',),
                            load_boxes=['a', 'b'])
    assert result == 'final'
    assert record['final_search_origin'] == 'llm:seed'
    assert record['final_shared_stow'] is True
    assert [(w['run'], w['origin']) for w in record['final_reaching_runs']] == [(0, 'master'), (1, 'llm:seed')]
    assert [c['objective'] for c in record['runs'][0]['candidates']] == [7]


def test_partial_retention_uses_whole_load_list_denominator(monkeypatch):
    seed = scored('seed', 10, tuple(Action(box, str(i)) for i, box in enumerate('abcd', 1)))
    final = scored('partial', 5, (Action('a', '1'), Action('b', '2'), Action('c', '4'), Action('d', '3')))
    _, record, _ = run(monkeypatch, hybrid, extra=[('llm:seed', seed)],
                        outputs={seed.actions: [final]}, load_boxes=list('abcd'))
    evidence = record['final_contribution']
    assert record['final_load_list'] == list('abcd')
    assert evidence['retained_pairs'] == [dict(box='a', slot='1'), dict(box='b', slot='2')]
    assert evidence['retained_count'] == 2
    assert evidence['total_load_boxes'] == 4
    assert evidence['retained_fraction'] == .5


def test_retention_comes_from_checked_finish_not_search_candidate(monkeypatch):
    seed = scored('seed', 10, (Action('a', '1'), Action('b', '2')))
    checked = scored('checked', 5, (Action('a', '2'), Action('b', '1')))
    _, record, _ = run(monkeypatch, hybrid, extra=[('llm:seed', seed)],
                        finished={'seed': checked}, load_boxes=['a', 'b'])
    assert record['final_search_changed'] is False
    assert record['final_contribution']['final_decisions'] == {'a': '2', 'b': '1'}
    assert record['final_contribution']['retained_fraction'] == 0


def test_contribution_excludes_onboard_boxes_and_does_not_shrink_denominator():
    from experiments.mvp.hybrid.contribution import decision_contribution
    evidence = decision_contribution('llm:seed', list('abcd'), {'a': '1', 'onboard': 'x'},
                                     {'a': '1', 'onboard': 'x'})
    assert evidence['seed_decisions'] == evidence['final_decisions'] == {'a': '1'}
    assert evidence['retained_count'] == 1
    assert evidence['total_load_boxes'] == 4
    assert evidence['retained_fraction'] == .25
    assert decision_contribution('llm:empty', [], {}, {})['retained_fraction'] == 0


@pytest.mark.parametrize('encoded_actions', [True, False])
def test_real_stored_moveplan_is_decoded_and_replayed(monkeypatch, encoded_actions):
    """A real MovePlan's .actions are dictionaries, never solver Place objects."""
    from stowage.io import load_instance
    from stowage.model import MovePlan

    iid = 'sim.small.41100001'
    repo = Path(__file__).resolve().parents[3]
    row = json.loads((repo / 'experiments/mvp/frontier/runs/val-0.9' / f'{iid}.json').read_text())
    assert '/corpus/val/' in row['input']
    instance = load_instance(repo / row['input'])
    sample = json.loads((repo / 'experiments/mvp/strategy/k1b/candidates' / iid / 'sample-00.json').read_text())
    plan = MovePlan.model_validate(sample['plan'])
    assert isinstance(plan.actions[0], dict)
    if not encoded_actions:
        plan = plan.model_copy(update={'actions': None})

    monkeypatch.setattr(hybrid, 'TwoStepPlanner', lambda **kw: NS(plan=lambda *a, **k: NS(plan=None, status='none')))
    monkeypatch.setattr(hybrid, 'blocked_plan', lambda *a, **k: NS(plan=None, status='none'))
    monkeypatch.setattr(hybrid, '_yard_first', lambda *a, **k: ([], 0))
    monkeypatch.setattr(hybrid, 'solve_master', lambda *a, **k: NS(assignment=None, status='none', objective=None,
                                                               bound=None, cuts=0, wall_s=0))
    monkeypatch.setattr(hybrid, 'sight_clear', lambda *a, **k: (True, None))

    def search(instance, actions, **kwargs):
        assert all(hasattr(a, 'box') and hasattr(a, 'slot') for a in actions)
        candidate = kwargs['problem'].score(actions)
        return [candidate], NS(iterations=0, accepted=0, infeasible=0, best_trace=[], operator_gains={})

    monkeypatch.setattr(hybrid, 'search', search)
    monkeypatch.setattr(hybrid, '_finish', lambda instance, plan, **kw: (NS(plan=plan), dict(label=kw['label'], passed=True)))
    result, record = hybrid.plan_and_check(instance, search_s=0, extra_starts=[('llm:sample-00', plan)])
    assert result is not None
    assert record['extra_starts'][0]['replayed'] is True
    assert record['final_origin'] == 'llm:sample-00'
    assert record['final_search_changed'] is False
    from experiments.mvp.hybrid.contribution import plan_decisions
    decisions = plan_decisions(instance, result.plan)
    assert {box: decisions[box] for box in instance.load_list} == record['final_seed_decisions']
