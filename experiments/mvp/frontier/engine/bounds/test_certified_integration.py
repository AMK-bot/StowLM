"""Exact certificates remain optional; only independent replay creates starts."""
from dataclasses import asdict
import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
import importlib

import pytest

from . import certified
from .exact.certified import CertifiedResult

bounds = importlib.import_module(__package__)


def _exact(cost):
    return CertifiedResult(cost, cost, 0, 'optimal', {'b': 'slot'}, .1, 0.,
                           first_aboard_order=['b'], proof_scope='exact: D11 with D15 fixed stow, no later loads')


@pytest.mark.parametrize('old,new,winner', [(8., 9., 'certified'), (9., 8., 'hatch_only'), (9., 9., 'hatch_only')])
def test_optional_exact_stage_preserves_strongest_bound_and_serializes_witness(monkeypatch, old, new, winner):
    monkeypatch.setattr(bounds, '_legacy_map_bound', lambda *a, **k: (old,
        dict(certificate_source='hatch_only', wall_time_s=.2)))
    monkeypatch.setattr(certified, 'lower_bound', lambda *a, **k: _exact(new))
    value, record = bounds.map_bound(object(), budget_s=1., certified=True)
    assert value == max(old, new)
    assert record['certificate_source'] == winner
    assert record['certified']['relaxation_assignment'] == {'b': 'slot'}
    assert record['certified']['origin'] == 'certified_final_stow_witness'
    json.dumps(record)


def test_exact_error_retains_old_certificate(monkeypatch):
    monkeypatch.setattr(bounds, '_legacy_map_bound', lambda *a, **k: (4.,
        dict(certificate_source='hatch_only', wall_time_s=.2)))
    def unknown(*a, **k):
        raise ValueError('unreviewed oracle domain')
    monkeypatch.setattr(certified, 'lower_bound', unknown)
    value, record = bounds.map_bound(object(), budget_s=1., certified=True)
    assert value == 4.
    assert record['certified_error'] == 'ValueError: unreviewed oracle domain'


def test_exact_witness_is_replayed_and_mask_gated(monkeypatch):
    import alns
    from stowage.actions.space import Place
    assignment = {'b': 'slot'}
    instance = SimpleNamespace(load_list=['b'], onboard=SimpleNamespace(placements=[]), discharge_list=[])
    problem = SimpleNamespace(instance=instance, score=lambda actions: object())
    monkeypatch.setattr(alns, 'Sequencer', lambda p: SimpleNamespace(order=lambda a: None))
    calls = []
    monkeypatch.setattr(alns, '_mask_ok', lambda p, actions: calls.append(actions) or True)
    record = dict(certificate_source='hatch_only', certified=asdict(_exact(4.)))
    actions = certified.repair(problem, record, deadline=monotonic()+1., greedy=False)
    assert actions == [(Place('b', 'slot'),)]
    assert calls == actions
    assert certified.repair.last_stats['full_checker_pass_claim'] is False


def test_real_eight_lift_first_aboard_witness_remains_a_replay_legal_start():
    from alns import Problem
    from stowage.io import load_instance
    from ..e9fix import install
    fixture = Path(__file__).parent/'exact/fixtures/checked25.json'
    row = json.loads(fixture.read_text())
    install()
    instance = load_instance('experiments/mvp/corpus/val/small/feeder_800/simulated_yard/seed-040100025.json')
    problem = Problem(instance)
    witness = dict(relaxation_assignment=row['assignment'], first_aboard_order=row['first_aboard_order'])
    starts = certified.repair(problem, witness, deadline=monotonic()+10., greedy=False)
    assert starts
    assert min(problem.score(actions).objective for actions in starts) == 8.
    assert problem.engine.depth == 0


def test_scope_is_named_string_with_explicit_D15_limit():
    from .scope import proof_scope, proof_details
    scope = proof_scope('certified_hatch_first_aboard_heat_e6_oog_securing')
    assert isinstance(scope, str)
    assert scope.startswith('certified_hatch_first_aboard_heat_e6_oog_securing:')
    assert 'checker-passing plans' in scope
    assert 'D15 fixed stow, no later loads' in scope
    assert proof_details('exact')['full_checker_pass_claim'] is False


@pytest.mark.parametrize('counting,expected', [(4., 'hatch_only'), (5., 'counting_only_fallback')])
def test_unsupported_exact_counting_fallback_never_masquerades_as_exact(monkeypatch, counting, expected):
    from .scope import proof_scope
    monkeypatch.setattr(bounds, '_legacy_map_bound', lambda *a, **k: (4.,
        dict(certificate_source='hatch_only', wall_time_s=.2)))
    fallback = _exact(counting)
    fallback.status = 'counting_only'
    fallback.relaxation_assignment = None
    fallback.first_aboard_order = None
    fallback.proof_scope = proof_scope('counting_only_fallback')
    monkeypatch.setattr(certified, 'lower_bound', lambda *a, **k: fallback)
    value, record = bounds.map_bound(object(), budget_s=1., certified=True)
    assert value == max(4., counting)
    assert record['certificate_source'] == expected
    assert record['proof_scope'].startswith(expected+':')
    assert record['certified']['status'] == 'counting_only'
    assert record['certified']['origin'] == 'counting_only_fallback'
    assert record['certified']['relaxation_assignment'] is None
