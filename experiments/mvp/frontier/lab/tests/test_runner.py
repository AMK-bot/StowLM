"""Runner safety, ranking and resume behavior without invoking a ballast solver."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
import score


def fixture_problem():
    from stowage.actions.space import Place
    instance = SimpleNamespace(id='random.tiny.fixture', load_list=['box'])
    class Problem:
        def score(self, actions):
            objective = 2. if actions[0].slot == 'costly' else 1.
            return SimpleNamespace(actions=actions, objective=objective, plan=SimpleNamespace(slot=actions[0].slot))
    return instance, Problem(), Place


def config():
    return dict(seed=5, budget_s=1., finalize_s=2., tries=3, input='fixture.json')


def test_claimed_pass_still_goes_through_checker():
    instance, problem, Place = fixture_problem()
    checked = []
    def checker(instance, plan):
        checked.append(plan)
        return SimpleNamespace(passed=False, violations=[SimpleNamespace(rule_id='illegal')])
    row = score.evaluate(instance, {}, config(), problem=problem,
                         method=lambda *args, **kw: [(Place('box', 'bad'),)],
                         finish=lambda *args, **kw: (args[1], {'passed': True, 'weighted': 0}),
                         checker=checker)
    assert checked
    assert row['passed'] is False and row['lifts'] is None
    assert row['assignment'] is None
    assert row['tries'][0]['violations'] == ['illegal']


def test_candidates_are_finished_cheapest_first():
    instance, problem, Place = fixture_problem()
    finished = []
    def finish(instance, plan, **kw):
        finished.append(plan.slot)
        return plan
    count = SimpleNamespace(weighted_nonproductive=1., relocations=1, hatch_cover_lifts=0,
                            restow_lifts=0, future=[])
    row = score.evaluate(instance, {}, config(), problem=problem,
                         method=lambda *args, **kw: [(Place('box', 'costly'),), (Place('box', 'cheap'),)],
                         finish=finish,
                         checker=lambda *args: SimpleNamespace(passed=True, count=count))
    assert finished == ['cheap']
    assert row['passed'] and row['lifts'] == 1.
    assert row['parts']['relocations'] == 1


def test_method_exception_is_failure():
    instance, problem, _ = fixture_problem()
    def crash(*args, **kwargs):
        raise RuntimeError('broken method')
    row = score.evaluate(instance, {}, config(), problem=problem, method=crash)
    assert row['status'] == 'method_error'
    assert row['passed'] is False
    assert 'broken method' in row['failure_reason']


def test_incomplete_candidate_never_reaches_finisher():
    instance, problem, _ = fixture_problem()
    def forbidden(*args, **kwargs):
        raise AssertionError('incomplete plan reached finisher')
    row = score.evaluate(instance, {}, config(), problem=problem,
                         method=lambda *args, **kw: [()], finish=forbidden)
    assert row['status'] == 'no_candidates'
    assert row['passed'] is False and not row['tries']


def test_resume_requires_same_complete_run_key(tmp_path):
    path = tmp_path / 'map.json'
    key = score.run_key(config(), 'input-sha', {})
    assert score.cached_row(path, key) is None
    score.write_json(path, dict(run_key=key, complete=True, passed=False, status='no_candidates'))
    assert score.cached_row(path, key)['status'] == 'no_candidates'
    for changed in (dict(config(), seed=99), dict(config(), finalize_s=10), dict(config(), method='changed')):
        assert score.cached_row(path, score.run_key(changed, 'input-sha', {})) is None
    assert score.cached_row(path, score.run_key(config(), 'new-input-sha', {})) is None
    assert score.cached_row(path, score.run_key(config(), 'input-sha', {'lifts': 2})) is None
    score.write_json(path, dict(run_key=key, complete=False))
    assert score.cached_row(path, key) is None
    path.write_text('{interrupted')
    assert score.cached_row(path, key) is None


def test_resume_skips_proposal_and_recomputes_after_change(tmp_path, monkeypatch):
    import catalog
    import stowage.io
    instance, _, _ = fixture_problem()
    rel = 'experiments/mvp/frontier/lab/tests/fixture.json'
    # Inputs remain in tmp_path. Only this test's path resolver is replaced.
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text('{}')
    monkeypatch.setattr(score, 'ROOT', tmp_path)
    monkeypatch.setattr(catalog, 'ensure_safe', lambda value: value)
    monkeypatch.setattr(stowage.io, 'load_instance', lambda value: instance)
    calls = []
    def evaluate(instance, baseline, run_config):
        calls.append(run_config)
        return dict(instance_id=instance.id, passed=False, status='no_candidates')
    monkeypatch.setattr(score, 'evaluate', evaluate)
    job = (rel, str(tmp_path / 'out'), {}, config())
    first, cached = score.one(job)
    assert not cached
    second, cached = score.one(job)
    assert cached and first == second and len(calls) == 1
    score.one((rel, job[1], {}, dict(config(), budget_s=3)))
    assert len(calls) == 2


def test_real_checker_rejects_illegal_finished_plan():
    from catalog import load_set
    from stowage.io import load_instance
    from stowage.model import MovePlan
    instance = load_instance(score.ROOT / load_set('quick_tiny')[0])
    problem = SimpleNamespace(score=lambda actions: SimpleNamespace(
        actions=actions, objective=0., plan=MovePlan(id='illegal', instance_id=instance.id,
                                                 call_index=instance.call_index)))
    from stowage.actions.space import Place
    # Deliberately bypass the cheap score gate to test the final independent gate.
    candidate = tuple(Place(box, '0010102') for box in instance.load_list)
    row = score.evaluate(instance, {}, config(), problem=problem,
                         method=lambda *args, **kw: [candidate],
                         finish=lambda instance, plan, **kw: (plan, {'passed': True}))
    assert row['passed'] is False and row['lifts'] is None
    assert row['tries'][0]['failure_reason'] == 'full checker rejected finished plan'
