from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import HatchCoverOff, Load, Relocate
from stowage.moves import apply_plan
from . import sequence


def example():
    instance = build_instance()
    a, b = instance.containers[2:4]
    plan = MovePlan(id='sequence-test', instance_id=instance.id, call_index=0, moves=[
        Load(call_index=0, container_id=a.id, src='A-003-01-01', dst='0010182'),
        Load(call_index=0, container_id=b.id, src='A-004-01-01', dst='0010282')])
    return instance, plan


def frame(step, passed=True):
    return dict(step=step, stability_passed=passed, strength_passed=True, error=None)


def test_public_move_engine_accepts_both_orders_and_preserves_reached_world():
    instance, plan = example()
    swapped, proof = sequence.adjacent_swap(instance, plan, 1)
    assert swapped.moves == list(reversed(plan.moves))
    assert proof['orders_both_legal'] and proof['normalized_reached_world_equal']
    assert sequence.normalized_world(apply_plan(instance, swapped).final_state) == \
        sequence.normalized_world(apply_plan(instance, plan).final_state)
    assert apply_plan(instance, swapped).count == apply_plan(instance, plan).count


def test_support_dependency_and_hatch_operations_cannot_be_swapped():
    instance, plan = example()
    dependent = plan.model_copy(update={'moves': [plan.moves[0], plan.moves[1].model_copy(update={'dst': '0010184'})]})
    assert sequence.adjacent_swap(instance, dependent, 1) is None
    hatch = plan.model_copy(update={'moves': [plan.moves[0], HatchCoverOff(call_index=0, cover_id='HC-02-2')]})
    assert sequence.adjacent_swap(instance, hatch, 1) is None
    assert sequence.adjacent_swap(instance, plan, 0) is None
    assert sequence.adjacent_swap(instance, plan, 2) is None


def test_independent_yard_relocation_can_commute_without_changing_ship():
    instance, plan = example()
    relocation = Relocate(call_index=0, container_id=instance.containers[0].id,
                          src='A-001-01-01', dst='A-005-01-01')
    candidate = plan.model_copy(update={'moves': [plan.moves[0], relocation, plan.moves[1]]})
    swapped, proof = sequence.adjacent_swap(instance, candidate, 1)
    assert swapped.moves[0] == relocation
    assert proof['transformation'] == 'load/ship-neutral yard-relocate swap'
    assert apply_plan(instance, swapped).count == apply_plan(instance, candidate).count


@pytest.fixture
def repair_case(monkeypatch, tmp_path):
    from app import stage_physics
    from . import gates, runtime
    from . import batch
    instance, plan = example()
    epoch = 'test-source-epoch'
    envelope = dict(**stage_physics.input_digests(instance, plan), physics_epoch=epoch,
                    frames=[frame(0), frame(1, False)])
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    monkeypatch.setattr(stage_physics, 'physics_epoch', lambda: epoch)
    monkeypatch.setattr(stage_physics, '_frames_ok', lambda frames, n: all('step' in f for f in frames))
    monkeypatch.setattr(stage_physics, 'compute_frame', lambda i, p, k: frame(k))
    identity = dict(passed=True, engine_version='0.13.0', engine_manifest_sha256='test-manifest')
    monkeypatch.setattr(gates, 'engine_identity', lambda: identity)
    monkeypatch.setattr(gates, 'runtime_cache_scope', nullcontext)
    monkeypatch.setattr(runtime, 'discarded_plot_scope', nullcontext)
    monkeypatch.setattr(batch, 'binding_fingerprint', lambda: 'test-binding-source')
    def check(i, p):
        count = apply_plan(i, p).count
        return SimpleNamespace(passed=True, count=count,
            model_dump=lambda **kw: dict(passed=True, count=count.model_dump(mode='json')))
    monkeypatch.setattr(gates, '_check', check)
    monkeypatch.setattr(gates, '_clean', lambda *args: None)
    return instance, plan, envelope, epoch, tmp_path


