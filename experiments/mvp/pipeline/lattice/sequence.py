"""Labelled, deterministic adjacent-load repair; native release gates are unchanged.

Call from an admitted, single-owner process. Reused frames guide trials only;
the shared gate recomputes every native frame before releasing a repaired plan.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import os

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LABEL = 'Deterministic crane-sequence repair after Frontier Engine 0.13.0'


def normalized_world(state):
    raw = state.model_dump(mode='json')
    raw['ship']['placements'].sort(key=lambda p: (p['slot'], p['container_id']))
    raw['yard']['stacks'].sort(key=lambda s: s['key'])
    return raw


def adjacent_swap(instance, plan, step):
    """Commute a load past one independent load or ship-neutral yard relocate."""
    if not 1 <= step < len(plan.moves):
        return None
    left, right = plan.moves[step-1:step+1]
    if left.kind != 'load' or right.kind not in ('load', 'relocate'):
        return None
    state = initial_state(instance)
    try:
        for index, move in enumerate(plan.moves[:step-1]):
            state = apply_move(instance, state, move, move_index=index)
        right_first = apply_move(instance, state, right, move_index=step-1)
        if right.kind == 'relocate' and normalized_world(right_first)['ship'] != normalized_world(state)['ship']:
            return None
        original_end = apply_move(instance, apply_move(instance, state, left, move_index=step-1), right, move_index=step)
        swapped_end = apply_move(instance, right_first, left, move_index=step)
    except IllegalMoveError:
        return None
    old_world, new_world = normalized_world(original_end), normalized_world(swapped_end)
    if old_world != new_world:
        return None
    moves = list(plan.moves)
    moves[step-1:step+1] = [right, left]
    candidate = plan.model_copy(update=dict(id=plan.id+'.lattice-sequence', moves=moves,
                                           actions=None, action_space=None))
    if Counter(canonical_json(m.model_dump(mode='json')) for m in candidate.moves) != \
            Counter(canonical_json(m.model_dump(mode='json')) for m in plan.moves):
        return None
    return candidate, dict(changed_step=step, swapped_move_indices=[step-1, step],
        original_move_pair=[left.model_dump(mode='json'), right.model_dump(mode='json')],
        transformation='load/load swap' if right.kind == 'load' else 'load/ship-neutral yard-relocate swap',
        reached_world_sha256=sha256_json(old_world), orders_both_legal=True,
        normalized_reached_world_equal=True, unchanged_prefix_through=step-1,
        unchanged_states_from=step+1, departure_ballast_unchanged=True,
        move_multiset_unchanged=True, actions_cleared=plan.actions is not None)


def _bad(frame):
    return bool(frame.get('error') or frame.get('stability_passed') is not True
                or frame.get('strength_passed') is not True)


def _bound_frames(instance, plan, envelope, epoch):
    from app import stage_physics
    if isinstance(envelope, (str, Path)):
        try:
            envelope = json.loads(Path(envelope).read_text())
        except (OSError, ValueError):
            return None
    expected = stage_physics.input_digests(instance, plan)
    if not isinstance(envelope, dict) or envelope.get('physics_epoch') != epoch or \
            any(envelope.get(k) != v for k, v in expected.items()):
        return None
    frames = envelope.get('frames', [])
    if not stage_physics._frames_ok(frames, len(plan.moves)):
        return None
    return {frame['step']: deepcopy(frame) for frame in frames}


def repair_candidate(instance, plan, known_frames, physics_epoch, *, rules=(), base_plan=None,
                     checkpoint_dir=None, profile=None, max_attempts=8):
    """Return ``(MovePlan, full_gate)`` or None; never starts another planner.

    ``known_frames`` is the exact source-bound native frame checkpoint envelope,
    not a bare list. Caller owns fleet admission and the process's CPU slot.
    """
    from app import stage_physics
    from .batch import binding_fingerprint, write_json
    from .gates import _check, _clean, engine_identity, gate_plan, runtime_cache_scope
    from .runtime import discarded_plot_scope
    if stage_physics.physics_drift() or stage_physics.physics_epoch() != physics_epoch:
        return None
    original = plan
    frames = _bound_frames(instance, original, known_frames, physics_epoch)
    if frames is None:
        return None
    original_hash = sha256_json(original.model_dump(mode='json'))
    directory = Path(checkpoint_dir) if checkpoint_dir is not None else HERE/'sequence'/original_hash[:16]/str(os.getpid())
    identity, source = engine_identity(), binding_fingerprint()
    producer_source = sha256_file(__file__)
    if not identity['passed']:
        return None
    with runtime_cache_scope():
        replay = apply_plan(instance, original)
        if not replay.legal:
            return None
        original_report = _check(instance, original)
        _clean(instance, original, original_report)
        if not original_report.passed or original_report.count is None:
            return None
        original_count = original_report.count.model_dump(mode='json')
        metadata = dict(producer='lattice.sequence', label=LABEL, native_engine_output=False,
            original_plan_sha256=original_hash, original_plan=original.model_dump(mode='json'),
            original_engine_version=identity['engine_version'], original_profile=profile,
            source_physics_epoch=physics_epoch, binding_source_sha256=source,
            producer_source_sha256=producer_source,
            producer_source_path=str(Path(__file__).resolve().relative_to(ROOT)),
            original_move_count=original_count, original_checker=original_report.model_dump(mode='json'),
            attempts=[], final_release='shared gate_plan; fresh native all-frame sweep')
        write_json(directory/'original-source.json', metadata)
        current = original
        for attempt in range(1, max_attempts+1):
            failed = sorted(k for k, frame in frames.items() if _bad(frame))
            if not failed or any(k not in frames for k in range(failed[0]+1)):
                return None
            step = failed[0]
            attempt_path = directory/f'attempt-{attempt}.json'
            write_json(attempt_path, dict(status='trying_adjacent_load_swap', step=step))
            swapped = adjacent_swap(instance, current, step)
            if swapped is None:
                write_json(attempt_path, dict(status='no_legal_commuting_adjacent_load_pair', step=step))
                return None
            candidate, proof = swapped
            def native_frame(k):
                try:
                    with discarded_plot_scope():
                        return stage_physics.compute_frame(instance, candidate, k)
                except Exception as error:
                    return dict(step=k, stability_passed=None, strength_passed=None,
                                error=f'{type(error).__name__}: {error}')
            changed = native_frame(step)
            trial_frames = dict(frames)
            trial_frames[step] = changed
            def checkpoint_trial():
                trial = dict(**stage_physics.input_digests(instance, candidate), physics_epoch=physics_epoch,
                    frames=[trial_frames[k] for k in sorted(trial_frames)], reuse_proof=proof,
                    scope='trial only: reused unchanged states; final shared native all-frame gate required',
                    release_certificate=False)
                write_json(directory/f'attempt-{attempt}.trial-frames.json', trial)
            checkpoint_trial()
            record = dict(attempt=attempt, proof=proof, trial_changed_frame=changed,
                          candidate_plan_sha256=sha256_json(candidate.model_dump(mode='json')))
            metadata['attempts'].append(record)
            if _bad(changed):
                write_json(attempt_path, dict(record, status='native_trial_frame_rejected'))
                return None
            report = _check(instance, candidate)
            _clean(instance, candidate, report)
            checked_count = report.count.model_dump(mode='json') if report.count is not None else None
            record.update(checker=report.model_dump(mode='json'),
                          move_objective_unchanged=checked_count == original_count)
            if not report.passed or checked_count != original_count:
                write_json(attempt_path, dict(record, status='native_full_checker_rejected'))
                return None
            # Other reached states are identical by the commuting proof. Only
            # previously unmeasured tail frames need trial computation.
            for k in range(len(candidate.moves)+1):
                if k not in trial_frames:
                    trial_frames[k] = native_frame(k)
                    checkpoint_trial()
                if _bad(trial_frames[k]):
                    break
            if checked_count != original_count or binding_fingerprint() != source or \
                    stage_physics.physics_drift() or stage_physics.physics_epoch() != physics_epoch or engine_identity() != identity \
                    or sha256_file(__file__) != producer_source:
                return None
            if any(_bad(frame) for frame in trial_frames.values()):
                record['next_failed_step'] = min(k for k, value in trial_frames.items() if _bad(value))
                write_json(attempt_path, dict(record, status='next_native_trial_frame_rejected'))
                frames, current = trial_frames, candidate
                continue
            frame_path = directory/f'attempt-{attempt}.native-frames.json'
            audit = gate_plan(instance, candidate, rules, base_plan=base_plan, admit=False,
                              frame_checkpoint_path=frame_path)
            checked_count = (audit.get('checker') or {}).get('count')
            record.update(gate_passed=audit['passed'], native_frame_checkpoint=str(frame_path))
            write_json(attempt_path, dict(record, status='passed' if audit['passed'] else 'native_gate_rejected', gate=audit))
            if audit['passed'] and checked_count == original_count and binding_fingerprint() == source \
                    and not stage_physics.physics_drift() and stage_physics.physics_epoch() == physics_epoch \
                    and engine_identity() == identity and sha256_file(__file__) == producer_source:
                metadata.update(new_plan_sha256=sha256_json(candidate.model_dump(mode='json')),
                    repaired_utc=datetime.now(timezone.utc).isoformat(),
                    binding_source_after_sha256=binding_fingerprint(),
                    producer_source_after_sha256=sha256_file(__file__),
                    final_native_frame_checkpoint=str(frame_path),
                    final_gate_passed=True, move_objective_unchanged=True)
                audit['sequence_repair'] = metadata
                write_json(directory/'result.json', dict(plan=candidate.model_dump(mode='json'), gate=audit,
                                                       plan_producer='lattice.sequence', producer_label=LABEL))
                return candidate, audit
            return None
    return None


def main():
    import argparse
    import time
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from .batch import admit, binding_fingerprint, write_json
    from .acceptance import repair_context, require_repair_gate
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_row', type=Path)
    parser.add_argument('source_frames', type=Path)
    parser.add_argument('target', type=Path)
    options = parser.parse_args()
    source = json.loads(options.source_row.read_text())
    row = deepcopy(source)
    row.pop('gate', None)
    row.pop('gauge', None)
    row.update(passed=None, status='sequence_repair_running', pid=os.getpid(),
               plan_producer='lattice.sequence', producer_label=LABEL,
               binding_source_sha256=binding_fingerprint(),
               original_binding_plan=source['plan'],
               repair_source_origin=dict(path=str(options.source_row), sha256=sha256_file(options.source_row),
                                         original_computed_utc=source.get('computed_utc')))
    write_json(options.target, row)
    row['sequence_repair_admission'] = admit()
    write_json(options.target, row)
    began = time.monotonic()
    try:
        instance_path = Path(source.get('path', source.get('instance_path', '')))
        instance = load_instance(instance_path if instance_path.is_absolute() else ROOT/instance_path)
        original, rules, base = repair_context(source, instance)
        frames = json.loads(options.source_frames.read_text())
        result = repair_candidate(instance, original, frames,
            frames.get('physics_epoch'), checkpoint_dir=options.target.parent/(options.target.stem+'.repair'),
            profile=source.get('profile'), rules=rules, base_plan=base)
        if result is None:
            row.update(passed=False, status='sequence_repair_failed', plan=None)
        else:
            repaired, audit = result
            require_repair_gate(instance, repaired, rules, audit, base_plan=base)
            row.update(passed=True, status='checked_plan', plan=repaired.model_dump(mode='json'),
                canonical_rules=rules, instance=instance.model_dump(mode='json'),
                gate=audit, sequence_repair=audit['sequence_repair'], full_checker=audit['checker'],
                gauge=dict(passed=True, frames=len(repaired.moves)+1, expected_frames=len(repaired.moves)+1),
                gauge_frames=audit['sequence_repair']['final_native_frame_checkpoint'])
    except Exception as error:
        row.update(passed=False, status='sequence_repair_failed', plan=None,
                   error=f'{type(error).__name__}: {error}')
    row.update(sequence_repair_wall_s=time.monotonic()-began,
               binding_source_after_sha256=binding_fingerprint(),
               computed_utc=datetime.now(timezone.utc).isoformat())
    if row['binding_source_sha256'] != row['binding_source_after_sha256']:
        row.update(passed=False, status='rerun_required')
    write_json(options.target, row)
    print(json.dumps(dict(status=row['status'], passed=row['passed'], target=str(options.target))), flush=True)


if __name__ == '__main__':
    main()
