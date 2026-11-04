"""CPU-only D128 recipe checks; no live source mutation or corpus evaluation."""
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def harness(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[3]
    stage = root/'experiments/mvp/exit/patches'
    modules = {}
    for name in ('checker_watchdog', 'outcomes', 'final_recipe'):
        path = stage/('d129' if name == 'final_recipe' else 'd124')/f'{name}.py'
        spec = importlib.util.spec_from_file_location(f'_d128_{name}', path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        if name != 'final_recipe':
            monkeypatch.setitem(sys.modules, f'llm.exit.{name}', module)
        modules[name] = module
    from llm.exit import runtime, identity, selection
    from llm.stowlm import planner
    from stowage.model.fixtures import build_instance
    from stowage.model import MovePlan
    instance = build_instance()
    path = tmp_path/'map.json'; path.write_text(instance.model_dump_json())
    plan = MovePlan(id='own', instance_id=instance.id, call_index=instance.call_index)
    calls = dict(config=[], events=[], verify=[], resident=[])
    class Planner:
        def __init__(self, **kwargs): calls['config'].append(kwargs)
        def plan(self, instance, **kwargs):
            calls['events'].append(('plan', instance.id, kwargs))
            return SimpleNamespace(status='ok', plan=plan, metrics={})
    def verify(instance, candidate, **kwargs):
        calls['events'].append(('verify', instance.id))
        calls['verify'].append(kwargs)
        return SimpleNamespace(passed=True, plan=plan, weighted_moves=3.,
            details=dict(checker_available=True, report={'passed':True}))
    monkeypatch.setattr(planner, 'StowLMPlanner', Planner)
    monkeypatch.setattr(runtime, '_resident_for', lambda p:calls['resident'].append(p))
    monkeypatch.setattr(identity, 'identity_guard', lambda *_:
        SimpleNamespace(require_allowed=lambda *args, **kwargs:None))
    monkeypatch.setattr(selection, 'verify_candidate', verify)
    monkeypatch.setattr(selection, 'decoder_factory', lambda:'production-lookahead-securing')
    final = modules['final_recipe']
    monkeypatch.setattr(final, 'scoped_v4_finalize', nullcontext)
    monkeypatch.setattr(final, '_pins', lambda _:
        dict(adapter={'weights':'adapter'}, sources={'final_recipe.py':'d128-source'}))
    monkeypatch.setattr(final, '_admit_cached', lambda row,*args:row)
    monkeypatch.delenv('STOWAGE_BALLAST_V4', raising=False)
    args = SimpleNamespace(corpus=tmp_path, seed=17, max_tokens=123)
    return final, path, plan, args, calls, Planner, modules


def test_d128_exact_greedy_recipe_default120_and_no_last_resort(harness, tmp_path):
    final, path, _, args, calls, _, _ = harness
    # Generic sampling settings may exist on runner args; final audit must ignore them.
    args.temperature = .8; args.k = 8
    out = tmp_path/'out'
    row = final.evaluate_recipe(tmp_path/'adapter', [path], out, args)[0]
    config = calls['config'][0]
    assert {k:config[k] for k in ('n_samples', 'temperature', 'greedy_first', 'backtrack',
                                'candidate_finalize_s', 'last_resort')} == dict(
        n_samples=1, temperature=0., greedy_first=True, backtrack=32,
        candidate_finalize_s=120., last_resort=False)
    assert config['decoder_factory'] == 'production-lookahead-securing'
    assert config['engine_config'].extra == {'global_screen':0}
    assert calls['events'][0][2] == dict(seed=17, time_limit_s=600.)
    assert calls['verify'] == [dict(ballast_s=120.)]
    recipe = json.loads((out/'identity.json').read_text())['recipe']
    assert recipe['sampling'] is False and recipe['sequential'] is True
    assert recipe['version'] == 'd128-greedy-sequential-v1'
    assert recipe['check_watchdog'] == dict(version='d124-owned-subprocess-v1', timeout_s=300.)
    assert row['passed'] and row['recipe_version'] == recipe['version']
    assert 'ballast by the deterministic V4 finisher' in row['label']


def test_two_maps_plan_and_verify_sequentially(harness, tmp_path):
    final, path, _, args, calls, _, _ = harness
    second = tmp_path/'second.json'; second.write_bytes(path.read_bytes())
    rows = final.evaluate_recipe(tmp_path/'adapter', [path,second], tmp_path/'out', args)
    assert [event[0] for event in calls['events']] == ['plan','verify','plan','verify']
    assert len(rows) == len(calls['resident']) == 2


def test_finisher_override_matches_both_calls_and_binds_cache(harness, tmp_path):
    final, path, _, args, calls, _, _ = harness
    args.final_recipe_finalize_s = 150.
    out = tmp_path/'out'
    final.evaluate_recipe(tmp_path/'adapter', [path], out, args)
    assert calls['config'][0]['candidate_finalize_s'] == 150.
    assert calls['verify'] == [dict(ballast_s=150.)]
    final.evaluate_recipe(tmp_path/'adapter', [path], out, args)
    assert len(calls['resident']) == 1
    args.final_recipe_finalize_s = 120.
    with pytest.raises(RuntimeError, match='cache adapter, maps, source or recipe'):
        final.evaluate_recipe(tmp_path/'adapter', [path], out, args)
    assert len(calls['resident']) == 1


@pytest.mark.parametrize('budget', [0., -1., float('inf'), float('nan')])
def test_invalid_finisher_budget_refused_before_model(harness, tmp_path, budget):
    final, path, _, args, calls, _, _ = harness
    args.final_recipe_finalize_s = budget
    with pytest.raises(ValueError):
        final.evaluate_recipe(tmp_path/'adapter', [path], tmp_path/'out', args)
    assert calls['resident'] == []


def test_d128_keeps_internal_watchdog_and_live_source_immutable(harness, monkeypatch, tmp_path):
    import stowage.checker as checker
    final, path, plan, args, calls, planner, modules = harness
    original = checker.check
    live = Path(__file__).resolve().parents[3]/'src/llm/exit/final_recipe.py'
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    watchdog = modules['checker_watchdog']
    monkeypatch.setattr(watchdog, 'run_full_check', lambda *args, **kwargs:
        watchdog.CheckOutcome(False, reason='watchdog_expired'))
    def run(self, instance, **kwargs):
        from stowage.checker import check
        try:
            check(instance, plan)
        except Exception:
            pytest.fail('planner swallowed checker unavailability')
        return SimpleNamespace(status='ok', plan=plan, metrics={})
    monkeypatch.setattr(planner, 'plan', run)
    row = final.evaluate_recipe(tmp_path/'adapter', [path], tmp_path/'out', args)[0]
    assert row['unavailable'] and not row['passed'] and row['plan'] is None
    assert row['verification_counts']['unavailable'] == 1
    assert row['verification_counts']['physics_failed'] == 0
    assert calls['verify'] == [] and checker.check is original
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before