def test_trial_success_still_requires_shared_fresh_full_gate(repair_case, monkeypatch):
    from . import gates
    instance, plan, envelope, epoch, directory = repair_case
    calls = []
    def gate(i, candidate, rules, **kwargs):
        calls.append((candidate, kwargs))
        return dict(passed=True, checker=dict(count=apply_plan(i, candidate).count.model_dump(mode='json')))
    monkeypatch.setattr(gates, 'gate_plan', gate)
    repaired, audit = sequence.repair_candidate(instance, plan, envelope, epoch,
        checkpoint_dir=directory, profile={'name': 'standard'})
    assert len(calls) == 1 and calls[0][1]['admit'] is False
    assert calls[0][1]['frame_checkpoint_path'].name.endswith('.native-frames.json')
    metadata = audit['sequence_repair']
    assert metadata['native_engine_output'] is False
    assert metadata['original_profile'] == {'name': 'standard'}
    assert metadata['original_plan_sha256'] == sequence.sha256_json(plan.model_dump(mode='json'))
    assert metadata['new_plan_sha256'] == sequence.sha256_json(repaired.model_dump(mode='json'))
    trial = json.loads((directory/'attempt-1.trial-frames.json').read_text())
    assert trial['release_certificate'] is False
    assert 'trial only' in trial['scope']


def test_shared_gate_rejection_cannot_be_overridden_by_green_trial(repair_case, monkeypatch):
    from . import gates
    instance, plan, envelope, epoch, directory = repair_case
    monkeypatch.setattr(gates, 'gate_plan', lambda i, p, r, **kw: dict(passed=False,
        checker=dict(count=apply_plan(i, p).count.model_dump(mode='json'))))
    assert sequence.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory) is None


def test_yard_relocate_then_opposite_load_repairs_in_two_recorded_swaps(repair_case, monkeypatch):
    from app import stage_physics
    from . import gates
    instance, original, _, epoch, directory = repair_case
    relocation = Relocate(call_index=0, container_id=instance.containers[0].id,
                          src='A-001-01-01', dst='A-005-01-01')
    plan = original.model_copy(update={'moves': [original.moves[0], relocation, original.moves[1]]})
    envelope = dict(**stage_physics.input_digests(instance, plan), physics_epoch=epoch,
                    frames=[frame(0), frame(1, False)])
    calls = []
    def native_frame(i, p, k):
        remains_bad = k == 2 and p.moves[1] == original.moves[0]
        return frame(k, not remains_bad)
    monkeypatch.setattr(stage_physics, 'compute_frame', native_frame)
    def gate(i, p, rules, *, frame_checkpoint_path, **kwargs):
        calls.append(p)
        return dict(passed=True, checker=dict(count=apply_plan(i, p).count.model_dump(mode='json')))
    monkeypatch.setattr(gates, 'gate_plan', gate)
    repaired, audit = sequence.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory)
    assert repaired.moves == [relocation, original.moves[1], original.moves[0]]
    assert len(calls) == 1
    assert [a['proof']['transformation'] for a in audit['sequence_repair']['attempts']] == [
        'load/ship-neutral yard-relocate swap', 'load/load swap']


@pytest.mark.parametrize('changed', ['epoch', 'plan', 'instance'])
def test_unbound_cached_frames_never_trigger_cpu(repair_case, monkeypatch, changed):
    from app import stage_physics
    instance, plan, envelope, epoch, directory = repair_case
    envelope[dict(epoch='physics_epoch', plan='plan_sha256', instance='instance_sha256')[changed]] = 'wrong'
    monkeypatch.setattr(stage_physics, 'compute_frame', lambda *a: pytest.fail('unbound frames launched physics'))
    assert sequence.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory) is None


def test_objective_change_rejects_even_a_passing_shared_gate(repair_case, monkeypatch):
    from . import gates
    instance, plan, envelope, epoch, directory = repair_case
    monkeypatch.setattr(gates, 'gate_plan', lambda *a, **kw: dict(passed=True, checker=dict(count={'loads': 999})))
    assert sequence.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory) is None
