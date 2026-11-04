"""Routing identity and incremental retention without native optimization."""
from types import SimpleNamespace as NS

import pytest

from . import method
from .routing import assignment_active


@pytest.mark.parametrize('size,loads,active', [
    ('tiny', 100, False), ('small', 100, False),
    ('medium', 10, True), ('large', 10, True), ('full', 10, True),
    (None, 60, False), (None, 61, True), ('unknown', 61, True),
])
def test_metadata_routing_precedes_load_count_fallback(size, loads, active):
    assert assignment_active(NS(size_class=size, load_list=range(loads))) is active


@pytest.mark.parametrize('size', ['tiny', 'small'])
def test_small_tiny_never_import_construct_or_solve_assignment(monkeypatch, size):
    def forbidden():
        raise AssertionError('numerical assignment runtime must remain inactive')
    monkeypatch.setattr(method, '_runtime', forbidden)
    problem = object()
    instance = NS(size_class=size, load_list=range(100))
    context = dict(problem=problem)
    assert list(method.iter_candidates(instance, context=context)) == []
    assert method.propose(instance, seed=0, budget_s=240., context=context) == []
    assert context['problem'] is problem
    assert context['assignment_metadata']['active'] is False
    assert method.finish(instance, None, seed=0, finalize_s=1., label='inactive', context=context)[0] is None


def _fake(monkeypatch, *, passes=True, diagnostic_error=False):
    instance = NS(size_class='medium', load_list=['a', 'b'], onboard=NS(placements=[]),
        vessel=NS(hull=NS(beam_m=10., depth_m=10., lpp_m=100., max_draught_m=8.)),
        port=NS(max_draught_m=None))
    plan = NS(id='engine-cold')
    actions = (NS(box='a', slot='1'), NS(box='b', slot='2'))
    scored = NS(actions=actions, plan=plan, objective=7.)
    problem = NS(instance=instance, boxes={b: NS(gross_mass_kg=10000.) for b in instance.load_list},
        mixes_hold_stack=lambda assignment: False, score=lambda ordered: scored)
    calls = []
    class Model:
        boxes = [0, 1]
        def __init__(self, *args, **kwargs):
            calls.append('model')
        def clear_oog(self, assignment):
            return assignment
    class Solver:
        def __init__(self, *args, **kwargs):
            calls.append('assignment')
        def dive(self, **kwargs):
            return {0: 0, 1: 1}
        def overages(self, placed):
            return {0: .5}
        def fill_hold(self, placed, instance):
            return placed, 0
    def ratio(*args):
        calls.append('diagnostics')
        if diagnostic_error:
            raise RuntimeError('optional geometry failed')
        return 1., .5, 0
    def check(*args):
        calls.append('check')
        return NS(passed=passes, count=NS(weighted_nonproductive=7.), violations=[])
    def shape(instance, value, floor):
        gm, securing, over = ratio()
        return securing, 1., gm, over, value
    runtime = NS(Scored=lambda actions, objective, value: NS(actions=actions, objective=objective, plan=value),
        shape=shape, install=lambda: calls.append('install'), Model=Model, Assignment=Solver,
        Settings=lambda **kwargs: NS(**kwargs), reference=lambda *args: None,
        block_layout=lambda model: {}, exact_ratio=ratio, check=check,
        to_assignment=lambda *args: ({'a': '1', 'b': '2'}, []),
        repair_oog=lambda problem, model, assignment: assignment,
        Sequencer=lambda problem: NS(order=lambda assignment: actions),
        apply_plan=lambda *args: NS(legal=True, final_state=NS(ship=NS(
            placements=[NS(container_id=b) for b in instance.load_list]))))
    monkeypatch.setattr(method, '_runtime', lambda: runtime)
    monkeypatch.setattr(method, '_finish_candidate', lambda *args, **kwargs: (None, dict(passed=False)))
    return instance, problem, scored, calls


def test_complete_candidate_is_published_and_yielded_before_optional_diagnostics(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch, diagnostic_error=True)
    published = []
    context = dict(problem=problem, publish_candidate=lambda value, **kwargs: published.append((value, kwargs)))
    stream = method.iter_candidates(instance, context=context)
    assert next(stream) is scored
    assert context['assignment_candidates'] == [scored]
    assert published[0][1]['checked'] is False
    assert 'diagnostics' not in calls and 'check' not in calls
    assert list(stream) == []
    assert context['checked_passes'] == [scored]
    assert published[1][1]['checked'] is True
    assert 'optional geometry failed' in context['assignment_metadata']['trials'][0]['securing_diagnostic_error']


