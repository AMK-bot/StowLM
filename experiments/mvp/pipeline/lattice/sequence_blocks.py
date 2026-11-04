"""Bounded crane-sequence postprocessing; never changes stowage or adds lifts."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import shutil
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json
from stowage.conventions import cells_of_slot
from stowage.moves import IllegalMoveError, apply_move, initial_state
from .sequence import _bad, _bound_frames, normalized_world

HERE = Path(__file__).resolve().parent
LABEL = 'Deterministic bounded legal crane permutation including hatch covers after Frontier Engine 0.13.0'
BLOCK_KINDS = frozenset(('load', 'relocate', 'hatch_cover_off', 'hatch_cover_on'))
DIVERSITY_POLICY = 'Best public-legal representative per exact load/cover event history at each depth; ship-neutral yard permutations share one beam slot.'


def cargo_moment(instance, state):
    """Cargo-only ranking proxy; native stability decides every acceptance."""
    boxes = {box.id: box for box in instance.containers}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    return sum(boxes[p.container_id].gross_mass_kg *
        sum(cells[cell].tcg_m for cell in cells_of_slot(p.slot))/len(cells_of_slot(p.slot))
        for p in state.ship.placements)


def block_candidates(instance, plan, step, *, window=32, lookback=4, beam_width=4, max_candidates=12):
    """Enumerate a bounded deterministic beam of completely legal permutations."""
    if not 1 <= window <= 32 or not 0 <= lookback <= 4 or not 1 <= beam_width <= 4 or not 1 <= max_candidates <= 12:
        raise ValueError('block bounds are window1–32, lookback0–4, beam1–4, candidates1–12')
    if not 1 <= step <= len(plan.moves):
        return []
    start = max(0, step-1-lookback)
    if not 0 <= start < len(plan.moves) or plan.moves[start].kind not in BLOCK_KINDS:
        return []
    block = []
    for move in plan.moves[start:start+window]:
        if move.kind not in BLOCK_KINDS:
            break
        block.append(move)
    if len(block) < 2:
        return []
    state = initial_state(instance)
    try:
        for index, move in enumerate(plan.moves[:start]):
            state = apply_move(instance, state, move, move_index=index)
        original_end = state
        for index, move in enumerate(block, start):
            original_end = apply_move(instance, original_end, move, move_index=index)
    except IllegalMoveError:
        return []
    beam = [((), state, 0., 0., ())]
    for depth in range(len(block)):
        expanded = []
        for order, current, peak, total, physical_history in beam:
            for index, move in enumerate(block):
                if index in order:
                    continue
                try:
                    reached = apply_move(instance, current, move, move_index=start+depth)
                except IllegalMoveError:
                    continue
                if move.kind == 'relocate' and normalized_world(reached)['ship'] != normalized_world(current)['ship']:
                    continue
                moment = abs(cargo_moment(instance, reached))
                history = physical_history if move.kind == 'relocate' else \
                    physical_history+(canonical_json(move.model_dump(mode='json')),)
                expanded.append((order+(index,), reached, max(peak, moment), total+moment, history))
        expanded.sort(key=lambda row: (row[2], row[3], row[0]))
        seen, beam = set(), []
        for item in expanded:
            if item[4] not in seen:
                seen.add(item[4])
                beam.append(item)
                if len(beam) == beam_width:
                    break
    output = []
    for order, reached, peak, total, physical_history in beam:
        if order == tuple(range(len(block))) or normalized_world(reached) != normalized_world(original_end):
            continue
        moves = list(plan.moves)
        moves[start:start+len(block)] = [block[index] for index in order]
        if Counter(canonical_json(m.model_dump(mode='json')) for m in moves) != \
                Counter(canonical_json(m.model_dump(mode='json')) for m in plan.moves):
            continue
        candidate = plan.model_copy(update=dict(id=plan.id+'.lattice-block-sequence', moves=moves,
                                               actions=None, action_space=None))
        output.append((candidate, dict(start_move_index=start, end_move_index=start+len(block)-1,
            first_failed_step=step, lookback=lookback, window=window, block_move_count=len(block),
            original_block=[m.model_dump(mode='json') for m in block], permutation=list(order),
            every_move_public_replay_legal=True, all_yard_relocations_ship_neutral=True,
            allowed_move_kinds=sorted(BLOCK_KINDS), hatch_cover_state_in_world_equality=True,
            diversity_policy=DIVERSITY_POLICY,
            candidate_physical_trajectory_sha256=sha256_json([json.loads(event) for event in physical_history]),
            physical_event_count=len(physical_history),
            normalized_reached_world_equal=True, reached_world_sha256=sha256_json(normalized_world(reached)),
            move_multiset_unchanged=True, unchanged_states_from=start+len(block),
            changed_steps=list(range(start+1, start+len(block))),
            peak_abs_cargo_moment_proxy_kgm=peak, sum_abs_cargo_moment_proxy_kgm=total,
            ranking_scope='cargo-only transverse moment; native stability decides acceptance')))
    return output[:max_candidates]


def repair_candidate(instance, plan, known_frames, physics_epoch, *, rules=(), base_plan=None,
                     checkpoint_dir=None, profile=None, window=32, lookback=4, beam_width=4,
                     max_candidates=12, budget_s=180., resume=False, adaptive_max_window=None):
    """Compose bounded trial blocks; only a fresh full shared gate can release."""
    from app import stage_physics
    from .batch import binding_fingerprint, write_json
    from .gates import _check, _clean, engine_identity, gate_plan, runtime_cache_scope
    from .runtime import discarded_plot_scope
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError('trial budget must be finite and positive')
    # None distinguishes omitted policy from an explicitly authorized resume cap.
    adaptive_cap = (window if resume else 32) if adaptive_max_window is None else adaptive_max_window
    if not 1 <= window <= adaptive_cap <= 32:
        raise ValueError('initial window must fit adaptive cap1–32')
    if stage_physics.physics_drift() or stage_physics.physics_epoch() != physics_epoch:
        return None
    frames = _bound_frames(instance, plan, known_frames, physics_epoch)
    if frames is None:
        return None
    failed = sorted(k for k, frame in frames.items() if _bad(frame))
    if not failed or any(k not in frames for k in range(failed[0]+1)):
        return None
    identity, source = engine_identity(), binding_fingerprint()
    producer = sha256_file(__file__)
    core_source = sha256_file(HERE/'sequence.py')
    if not identity['passed']:
        return None
    directory = Path(checkpoint_dir) if checkpoint_dir is not None else HERE/'block-sequence'/sha256_json(plan.model_dump(mode='json'))[:16]
    resume_provenance = None
    if resume and directory.exists():
        previous = directory/'original-source.json'
        old = json.loads(previous.read_text()) if previous.exists() else {}
        archive = directory/'resume-history'/str(time.time_ns())
        archive.mkdir(parents=True)
        hashes = {}
        for path in directory.glob('*.json'):
            shutil.copy2(path, archive/path.name)
            hashes[path.name] = sha256_file(path)
        resume_provenance = dict(archive=str(archive), sha256=hashes,
            previous_bounds=old.get('bounds'), previous_producer_source_sha256=old.get('producer_source_sha256'),
            policy='Exact candidate instance/plan/physics epoch required; trial guidance only; final gate stays fresh.')
    began = time.monotonic()
    with runtime_cache_scope():
        original_report = _check(instance, plan)
        _clean(instance, plan, original_report)
        if not original_report.passed or original_report.count is None:
            return None
        original_count = original_report.count.model_dump(mode='json')
        metadata = dict(producer='lattice.sequence_blocks', label=LABEL, native_engine_output=False,
            original_plan=plan.model_dump(mode='json'), original_plan_sha256=sha256_json(plan.model_dump(mode='json')),
            original_engine_version=identity['engine_version'], original_profile=profile,
            producer_source_sha256=producer, sequence_core_source_sha256=core_source,
            diversity_policy=DIVERSITY_POLICY,
            binding_source_sha256=source, source_physics_epoch=physics_epoch,
            bounds=dict(window=window, lookback=lookback, beam_width=beam_width, max_candidates=max_candidates,
                        adaptive_max_window=adaptive_cap, requested_initial_window=window,
                        resume_adaptive_cap_explicit=adaptive_max_window is not None,
                        max_blocks=8, budget_s=budget_s), attempts=[], trial_only_blocks=[], width_stages=[])
        if resume_provenance:
            metadata['resume_provenance'] = resume_provenance
        metadata['budget_scope'] = 'Trial wall allowance; atomic native calls and final strict release can overrun.'
        write_json(directory/'original-source.json', metadata)
        def stable():
            return (not stage_physics.physics_drift() and stage_physics.physics_epoch() == physics_epoch
                    and binding_fingerprint() == source and engine_identity() == identity
                    and sha256_file(__file__) == producer and sha256_file(HERE/'sequence.py') == core_source)
        current, used, current_window, block_index = plan, 0, window, 1
        while block_index <= 8:
            if used >= max_candidates or time.monotonic()-began >= budget_s or not stable():
                break
            failed_step = min(k for k, value in frames.items() if _bad(value))
            committed = False
            candidates = block_candidates(instance, current, failed_step,
                window=current_window, lookback=lookback, beam_width=beam_width, max_candidates=max_candidates-used)
            metadata['width_stages'].append(dict(block=block_index, window=current_window,
                input_plan_sha256=sha256_json(current.model_dump(mode='json')), global_candidates_before=used))
            inside_rejections = 0
            for candidate, proof in candidates:
                if used >= max_candidates or time.monotonic()-began >= budget_s:
                    break
                used += 1
                path = directory/f'candidate-{used}.json'
                record = dict(candidate=used, block=block_index, proof=proof, status='checking_native_trial',
                    input_plan_sha256=sha256_json(current.model_dump(mode='json')),
                    candidate_plan_sha256=sha256_json(candidate.model_dump(mode='json')))
                metadata['attempts'].append(record)
                write_json(path, record)
                report = _check(instance, candidate)
                _clean(instance, candidate, report)
                if not report.passed or report.count is None or report.count.model_dump(mode='json') != original_count:
                    record['status'] = 'native_checker_or_objective_rejected'
                    write_json(path, record)
                    continue
                trial = {k: deepcopy(value) for k, value in frames.items() if k not in proof['changed_steps']}
                trial_path = directory/f'candidate-{used}.trial-frames.json'
                if resume and trial_path.exists():
                    cached = _bound_frames(instance, candidate, trial_path, physics_epoch)
                    record['resume_checkpoint_sha256'] = sha256_file(trial_path)
                    record['resume_checkpoint_accepted'] = cached is not None
                    if cached is not None:
                        trial.update(cached)
                        record['resumed_trial_steps'] = sorted(cached)
                for step in range(len(candidate.moves)+1):
                    if step not in trial:
                        if time.monotonic()-began >= budget_s:
                            record['status'] = 'trial_budget_failed'
                            write_json(path, record)
                            write_json(directory/'original-source.json', metadata)
                            return None
                        with discarded_plot_scope():
                            trial[step] = stage_physics.compute_frame(instance, candidate, step)
                        write_json(trial_path, dict(**stage_physics.input_digests(instance, candidate),
                            physics_epoch=physics_epoch, frames=[trial[k] for k in sorted(trial)], reuse_proof=proof,
                            preceding_trial_blocks=metadata['trial_only_blocks'],
                            scope='trial only; fresh native all-frame gate required', release_certificate=False))
                    if _bad(trial[step]):
                        break
                bad = sorted(k for k, value in trial.items() if _bad(value))
                record.update(trial_failed_steps=bad, move_objective_unchanged=True)
                if bad:
                    end = proof['unchanged_states_from']
                    if min(bad) > end and all(k in trial and not _bad(trial[k]) for k in range(end+1)) and stable():
                        envelope = dict(**stage_physics.input_digests(instance, candidate), physics_epoch=physics_epoch,
                            frames=[trial[k] for k in sorted(trial)], release_certificate=False,
                            scope='composed trial guidance only; fresh all-frame release required')
                        rebound = _bound_frames(instance, candidate, envelope, physics_epoch)
                        if rebound is None:
                            return None
                        record['status'] = 'committed_trial_only_next_failure_invariant'
                        record['next_failed_step'] = min(bad)
                        metadata['trial_only_blocks'].append(dict(record))
                        current, frames, committed = candidate, rebound, True
                        write_json(directory/f'block-{block_index}.trial-frames.json', envelope)
                        write_json(path, record)
                        write_json(directory/'original-source.json', metadata)
                        break
                    record['status'] = 'native_trial_frame_rejected'
                    write_json(path, record)
                    inside_rejections += 1
                    continue
                if not stable():
                    return None
                frame_path = directory/f'candidate-{used}.native-frames.json'
                audit = gate_plan(instance, candidate, rules, base_plan=base_plan, admit=False, frame_checkpoint_path=frame_path)
                record['status'] = 'passed' if audit['passed'] else 'native_gate_rejected'
                write_json(path, dict(record, gate=audit))
                if audit['passed'] and (audit.get('checker') or {}).get('count') == original_count and stable():
                    metadata.update(new_plan_sha256=sha256_json(candidate.model_dump(mode='json')),
                        repaired_utc=datetime.now(timezone.utc).isoformat(), binding_source_after_sha256=source,
                        final_native_frame_checkpoint=str(frame_path), move_objective_unchanged=True,
                        total_candidates=used, composed_blocks=block_index,
                        final_release='shared gate_plan; fresh native all-frame sweep')
                    audit['sequence_repair'] = metadata
                    write_json(directory/'result.json', dict(plan=candidate.model_dump(mode='json'), gate=audit,
                        plan_producer='lattice.sequence_blocks', producer_label=LABEL))
                    return candidate, audit
                return None
            if committed:
                block_index += 1
                continue
            if (candidates and inside_rejections == len(candidates) and current_window < adaptive_cap
                    and used < max_candidates and time.monotonic()-began < budget_s
                    and any(proof['block_move_count'] == current_window and proof['end_move_index'] < len(current.moves)-1
                            for _,proof in candidates)):
                current_window = min(adaptive_cap, current_window*2)
                write_json(directory/'original-source.json', metadata)
                continue
            break
        metadata.update(total_candidates=used, status='bounded_trial_exhausted_not_impossible')
        write_json(directory/'original-source.json', metadata)
    return None


def main():
    import argparse
    import os
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from .batch import admit, binding_fingerprint, write_json
    from .acceptance import repair_context, require_repair_gate
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_row', type=Path)
    parser.add_argument('source_frames', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--trial-budget-s', type=float, default=180.)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--checkpoint-dir', type=Path)
    options = parser.parse_args()
    source = json.loads(options.source_row.read_text())
    row = deepcopy(source)
    row.pop('gate', None)
    row.pop('gauge', None)
    row.update(passed=None, status='sequence_repair_running', pid=os.getpid(),
        plan_producer='lattice.sequence_blocks', producer_label=LABEL,
        binding_source_sha256=binding_fingerprint(), original_binding_plan=source['plan'],
        repair_source_origin=dict(path=str(options.source_row), sha256=sha256_file(options.source_row),
                                  original_computed_utc=source.get('computed_utc')))
    write_json(options.target, row)
    row['sequence_repair_admission'] = admit()
    write_json(options.target, row)
    began = time.monotonic()
    try:
        root = HERE.parents[3]
        path = Path(source.get('path', source.get('instance_path', '')))
        instance = load_instance(path if path.is_absolute() else root/path)
        original, rules, base = repair_context(source, instance)
        frames = json.loads(options.source_frames.read_text())
        result = repair_candidate(instance, original, frames,
            frames.get('physics_epoch'), checkpoint_dir=options.checkpoint_dir or options.target.parent/(options.target.stem+'.repair'),
            profile=source.get('profile'), budget_s=options.trial_budget_s, resume=options.resume,
            rules=rules, base_plan=base)
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
        binding_source_after_sha256=binding_fingerprint(), computed_utc=datetime.now(timezone.utc).isoformat())
    if row['binding_source_sha256'] != row['binding_source_after_sha256']:
        row.update(passed=False, status='rerun_required')
    write_json(options.target, row)
    print(json.dumps(dict(status=row['status'], passed=row['passed'], target=str(options.target))), flush=True)


if __name__ == '__main__':
    main()
