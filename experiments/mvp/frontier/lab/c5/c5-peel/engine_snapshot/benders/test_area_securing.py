"""Cold routing and full-checker acceptance, without retained artifact inputs."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from stowage.model import MovePlan
from stowage.model.fixtures import build_instance

from . import area_securing as area
from .permute import optimize_assignment
from .optimize import optimize


def _report(*violations, passed=False):
    return NS(passed=passed, violations=list(violations),
              count=NS(weighted_nonproductive=11.))


def _violation(rule, unit=None):
    return NS(rule_id=rule, unit=unit, severity='hard')


def _plan():
    return MovePlan(id='cold-area-search', instance_id=build_instance().id,
                    call_index=0, moves=[])


def test_only_actual_area_and_securing_failures_enter_search():
    area_failure = _violation(area.SECOND_CHECK, 'm*rad')
    securing = _violation(area.SECURING, '1')
    assert area.eligible(_report(area_failure, securing))
    assert area.eligible(_report(securing))
    assert not area.eligible(_report(_violation(area.SECOND_CHECK, 'm')))
    assert not area.eligible(_report(area_failure, _violation('iscode.loading.trim', 'm')))
    assert not area.eligible(_report(area_failure, _violation('solas.visibility', 'm')))
    assert not area.eligible(_report())
    assert not area.eligible(_report(passed=True))


def test_cold_route_uses_only_in_memory_engine_stow_and_fresh_physics(monkeypatch):
    instance, plan = build_instance(), _plan()
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('cold search must not read external records or recipes')))
    calls = []
    def local(inst, seed_plan, **kwargs):
        assert inst is instance and seed_plan is plan
        assert 'out' not in kwargs
        calls.append(seed_plan.id)
        return seed_plan, dict(full_checker_passed=True, best_physics={'tank_masses_kg': {}})
    monkeypatch.setattr(area, 'optimize', local)
    monkeypatch.setattr(area, 'check', lambda *args: _report(passed=True))
    candidate, row = area.refine(instance, plan,
        _report(_violation(area.SECOND_CHECK, 'm*rad')))
    assert candidate is plan and row['passed'] and row['cold']
    assert row['physical_infeasibility_proven'] is False
    assert calls == ['cold-area-search']


def test_search_metadata_cannot_replace_full_checker(monkeypatch):
    monkeypatch.setattr(area, 'optimize', lambda inst, plan, **kwargs: (
        plan, dict(full_checker_passed=True, best_physics=None)))
    monkeypatch.setattr(area, 'check', lambda *args: _report(_violation(area.SECURING)))
    candidate, row = area.refine(build_instance(), _plan(),
        _report(_violation(area.SECURING)), permute_cargo=False)
    assert candidate is None and not row['passed']


def test_external_recipe_or_record_paths_are_not_an_input_api(monkeypatch):
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('external record read')))
    with pytest.raises(TypeError, match='in-memory'):
        area.refine(build_instance(), Path('/tmp/retained-plan.json'), _report(_violation(area.SECURING)))
    with pytest.raises(TypeError, match='in-memory'):
        optimize(build_instance(), '/tmp/retained-plan.json')
    with pytest.raises(TypeError, match='in-memory'):
        optimize_assignment(build_instance(), '/tmp/retained-plan.json')
    with pytest.raises(TypeError, match='in-memory'):
        optimize_assignment(build_instance(), _plan(), '/tmp/retained-physics.json')
