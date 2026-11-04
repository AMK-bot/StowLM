"""Finish routing preserves the full gate and native baseline finishing budget."""
from types import SimpleNamespace

from stowage.model import MovePlan, PlannerResult
from stowage.model.fixtures import build_instance

from . import chain
from .fast_finish import install, worker_inherited


def _plan():
    return MovePlan(id='finish-test', instance_id=build_instance().id, call_index=0, moves=[])


def _report(passed):
    return SimpleNamespace(passed=passed, violations=[],
                           count=SimpleNamespace(weighted_nonproductive=7.))


def test_securing_contradiction_precedes_every_expensive_lane(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, 'apply_plan', lambda *args: SimpleNamespace(
        legal=True, final_state=SimpleNamespace(ship=object())))
    monkeypatch.setattr(chain, 'securing_bound', lambda *args: dict(proof='fixed stow impossible'))
    monkeypatch.setattr(chain, 'check', lambda *args: calls.append('check'))
    result, row = chain.finish_chain(build_instance(), _plan(), area_securing=False)
    assert result is None and not row['passed']
    assert row['finalize_status'] == 'infeasible_fixed_stow'
    assert calls == []


def test_direct_full_pass_needs_no_ballast_lane(monkeypatch):
    monkeypatch.setattr(chain, 'check', lambda *args: _report(True))
    monkeypatch.setattr(chain, 'finish_inherited', lambda *args, **kw: (_ for _ in ()).throw(AssertionError()))
    result, row = chain.finish_chain(build_instance(), _plan(), securing=False)
    assert result is not None and row['passed']
    assert row['lane'] == 'direct' and row['weighted'] == 7.
    assert result.planner_version == chain.VERSION


def test_native_v4_retains_300_seconds_after_failed_extra_lane(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, 'check', lambda *args: _report(False))
    def inherited(*args, **kwargs):
        calls.append(('inherited', kwargs['finalize_s']))
        return None, dict(passed=False)
    def native(instance, plan, **kwargs):
        calls.append(('v4', kwargs['finalize_s']))
        return PlannerResult(status='ok', plan=plan, planner='test', planner_version='test'), dict(passed=True, weighted=7.)
    monkeypatch.setattr(chain, 'finish_inherited', inherited)
    monkeypatch.setattr(chain.frozen_planner, '_finish', native)
    result, row = chain.finish_chain(build_instance(), _plan(), finalize_s=1., securing=False, ballast_lp=False)
    assert result is not None and row['passed'] and row['lane'] == 'v4'
    assert calls == [('inherited', 60.), ('v4', 300.)]


def test_canonical_spawn_worker_is_importable_and_restored():
    from stowage.solver import ballast_v4_dispatch
    original = ballast_v4_dispatch._worker
    restore = install('worker_inherited')
    try:
        assert ballast_v4_dispatch._worker is worker_inherited
        assert worker_inherited.__module__.endswith('engine.finishing.fast_finish')
    finally:
        restore()
    assert ballast_v4_dispatch._worker is original


def test_lp_loading_success_still_needs_full_checker_and_native_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, 'check', lambda *args: _report(False))
    def local(*args, **kwargs):
        calls.append(('lp', kwargs['budget_s']))
        return dict(status='loading_feasible', candidate_plan=_plan(), loading_feasible=True)
    def native(instance, plan, **kwargs):
        calls.append(('v4', kwargs['finalize_s']))
        return None, dict(passed=False, finalize_status='failed')
    monkeypatch.setattr(chain, 'solve_ballast', local)
    monkeypatch.setattr(chain.frozen_planner, '_finish', native)
    result, row = chain.finish_chain(build_instance(), _plan(), finalize_s=1.,
        securing=False, inherited=False)
    assert result is None and not row['passed']
    assert calls == [('lp', 10.), ('v4', 300.)]
    lp = next(stage for stage in row['stages'] if stage['stage'] == 'ballast_lp')
    assert lp['candidate'] and not lp['passed']


def test_lp_candidate_full_pass_precedes_v4(monkeypatch):
    calls = []
    def checked(instance, plan):
        calls.append(plan.id)
        return _report(plan.id == 'lp-recipe')
    monkeypatch.setattr(chain, 'check', checked)
    monkeypatch.setattr(chain, 'solve_ballast', lambda *args, **kwargs: dict(
        status='unknown', candidate_plan=_plan().model_copy(update={'id': 'lp-recipe'})))
    monkeypatch.setattr(chain.frozen_planner, '_finish', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    result, row = chain.finish_chain(build_instance(), _plan(), securing=False, inherited=False)
    assert result is not None and row['passed'] and row['lane'] == 'ballast_lp'
    assert calls == ['finish-test', 'lp-recipe']



def test_rejected_existing_ballast_does_not_bypass_native_search(monkeypatch):
    from stowage.model import BallastCondition
    monkeypatch.setattr(chain, 'check', lambda *args: _report(False))
    seen = []
    def native(instance, plan, **kwargs):
        seen.append((plan.ballast, plan.moves, kwargs['finalize_s']))
        return None, dict(passed=False, finalize_status='failed')
    monkeypatch.setattr(chain.frozen_planner, '_finish', native)
    plan = _plan().model_copy(update={'ballast': BallastCondition(tank_masses_kg={}, source='finisher')})
    chain.finish_chain(build_instance(), plan, securing=False, inherited=False, ballast_lp=False)
    assert seen == [(None, plan.moves, 600.)]


def test_real_area_search_precedes_accelerated_and_native_v4(monkeypatch):
    calls = []
    area_report = SimpleNamespace(passed=False, count=SimpleNamespace(weighted_nonproductive=7.),
        violations=[SimpleNamespace(rule_id='iscode.b2_3.containership_second_check',
                                    unit='m*rad', severity='hard')])
    monkeypatch.setattr(chain, 'check', lambda *args: area_report)
    def area_search(*args, **kwargs):
        calls.append('area')
        return None, dict(passed=False)
    def inherited(*args, **kwargs):
        calls.append('inherited_v4')
        return None, dict(passed=False)
    def native(*args, **kwargs):
        calls.append(('native_v4', kwargs['finalize_s']))
        return None, dict(passed=False)
    monkeypatch.setattr(chain, 'refine_area_securing', area_search)
    monkeypatch.setattr(chain, 'finish_inherited', inherited)
    monkeypatch.setattr(chain.frozen_planner, '_finish', native)
    chain.finish_chain(build_instance(), _plan(), securing=False, ballast_lp=False, finalize_s=1.)
    assert calls == ['area', 'inherited_v4', ('native_v4', 300.)]


def test_fixed_stow_proof_does_not_block_cargo_permutation(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, 'apply_plan', lambda *args: SimpleNamespace(
        legal=True, final_state=SimpleNamespace(ship=object())))
    monkeypatch.setattr(chain, 'securing_bound', lambda *args: dict(proof='fixed stow impossible'))
    report = SimpleNamespace(passed=False, count=SimpleNamespace(weighted_nonproductive=7.),
        violations=[SimpleNamespace(rule_id='iacs.member_irs.container_securing', severity='hard', unit='1')])
    monkeypatch.setattr(chain, 'check', lambda *args: report)
    monkeypatch.setattr(chain, 'refine_area_securing', lambda *args, **kwargs: (
        calls.append(kwargs['permute_cargo']), dict(passed=False)))
    result, row = chain.finish_chain(build_instance(), _plan(), ballast_lp=False)
    assert result is None and calls == [True]
    assert row['finalize_status'] == 'infeasible_fixed_stow'
