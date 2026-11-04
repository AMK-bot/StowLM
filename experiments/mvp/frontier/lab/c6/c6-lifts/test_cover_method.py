"""Regression gate: finishing precedes search and every rejection retains ballast."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest

spec = importlib.util.spec_from_file_location('tested_cover_method', Path(__file__).with_name('cover_method.py'))
method = importlib.util.module_from_spec(spec)
spec.loader.exec_module(method)


@pytest.mark.parametrize('outcome', ['exception', 'failed_check', 'more_lifts', 'improvement'])
def test_finished_c5_plan_is_exact_fallback(monkeypatch, outcome):
    plan = object()
    better_plan = object()
    base = NS(propose=object(), finish=object())
    baseline = dict(passed=True, plan=plan, actions=[dict(box='b', slot='s')], lifts=10.,
                    parts={}, wall_s=2., status='passed', method_metadata={'replay_legal': True})
    seen = []
    def evaluate(instance, stored, config, **kwargs):
        assert stored == {} and config['budget_s'] == 240 and config['finalize_s'] == 120
        assert kwargs['method'] is base.propose and kwargs['finish'] is base.finish
        seen.append('finished_baseline')
        return dict(baseline)
    monkeypatch.setattr(method, '_baseline', lambda: base)
    monkeypatch.setattr(method, '_save_baseline', lambda *_: None)
    monkeypatch.setitem(sys.modules, 'score', NS(evaluate=evaluate, plugin_info=lambda _: {}))
    monkeypatch.setitem(sys.modules, 'alns', NS(Scored=lambda actions, objective, plan: NS(actions=actions, objective=objective, plan=plan)))
    monkeypatch.setitem(sys.modules, 'stowage.model', NS(MovePlan=NS(model_validate=lambda p: p)))
    monkeypatch.setitem(sys.modules, 'stowage.actions.space', NS(Place=lambda **kw: NS(**kw)))
    def improve(instance, old, **kwargs):
        assert seen == ['finished_baseline'] and old.plan is plan
        if outcome == 'exception':
            raise RuntimeError('search failed')
        return NS(actions=old.actions, objective=8., plan=better_plan)
    monkeypatch.setattr(method, '_improve', improve)
    monkeypatch.setitem(sys.modules, 'stowage.checker', NS(check=lambda *_: NS(
        passed=outcome != 'failed_check', count=NS(weighted_nonproductive=11 if outcome == 'more_lifts' else 8))))
    context = dict(problem=object(), method_metadata={})
    method.propose(NS(id='id'), seed=0, budget_s=780, context=context)
    result, _ = method.finish(None, None, seed=0, finalize_s=1, label='x', context=context)
    assert result is (better_plan if outcome == 'improvement' else plan)


def test_no_search_without_a_full_c5_pass(monkeypatch):
    monkeypatch.setattr(method, '_baseline', lambda: NS(propose=None, finish=None))
    monkeypatch.setattr(method, '_save_baseline', lambda *_: None)
    monkeypatch.setitem(sys.modules, 'score', NS(evaluate=lambda *a, **k: dict(passed=False, method_metadata={}), plugin_info=lambda _: {}))
    monkeypatch.setattr(method, '_improve', lambda *a, **k: pytest.fail('failed baseline must not be polished'))
    context = dict(problem=object(), method_metadata={})
    assert method.propose(NS(id='id'), seed=0, budget_s=780, context=context) == []
