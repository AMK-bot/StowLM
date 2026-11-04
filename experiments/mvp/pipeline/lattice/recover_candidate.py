"""Recover one recorded permutation with entirely fresh current-epoch gates."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json
from stowage.moves import IllegalMoveError, apply_move, initial_state
from .sequence import normalized_world

HERE = Path(__file__).resolve().parent
LABEL = 'Recorded crane permutation, freshly checked under current physics'


def reconstruct(instance, original, recorded):
    """Replay the exact recorded block; there is no permutation search."""
    if not isinstance(recorded, dict):
        return None
    proof = recorded.get('proof', recorded)
    if not isinstance(proof, dict):
        return None
    start, end = proof.get('start_move_index'), proof.get('end_move_index')
    order = proof.get('permutation')
    if (type(start) is not int or type(end) is not int or not isinstance(order, list)
            or any(type(index) is not int for index in order)
            or not 0 <= start <= end < len(original.moves) or not 2 <= end-start+1 <= 8
            or sorted(order) != list(range(end-start+1))):
        return None
    block = original.moves[start:end+1]
    if any(move.kind not in ('load', 'relocate') for move in block) or \
            proof.get('original_block') != [move.model_dump(mode='json') for move in block]:
        return None
    prefix = initial_state(instance)
    try:
        for index, move in enumerate(original.moves[:start]):
            prefix = apply_move(instance, prefix, move, move_index=index)
        old_end, new_end = prefix, prefix
        for offset, move in enumerate(block):
            old_end = apply_move(instance, old_end, move, move_index=start+offset)
        for offset, index in enumerate(order):
            move = block[index]
            reached = apply_move(instance, new_end, move, move_index=start+offset)
            if move.kind == 'relocate' and normalized_world(reached)['ship'] != normalized_world(new_end)['ship']:
                return None
            new_end = reached
    except IllegalMoveError:
        return None
    world = normalized_world(new_end)
    if world != normalized_world(old_end) or sha256_json(world) != proof.get('reached_world_sha256'):
        return None
    moves = list(original.moves)
    moves[start:end+1] = [block[index] for index in order]
    if Counter(canonical_json(move.model_dump(mode='json')) for move in moves) != \
            Counter(canonical_json(move.model_dump(mode='json')) for move in original.moves):
        return None
    return original.model_copy(update=dict(id=original.id+'.lattice-block-sequence', moves=moves,
                                           actions=None, action_space=None))


def recover_candidate(instance, original, recorded_proof, historical_trial, *, rules=(),
                      base_plan=None, checkpoint_dir=None, profile=None):
    from app import stage_physics
    from .batch import binding_fingerprint, write_json
    from .gates import _check, _clean, engine_identity, gate_plan, runtime_cache_scope
    candidate = reconstruct(instance, original, recorded_proof)
    if candidate is None:
        return None
    expected = stage_physics.input_digests(instance, candidate)
    if any(historical_trial.get(key) != value for key, value in expected.items()):
        return None
    # No historical frame or epoch is consulted for a native verdict.
    epoch = stage_physics.physics_epoch()
    if stage_physics.physics_drift():
        return None
    identity, source = engine_identity(), binding_fingerprint()
    producer_source = sha256_file(__file__)
    core_source = sha256_file(HERE/'sequence.py')
    if not identity['passed']:
        return None
    directory = Path(checkpoint_dir) if checkpoint_dir is not None else HERE/'candidate-recovery'/expected['plan_sha256'][:16]
    metadata = dict(producer='lattice.recover_candidate', label=LABEL, native_engine_output=False,
        original_plan=original.model_dump(mode='json'), original_plan_sha256=sha256_json(original.model_dump(mode='json')),
        new_plan_sha256=expected['plan_sha256'], original_engine_version=identity['engine_version'],
        original_profile=profile, recorded_permutation_proof=recorded_proof,
        recorded_proof_sha256=sha256_json(recorded_proof), producer_source_sha256=producer_source,
        sequence_core_source_sha256=core_source, binding_source_sha256=source,
        historical_physics_epoch=historical_trial.get('physics_epoch'), fresh_physics_epoch=epoch,
        historical_physics_policy='Historical guidance only; every current frame recomputed by strict shared gate.',
        no_engine_search=True)
    write_json(directory/'recovery-basis.json', metadata)
    with runtime_cache_scope():
        before = _check(instance, original)
        _clean(instance, original, before)
        after = _check(instance, candidate)
        _clean(instance, candidate, after)
        old_count = before.count.model_dump(mode='json') if before.count is not None else None
        new_count = after.count.model_dump(mode='json') if after.count is not None else None
        write_json(directory/'fresh-checkers.json', dict(original=before.model_dump(mode='json'),
                   candidate=after.model_dump(mode='json'), move_objective_unchanged=old_count == new_count))
        if not before.passed or not after.passed or old_count is None or old_count != new_count:
            return None
        frame_path = directory/'current.native-frames.json'
        audit = gate_plan(instance, candidate, rules, base_plan=base_plan, admit=False,
                          frame_checkpoint_path=frame_path)
    write_json(directory/'current-gate.json', audit)
    if (not audit['passed'] or (audit.get('checker') or {}).get('count') != old_count
            or stage_physics.physics_drift() or stage_physics.physics_epoch() != epoch
            or engine_identity() != identity or binding_fingerprint() != source
            or sha256_file(__file__) != producer_source or sha256_file(HERE/'sequence.py') != core_source):
        return None
    metadata.update(binding_source_after_sha256=source, producer_source_after_sha256=producer_source,
        final_native_frame_checkpoint=str(frame_path), move_objective_unchanged=True,
        repaired_utc=datetime.now(timezone.utc).isoformat(), final_release='shared gate_plan; fresh native all-frame sweep')
    audit['sequence_repair'] = metadata
    write_json(directory/'result.json', dict(plan=candidate.model_dump(mode='json'), gate=audit,
                                           plan_producer='lattice.recover_candidate', producer_label=LABEL))
    return candidate, audit


def main():
    import argparse
    import os
    import time
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from .batch import admit, binding_fingerprint, write_json
    from .acceptance import repair_context, require_repair_gate
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source_row', 'proof', 'historical_trial', 'target'):
        parser.add_argument(name, type=Path)
    options = parser.parse_args()
    source = json.loads(options.source_row.read_text())
    row = deepcopy(source)
    row.pop('gate', None)
    row.pop('gauge', None)
    row.update(passed=None, status='sequence_repair_running', pid=os.getpid(),
        plan_producer='lattice.recover_candidate', producer_label=LABEL,
        binding_source_sha256=binding_fingerprint(), original_binding_plan=source['plan'],
        repair_source_origin=dict(path=str(options.source_row), sha256=sha256_file(options.source_row),
            proof_path=str(options.proof), proof_sha256=sha256_file(options.proof),
            historical_trial_path=str(options.historical_trial), historical_trial_sha256=sha256_file(options.historical_trial)))
    write_json(options.target, row)
    row['sequence_repair_admission'] = admit()
    write_json(options.target, row)
    began = time.monotonic()
    try:
        root = HERE.parents[3]
        path = Path(source.get('path', source.get('instance_path', '')))
        instance = load_instance(path if path.is_absolute() else root/path)
        original, rules, base = repair_context(source, instance)
        result = recover_candidate(instance, original,
            json.loads(options.proof.read_text()), json.loads(options.historical_trial.read_text()),
            checkpoint_dir=options.target.parent/(options.target.stem+'.recovery'), profile=source.get('profile'),
            rules=rules, base_plan=base)
        if result is None:
            row.update(passed=False, status='sequence_repair_failed', plan=None)
        else:
            candidate, audit = result
            require_repair_gate(instance, candidate, rules, audit, base_plan=base)
            row.update(passed=True, status='checked_plan', plan=candidate.model_dump(mode='json'),
                canonical_rules=rules, instance=instance.model_dump(mode='json'),
                gate=audit, sequence_repair=audit['sequence_repair'], full_checker=audit['checker'],
                gauge=dict(passed=True, frames=len(candidate.moves)+1, expected_frames=len(candidate.moves)+1),
                gauge_frames=audit['sequence_repair']['final_native_frame_checkpoint'])
    except Exception as error:
        row.update(passed=False, status='sequence_repair_failed', plan=None,
                   error=f'{type(error).__name__}: {error}')
    row.update(sequence_repair_wall_s=time.monotonic()-began,
        binding_source_after_sha256=binding_fingerprint(), computed_utc=datetime.now(timezone.utc).isoformat())
    if row['binding_source_sha256'] != row['binding_source_after_sha256']:
        row.update(passed=False, status='rerun_required')
    write_json(options.target, row)
    print(json.dumps(dict(status=row['status'], passed=row['passed'], target=str(options.target))), flush=True)


if __name__ == '__main__':
    main()
