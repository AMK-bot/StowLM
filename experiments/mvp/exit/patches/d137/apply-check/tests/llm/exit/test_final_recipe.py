from contextlib import contextmanager
import json
import os
from types import SimpleNamespace

import pytest

from llm.exit import final_recipe as f


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from llm.exit import runtime, identity, selection
    from llm.stowlm import planner
    from stowage.model import MovePlan
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    path = tmp_path / 'map.json'
    path.write_text(instance.model_dump_json())
    plan = MovePlan(id='own-plan', instance_id=instance.id, call_index=instance.call_index)
    calls = dict(construct=[], plan=[], resident=[], verify=[], guard=[])
    args = SimpleNamespace(corpus=tmp_path, max_tokens=123, seed=17)
    class FakePlanner:
        def __init__(self, **kwargs):
            calls['construct'].append(kwargs)
        def plan(self, instance, **kwargs):
            assert getattr(self, '_model', None) is resident
            calls['plan'].append(kwargs)
            return SimpleNamespace(status='ok', plan=plan, metrics={})
    resident = object()
    def load(value):
        calls['resident'].append(value)
        value._model = resident
        return resident
    def verify(instance, candidate, **kwargs):
        assert os.environ.get('STOWAGE_BALLAST_V4') != '1'
        calls['verify'].append((candidate.source, kwargs))
        return SimpleNamespace(passed=True, plan=plan, weighted_moves=3., details=dict(report={}))
    class Guard:
        def require_allowed(self, *args, **kwargs):
            calls['guard'].append(kwargs)
    monkeypatch.setattr(planner, 'StowLMPlanner', FakePlanner)
    monkeypatch.setattr(runtime, '_resident_for', load)
    monkeypatch.setattr(identity, 'identity_guard', lambda *args: Guard())
    monkeypatch.setattr(selection, 'verify_candidate', verify)
    monkeypatch.setattr(selection, 'decoder_factory', lambda: 'shared-current-layers')
    monkeypatch.setattr(f, '_pins', lambda parent: dict(adapter={'weights': 'a'}, sources={'code': 'b'}))
    monkeypatch.setattr(f, '_admit_cached', lambda row, *args: row)
    @contextmanager
    def scope(enabled):
        old = os.environ.get('STOWAGE_BALLAST_V4')
        os.environ['STOWAGE_BALLAST_V4'] = '1' if enabled else '0'
        try:
            yield
        finally:
            if old is None:
                os.environ.pop('STOWAGE_BALLAST_V4', None)
            else:
                os.environ['STOWAGE_BALLAST_V4'] = old
    monkeypatch.setattr(selection, '_gold_driver', lambda: SimpleNamespace(scoped_ballast_v4=scope))
    monkeypatch.delenv('STOWAGE_BALLAST_V4', raising=False)
    return path, args, calls, FakePlanner


def test_exact_row_d_recipe_uses_resident_model_and_own_full_gates(harness, tmp_path):
    path, args, calls, _ = harness
    rows = f.evaluate_recipe(tmp_path / 'adapter', [path], tmp_path / 'out', args)
    config = calls['construct'][0]
    assert {key: config[key] for key in ('n_samples', 'greedy_first', 'temperature',
        'backtrack', 'candidate_finalize_s', 'max_tokens')} == dict(n_samples=1,
        greedy_first=True, temperature=0., backtrack=32, candidate_finalize_s=120., max_tokens=123)
    assert config['engine_config'].extra == {'global_screen': 0}
    assert config['decoder_factory'] == 'shared-current-layers'
    assert calls['plan'] == [dict(seed=17, time_limit_s=600.)]
    assert calls['verify'][0][0] == 'model'
    assert calls['guard'][0]['role'] == 'audit'
    assert rows[0]['passed'] and rows[0]['weighted_moves'] == 3
    assert rows[0]['recipe'] == 'row_d' and rows[0]['lns'] is False


def test_finalize_v4_scope_and_restoration_even_on_exception(harness, monkeypatch):
    import stowage.planner as module
    calls = []
    def original(*args, **kwargs):
        calls.append(os.environ.get('STOWAGE_BALLAST_V4'))
        raise RuntimeError('finisher failed')
    monkeypatch.setattr(module, 'finalize', original)
    with pytest.raises(RuntimeError, match='finisher failed'):
        with f.scoped_v4_finalize():
            assert os.environ.get('STOWAGE_BALLAST_V4') != '1'
            module.finalize('instance', 'result')
    assert calls == ['1']
    assert module.finalize is original
    assert os.environ.get('STOWAGE_BALLAST_V4') is None


