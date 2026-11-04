"""Gold admission tests: real CPU mask, synthetic bound full-checker oracle."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import (BallastCondition, CheckReport, PartReport, PlannerResult,
                           VoyageConditionIdentity, VoyageSpec)
from stowage.model.fixtures import build_instance
from stowage.moves import count_moves


@pytest.fixture
def driver():
    path = Path(__file__).resolve().parents[2] / 'experiments/gold-plans/tiny/driver.py'
    spec = importlib.util.spec_from_file_location('gold_admission_driver', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def case(driver, tmp_path, monkeypatch):
    instance = build_instance()
    box = instance.containers[1].model_copy(update={'dg': None, 'oog': None, 'special_stowage': []})
    instance.containers = [box]
    instance.load_list = [box.id]
    instance.discharge_list = []
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={
        'key': 'A-001-01', 'length_ft': 20, 'boxes': [box.id]})]
    instance.voyage = VoyageSpec(**{phase: VoyageConditionIdentity(
        id=f'gold.{phase}', phase=phase, call_index=i,
        locode=instance.rotation.calls[i].locode, environment_id=f'synthetic-{phase}',
        density_id='synthetic-seawater', water_density_kg_m3=1025.)
        for i, phase in enumerate(('departure', 'arrival'))})
    plan = get_action_space().expand(instance, [Place(box.id, '0010182')])
    plan.ballast = BallastCondition(source='planner', tank_masses_kg={})
    result = PlannerResult(status='ok', plan=plan, planner='solver.exact', planner_version='synthetic',
                           metrics={'global_screen': 'on', 'shippable': True, 'mask_verified': True})
    # Hull-free synthetic oracle isolates mask and writer policy. It is never
    # production C2 qualification or a physically approved gold witness.
    config = driver.Config(output=tmp_path, hard_stop_at='2030-01-01T00:00:00+00:00', c2_off=True)
    monkeypatch.setattr(driver, '_plan', lambda *args: result)
    monkeypatch.setattr(driver, '_finalize', lambda i, r, c: r)
    monkeypatch.setattr(driver, 'source_fingerprint', lambda: {'sha256': 'epoch'})
    versions = driver.component_versions()
    monkeypatch.setattr(driver, 'component_versions', lambda: dict(versions))
    def phase(function, args, limit):
        return function(*args), dict(sources_before={'sha256': 'epoch'}, sources_after={'sha256': 'epoch'},
                                    versions_before=dict(versions), versions_after=dict(versions))
    monkeypatch.setattr(driver, '_phase', phase)
    monkeypatch.setattr(driver, '_check', bound_report)
    return instance, result, config


def bound_report(instance, plan):
    parents = [f'checker.{kind}.sha256:' + sha256(json.dumps(value.model_dump(mode='json'),
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        for kind, value in [('instance', instance), ('plan', plan)]]
    names = ['moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints', 'voyage']
    names += [f'condition:{identity.id}:{part}'
              for identity in (instance.voyage.departure, instance.voyage.arrival)
              for part in ('stability', 'strength')]
    return CheckReport(passed=True, count=count_moves(instance, plan),
                       parts={name: PartReport(name=name) for name in names},
                       provenance={'parents': parents})


def test_real_cpu_mask_rejects_checker_passing_action(driver, case):
    from stowage.model.cargo import SpecialStowage
    instance, result, config = case
    instance.containers[0].special_stowage = [SpecialStowage.UNDER_DECK]
    outcome = driver._judge(instance, 'exact', config)
    assert not outcome['accepted'], 'checker-passing, mask-rejected candidate published'
    assert outcome['mask_verified'] is False
    assert outcome['mask_replay']['error_type']


@pytest.mark.parametrize('status', ['ok', 'timeout'])
def test_declared_ballast_missing_blocks_before_finalization(driver, case, monkeypatch, status):
    instance, result, config = case
    result.status = status
    result.plan.ballast = None
    monkeypatch.setattr(driver, '_finalize', lambda *args: pytest.fail('declaration bypassed'))
    outcome = driver._judge(instance, 'exact', config)
    assert not outcome['accepted']
    assert 'sets_ballast' in outcome['message']


def test_unbound_report_is_not_shipping_evidence(driver, case, monkeypatch):
    instance, result, config = case
    report = bound_report(instance, result.plan)
    report.provenance.parents = []
    monkeypatch.setattr(driver, '_check', lambda *args: report)
    outcome = driver._judge(instance, 'exact', config)
    assert not outcome['accepted']
    assert outcome['report_binding_valid'] is False


@pytest.mark.parametrize('omission', ['count', 'parts', 'arrival'])
def test_incomplete_report_cannot_ship_even_with_claimed_flags(driver, case, monkeypatch, omission):
    instance, result, config = case
    def check(i, p):
        report = bound_report(i, p)
        if omission == 'count': report.count = None
        elif omission == 'parts': report.parts = {}
        else: report.parts.pop(f'condition:{i.voyage.arrival.id}:strength')
        return report
    monkeypatch.setattr(driver, '_check', check)
    outcome = driver._judge(instance, 'exact', config)
    assert not outcome['accepted']


def test_real_mask_complete_bound_report_admits_current_candidate(driver, case):
    instance, result, config = case
    outcome = driver._judge(instance, 'exact', config)
    assert outcome['accepted'], outcome.get('mask_replay', outcome)
    assert outcome['shippable'] is True
    assert outcome['mask_verified'] is True
    assert outcome['report_binding_valid'] is True
    assert outcome['mask_replay']['status'] == 'complete'


def test_mask_runtime_exception_is_recorded(driver, case, monkeypatch):
    instance, result, config = case
    def broken(*args):
        raise RuntimeError('synthetic replay backend failure')
    monkeypatch.setattr(driver, '_replay_mask', broken, raising=False)
    outcome = driver._judge(instance, 'exact', config)
    assert not outcome['accepted']
    assert outcome['mask_replay']['error_type'] == 'RuntimeError'
    assert 'synthetic replay backend failure' in outcome['mask_replay']['message']


def test_production_replay_refuses_actual_c2_unavailable_despite_planner_claim(driver, case):
    instance, result, config = case
    # The hull-free test fixture has no qualified global model. A planner's
    # claim that C2 ran cannot replace the replay engine's actual diagnostics.
    outcome = driver._judge(instance, 'exact', replace(config, c2_off=False))
    assert not outcome['accepted']
    assert not outcome['mask_verified']
    assert 'actual C2 on' in outcome['mask_replay']['message']


def test_timeout_incumbent_requires_fresh_evidence_and_never_claims_proof(driver, case):
    instance, result, config = case
    result.status = 'timeout'
    result.metrics['optimality_status'] = 'TIME_LIMIT'
    outcome = driver._judge(instance, 'exact', config)
    assert outcome['accepted']
    assert outcome['raw_status'] == 'timeout'
    assert outcome['timeout_policy'] == 'recheck_complete_incumbent_without_optimality_claim'
    assert outcome['metrics']['optimality_status'] == 'TIME_LIMIT'


def test_scalable_missing_ballast_is_finished_then_independently_replayed(driver, case, monkeypatch):
    instance, result, config = case
    result.plan.ballast = None
    finalized = []
    def finish(i, candidate, cfg):
        finalized.append(candidate)
        plan = candidate.plan.model_copy(update={
            'ballast': BallastCondition(source='finisher', tank_masses_kg={})})
        return candidate.model_copy(update={'plan': plan})
    monkeypatch.setattr(driver, '_finalize', finish)
    outcome = driver._judge(instance, 'scalable', config)
    assert outcome['accepted'] and outcome['mask_verified'] and outcome['shippable']
    assert len(finalized) == 1
    assert outcome['mask_replay']['global_screen'].startswith('off:')


def test_actual_full_checker_physical_failure_rejects_candidate(driver, case, monkeypatch):
    from time import monotonic
    from stowage.checker import check
    from stowage.model import ShipState
    from stowage.solver.exact_deadline import run_with_deadline
    from stowage.vessel.fleet import load_vessel

    instance, result, config = case
    # Production vessel/hull, policy, tank metadata and strength limits. A lone
    # 12 t box on an otherwise light feeder is physically invalid. The voyage
    # identities above explicitly declare synthetic next-call seawater input;
    # the actual D50 drawdown/endpoint checker remains active.
    instance.vessel = load_vessel('feeder_500')
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    def bounded_check(i, p):
        checked = run_with_deadline(check, (i, p), deadline=monotonic()+10.)
        assert not checked.timed_out, 'actual full checker exceeded bounded regression budget'
        return checked.value
    monkeypatch.setattr(driver, '_check', bounded_check)
    outcome = driver._judge(instance, 'exact', replace(config, checker_s=10.))
    assert not outcome['accepted']
    report = outcome['report']
    assert report['passed'] is False
    # D57 evaluates five parts at departure, midpoint and arrival. This light
    # condition exceeds D56's lashing design GM, so unavailable lashing evidence
    # is an expected hard rejection; the other physical checks must still run.
    identities = (instance.voyage.departure.id,
                  f'{instance.voyage.departure.id}:mid_voyage', instance.voyage.arrival.id)
    for identity in identities:
        for name in ('stability', 'strength', 'visibility', 'constraints'):
            assert report['parts'][f'condition:{identity}:{name}']['ran']
        lashing = report['parts'][f'condition:{identity}:lashing']
        assert not lashing['ran']
        assert 'GM above lashing design GM' in lashing['error']
    assert all(report['parts'][name]['ran'] for name in ('moves', 'rules', 'voyage'))
    assert any(v['rule_id'].startswith('iscode.') for v in report['violations'])


def test_case_budget_includes_both_mask_replay_phases(driver, case):
    _, _, config = case
    requested = config.search_s + config.scalable_s + 2 * (config.finalize_s + 2 * config.checker_s)
    assert driver.case_budget(config) >= requested


def test_stale_component_epoch_is_rejected(driver, case, monkeypatch):
    instance, result, config = case
    current_phase = driver._phase
    def stale_phase(function, args, limit):
        value, metadata = current_phase(function, args, limit)
        metadata['versions_after']['checker'] += '.stale'
        return value, metadata
    monkeypatch.setattr(driver, '_phase', stale_phase)
    outcome = driver._judge(instance, 'exact', config)
    assert not outcome['accepted']
    assert outcome['checker_passed'] and outcome['mask_verified']
    assert outcome['message'] == 'component versions changed during a phase'
