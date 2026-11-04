from copy import deepcopy

from stowage.moves import apply_plan
from . import recover_candidate, sequence_blocks
from .test_sequence import example, repair_case


def test_recorded_permutation_reconstructs_exact_candidate_without_search(monkeypatch):
    instance, original = example()
    candidate, proof = sequence_blocks.block_candidates(instance, original, 1)[0]
    monkeypatch.setattr(sequence_blocks, 'block_candidates', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('search called')))
    assert recover_candidate.reconstruct(instance, original, {'proof': proof}) == candidate
    tampered = deepcopy(proof)
    tampered['reached_world_sha256'] = 'wrong'
    assert recover_candidate.reconstruct(instance, original, tampered) is None
    assert recover_candidate.reconstruct(instance, original, dict(proof, permutation=[0, 0])) is None


def test_old_epoch_frames_are_discarded_and_current_shared_gate_is_fresh(repair_case, monkeypatch):
    from app import stage_physics
    from . import gates
    instance, original, _, epoch, directory = repair_case
    candidate, proof = sequence_blocks.block_candidates(instance, original, 1)[0]
    historical = dict(**stage_physics.input_digests(instance, candidate),
                      physics_epoch='obsolete-epoch', frames=[{'forged': 'ignored'}])
    calls = []
    def gate(i, p, rules, **kwargs):
        calls.append(kwargs)
        assert set(kwargs) == {'base_plan', 'admit', 'frame_checkpoint_path'}
        return dict(passed=True, checker=dict(count=apply_plan(i, p).count.model_dump(mode='json')))
    monkeypatch.setattr(gates, 'gate_plan', gate)
    repaired, audit = recover_candidate.recover_candidate(instance, original, {'proof': proof}, historical,
        checkpoint_dir=directory, profile={'name': 'standard'})
    assert repaired == candidate and len(calls) == 1
    meta = audit['sequence_repair']
    assert meta['historical_physics_epoch'] == 'obsolete-epoch'
    assert meta['fresh_physics_epoch'] == epoch
    assert meta['no_engine_search'] is True
    assert meta['original_profile'] == {'name': 'standard'}


def test_wrong_archived_candidate_hash_cannot_trigger_native_work(repair_case, monkeypatch):
    from app import stage_physics
    from . import gates
    instance, original, _, epoch, directory = repair_case
    candidate, proof = sequence_blocks.block_candidates(instance, original, 1)[0]
    historical = dict(**stage_physics.input_digests(instance, candidate), physics_epoch='old')
    historical['plan_sha256'] = 'wrong'
    def check(*args):
        raise AssertionError('wrong archived candidate launched native checker')
    monkeypatch.setattr(gates, '_check', check)
    assert recover_candidate.recover_candidate(instance, original, {'proof': proof}, historical,
                                              checkpoint_dir=directory) is None


def test_current_native_gate_rejection_overrules_all_historical_facts(repair_case, monkeypatch):
    from app import stage_physics
    from . import gates
    instance, original, _, epoch, directory = repair_case
    candidate, proof = sequence_blocks.block_candidates(instance, original, 1)[0]
    historical = dict(**stage_physics.input_digests(instance, candidate), physics_epoch=epoch, frames=[])
    monkeypatch.setattr(gates, 'gate_plan', lambda i, p, rules, **kw: dict(passed=False,
        checker=dict(count=apply_plan(i, p).count.model_dump(mode='json'))))
    assert recover_candidate.recover_candidate(instance, original, {'proof': proof}, historical,
                                              checkpoint_dir=directory) is None