def test_dead_end_remains_failed_without_any_fallback(harness, tmp_path, monkeypatch):
    path, args, calls, planner = harness
    monkeypatch.setattr(planner, 'plan', lambda *args, **kwargs:
                        SimpleNamespace(status='dead_end', plan=None, metrics={}))
    rows = f.evaluate_recipe(tmp_path / 'adapter', [path], tmp_path / 'out', args)
    assert not rows[0]['passed'] and rows[0]['weighted_moves'] is None
    assert calls['verify'] == []


def test_map_and_source_cache_identity_changes_fail_before_gpu(harness, tmp_path, monkeypatch):
    path, args, calls, _ = harness
    out = tmp_path / 'out'
    f.evaluate_recipe(tmp_path / 'adapter', [path], out, args)
    assert len(calls['resident']) == 1
    # Unchanged cache does not reload or decode.
    f.evaluate_recipe(tmp_path / 'adapter', [path], out, args)
    assert len(calls['resident']) == 1
    path.write_text(path.read_text() + '\n')
    with pytest.raises(RuntimeError, match='cache adapter, maps, source or recipe'):
        f.evaluate_recipe(tmp_path / 'adapter', [path], out, args)
    assert len(calls['resident']) == 1


def test_heldout_identity_guard_rejects_before_model_or_cache(harness, tmp_path, monkeypatch):
    from llm.exit import identity
    path, args, calls, _ = harness
    def denied(*args, **kwargs):
        raise ValueError('test/showcase map refused')
    monkeypatch.setattr(identity, 'identity_guard', lambda *args: SimpleNamespace(require_allowed=denied))
    with pytest.raises(ValueError, match='test/showcase'):
        f.evaluate_recipe(tmp_path / 'adapter', [path], tmp_path / 'out', args)
    assert calls['resident'] == []


def test_adapter_source_and_seed_cache_changes_fail_before_gpu(harness, tmp_path, monkeypatch):
    path, args, calls, _ = harness
    out = tmp_path / 'out'
    f.evaluate_recipe(tmp_path / 'adapter', [path], out, args)
    monkeypatch.setattr(f, '_pins', lambda parent: dict(adapter={'weights': 'changed'}, sources={'code': 'b'}))
    with pytest.raises(RuntimeError, match='cache adapter, maps, source or recipe'):
        f.evaluate_recipe(tmp_path / 'adapter', [path], out, args)
    monkeypatch.setattr(f, '_pins', lambda parent: dict(adapter={'weights': 'a'}, sources={'code': 'changed'}))
    with pytest.raises(RuntimeError, match='cache adapter, maps, source or recipe'):
        f.evaluate_recipe(tmp_path / 'adapter', [path], out, args)
    monkeypatch.setattr(f, '_pins', lambda parent: dict(adapter={'weights': 'a'}, sources={'code': 'b'}))
    args.seed += 1
    with pytest.raises(RuntimeError, match='cache adapter, maps, source or recipe'):
        f.evaluate_recipe(tmp_path / 'adapter', [path], out, args)
    assert len(calls['resident']) == 1


def test_cached_pass_requires_bound_report_and_layered_mask_evidence(monkeypatch):
    from stowage import checker
    from stowage.model import CheckReport, MoveCount, MovePlan
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    plan = MovePlan(id='own', instance_id=instance.id, call_index=instance.call_index)
    report = CheckReport(passed=True, count=MoveCount(weighted_nonproductive=3),
                         provenance=dict(versions=dict(checker=checker.CHECKER_VERSION)))
    row = dict(cache_identity='identity', instance_id=instance.id, passed=True,
        plan=plan.model_dump(mode='json'), weighted_moves=3., recipe='row_d', source='own_model',
        verification=dict(report=report.model_dump(mode='json'), mask=dict(passed=True)))
    monkeypatch.setattr(checker, 'report_matches_inputs', lambda *args: True)
    assert f._admit_cached(row, instance, 'identity') is row
    row['verification']['mask']['passed'] = False
    with pytest.raises(RuntimeError, match='lacks bound current own-plan evidence'):
        f._admit_cached(row, instance, 'identity')