def test_checked_pass_survives_callback_and_early_stop_propagates(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch)
    stopped = RuntimeError('consumer control exception')
    def broken_callback(*args, checked=False, **kwargs):
        if checked:
            raise stopped
    context = dict(problem=problem, publish_candidate=broken_callback)
    stream = method.iter_candidates(instance, context=context)
    assert next(stream) is scored
    with pytest.raises(RuntimeError) as raised:
        next(stream)
    assert raised.value is stopped
    assert context['_engine_assignment_callback_error'] is stopped
    assert context['assignment_candidates'] == context['checked_passes'] == [scored]
    assert context['_candidate_plans'][method.assignment_key(scored.actions)] is scored.plan
    assert context['assignment_reports'][method.assignment_key(scored.actions)].passed
    assert len(context['assignment_metadata']['publication_errors']) == 1


def test_candidate_callback_early_stop_propagates_before_optional_diagnostics(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch)
    stopped = RuntimeError('root found a proved optimum')
    def stop(*args, **kwargs):
        raise stopped
    context = dict(problem=problem, publish_candidate=stop)
    with pytest.raises(RuntimeError) as raised:
        list(method.iter_candidates(instance, context=context))
    assert raised.value is stopped
    assert context['assignment_candidates'] == [scored]
    assert 'diagnostics' not in calls and 'check' not in calls


def test_checked_pass_survives_later_telemetry_failure(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch)
    runtime = method._runtime()
    class BrokenTelemetry:
        passed = True
        count = NS(weighted_nonproductive=7.)
        @property
        def violations(self):
            raise RuntimeError('optional telemetry failed')
    runtime.check = lambda *args: BrokenTelemetry()
    context = dict(problem=problem)
    assert list(method.iter_candidates(instance, context=context)) == [scored]
    assert context['checked_passes'] == [scored]
    assert context['assignment_reports'][method.assignment_key(scored.actions)].passed
    assert 'optional telemetry failed' in context['assignment_metadata']['trials'][0]['error']


def test_alternate_orders_for_same_stow_remain_distinct():
    one = (NS(box='a', slot='1'), NS(box='b', slot='2'))
    assert method.assignment_key(one) != method.assignment_key(tuple(reversed(one)))


def test_all_complete_failed_candidates_are_returned_without_top_k_cap(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch, passes=False)
    candidates = []
    def score(ordered):
        index = len(candidates)
        value = NS(actions=(NS(box='a', slot=str(index)), NS(box='b', slot='last')),
                   plan=NS(id=f'cold-{index}'), objective=7.+index)
        candidates.append(value)
        return value
    problem.score = score
    context = dict(problem=problem)
    returned = method.propose(instance, seed=0, budget_s=240., context=context)
    assert len(returned) == method.ROUNDS == 6
    assert returned == [value.actions for value in candidates]
    assert context['assignment_candidates'] == candidates
    assert context['assignment_metadata']['full_checker_calls'] == 6
    assert context['assignment_metadata']['full_checker_passes'] == 0


def test_raw_partial_assignment_is_retained_but_never_claimed_complete_or_checked():
    instance = NS(load_list=['a', 'b'])
    model = NS(T=NS(box_ids=['a', 'b'], slot_ids=['one', 'two']))
    solver = NS(partial_fixed={0: 0})
    context, metadata = {}, {}
    method._retain_partial(instance, model, solver, 0, context, metadata)
    method._retain_partial(instance, model, solver, 1, context, metadata)
    retained = context['assignment_partial_assignments']
    assert retained is metadata['partial_assignments'] and len(retained) == 1
    assert retained[0]['assignment'] == {'a': 'one'}
    assert retained[0]['complete'] is False
    assert retained[0]['replay_legal'] is None
    assert retained[0]['full_checker_passed'] is None
    assert 'assignment_candidates' not in context and 'checked_passes' not in context


def test_optional_partial_telemetry_failure_cannot_erase_complete_dive(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch)
    def broken_partial(*args):
        raise RuntimeError('optional partial conversion failed')
    monkeypatch.setattr(method, '_retain_partial', broken_partial)
    context = dict(problem=problem)
    assert list(method.iter_candidates(instance, context=context)) == [scored]
    assert context['checked_passes'] == [scored]
    assert 'optional partial conversion failed' in context['assignment_metadata']['trials'][0]['partial_retention_error']


def test_assignment_honors_process_e9_ablation(monkeypatch):
    instance,problem,scored,calls=_fake(monkeypatch)
    context=dict(problem=problem,engine_config=NS(assignment_lp=True,e9_fix=False))
    assert list(method.iter_candidates(instance,context=context))==[scored]
    assert 'install' not in calls


def test_c6_assignment_limits_ignore_global_finisher_and_try_values():
    assert method.limits({}) == (300., 180., 2)
    context = dict(finalize_s=600., tries=3,
                   engine_config=NS(assignment_budget_s=123., assignment_finalize_s=45., assignment_tries=1))
    assert method.limits(context) == (123., 45., 1)
    assert method.limits(context, 99.) == (99., 45., 1)
    assert method.first_guess_gm(NS(beam_m=20.)) == pytest.approx(1.5)
    assert method.ROUNDS == 6 and method.PAIRS_PER_BOX == 80


def test_c6_finisher_checked_pass_keeps_exact_ballast_after_later_error(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch, passes=False)
    monkeypatch.setattr(method, 'ROUNDS', 1)
    exact = NS(id='cold-shaped-and-finished', ballast=NS(tank_masses_kg={'DB-F-P': 123.}))
    report = NS(passed=True, count=NS(weighted_nonproductive=7.), violations=[])
    received = []
    def finish(*args, on_checked, on_check, **kwargs):
        received.append(kwargs['finalize_s'])
        on_check()
        on_checked(exact, report)
        raise RuntimeError('later finisher telemetry failed')
    monkeypatch.setattr(method, '_finish_candidate', finish)
    published=[]
    context=dict(problem=problem, finalize_s=600., tries=3,
                 publish_candidate=lambda value, **kwargs: published.append((value, kwargs)))
    assert list(method.iter_candidates(instance, context=context)) == [scored]
    assert received == [180.]
    assert context['checked_passes'][0].plan is exact
    assert context['_candidate_plans'][method.assignment_key(scored.actions)] is exact
    assert published[-1][1]['checked'] is True and published[-1][0].plan is exact
    assert context['assignment_metadata']['full_checker_passes'] == 1
    assert 'later finisher telemetry failed' in context['assignment_metadata']['finishing'][0]['error']


def test_c6_finish_control_exception_propagates_after_holding_pass(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch, passes=False)
    monkeypatch.setattr(method, 'ROUNDS', 1)
    stop = RuntimeError('root proved-optimal control')
    exact = NS(id='native-checked', ballast=NS(tank_masses_kg={'DB-F-P': 1.}))
    report = NS(passed=True, count=NS(weighted_nonproductive=7.), violations=[])
    def finish(*args, on_checked, on_check, **kwargs):
        on_check(); on_checked(exact, report)
        raise AssertionError('must stop before returning')
    monkeypatch.setattr(method, '_finish_candidate', finish)
    def callback(value, *, checked, report):
        if checked:raise stop
    context=dict(problem=problem,publish_candidate=callback)
    stream=method.iter_candidates(instance,context=context)
    assert next(stream) is scored
    with pytest.raises(RuntimeError) as error:
        next(stream)
    assert error.value is stop
    assert context['checked_passes'][0].plan is exact


def test_c6_no_capacity_or_lab_runtime_imports():
    import ast
    from pathlib import Path
    for path in Path(method.__file__).parent.glob('*.py'):
        if path.name.startswith('test_'):continue
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != 'capacity' and node.module != '.capacity'
                assert not node.module or 'lab.' not in node.module
            elif isinstance(node, ast.Import):
                assert all(alias.name != 'capacity' and 'lab.' not in alias.name for alias in node.names)


def test_public_c6_finish_recovers_checked_exact_plan_after_optimizer_error(monkeypatch):
    instance, problem, scored, calls = _fake(monkeypatch, passes=False)
    class Place:
        def __init__(self, box, slot):self.box,self.slot=box,slot
    actions=(Place('a','1'),Place('b','2'))
    runtime=NS(Place=Place,get_action_space=lambda name:NS(from_plan=lambda *args:actions),
               Scored=lambda actions,objective,plan:NS(actions=actions,objective=objective,plan=plan))
    monkeypatch.setattr(method,'_finish_runtime',lambda:runtime)
    exact=NS(id='held-full-pass',ballast=NS(tank_masses_kg={'DB-F-P':123.}))
    report=NS(passed=True,count=NS(weighted_nonproductive=7.),violations=[])
    def broken(*args,on_check,on_checked,**kwargs):
        assert kwargs['finalize_s']==180.
        on_check();on_checked(exact,report)
        raise RuntimeError('serialization after full pass')
    monkeypatch.setattr(method,'_finish_candidate',broken)
    context=dict(problem=problem)
    result,metadata=method.finish(instance,scored.plan,finalize_s=600.,context=context)
    assert result is exact and metadata['passed'] and metadata['recovered_checked_pass']
    assert context['checked_passes'][0].plan is exact
