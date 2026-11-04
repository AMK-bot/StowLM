"""Resumable local batch. Each request gets an independent owned process cap."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import tempfile
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .binding_sources import SOURCE_SCOPE, runtime_fingerprint, runtime_source_files

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
STATE = ROOT / 'experiments/fleet/core-state.json'
BINDING_SOURCE_SCOPE = SOURCE_SCOPE
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS')
for _name in THREADS:
    os.environ[_name] = '1'


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def capacity_reading(state_path=STATE, *, now=None):
    """Invalid telemetry is unavailable; callers wait without throwing."""
    try:
        row = json.loads(Path(state_path).read_text())
        mask = int(row['mask'], 0) if isinstance(row['mask'], str) else int(row['mask'])
        load = float(row['load1'])
        age = (time.time() if now is None else now) - float(row['t'])
        if mask >= 0 and mask.bit_count() >= 8 and 0 <= load <= 45 and 0 <= age < 120:
            return dict(mask=mask, enabled_cores=mask.bit_count(), load1=load, reading_age_s=age)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError):
        pass
    return None


def admit(state_path=STATE, *, poll_s=2., sleep=time.sleep):
    began = time.monotonic()
    while (row := capacity_reading(state_path)) is None:
        sleep(poll_s)
    return dict(row, waited_s=time.monotonic()-began)


def refusal_status(evidence):
    """A budget or unavailable allocator is never an impossibility proof."""
    reason = evidence.get('reason', '')
    proof = (('needs ' in reason and 'remain open' in reason)
             or reason.startswith('no instruction-allowed physical footprint:')
             or 'allocation: INFEASIBLE' in reason
             or 'retained' in reason.lower() and 'conflict' in reason.lower())
    return 'proved_refusal' if proof else 'budget_failed'


def release_failure_status(error):
    message = str(error).lower()
    kind = type(error).__name__.lower()
    drift = ('epochdrift' in kind or 'physics sources changed' in message
             or 'checker sources changed' in message or 'source drift' in message
             or 'physics source changed' in message or 'checker source changed' in message
             or 'pinned engine sources do not match' in message
             or 'epoch' in message and ('changed' in message or 'drift' in message))
    return 'rerun_required' if drift else 'error'


def binding_source_files():
    return runtime_source_files(HERE.parent/'binding')


def binding_fingerprint():
    return runtime_fingerprint(HERE.parent/'binding')


def refresh_progress(run_dir):
    from .report import update
    return update(run_dir=run_dir, output_dir=Path(run_dir).parent)


def campaign_status(manifest, *, control_path=None):
    """Missing control preserves standalone use; an active campaign is exact."""
    path = Path(control_path) if control_path is not None else HERE/'campaign-control.json'
    try:
        control = json.loads(path.read_text())
        active = control['active_campaign']
        if not isinstance(active, str) or not active:
            raise ValueError('active_campaign must be a nonempty string')
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return dict(status='campaign_control_unavailable', campaign_id=manifest.get('campaign_id'))
    if manifest.get('campaign_id') != active:
        return dict(status='superseded', campaign_id=manifest.get('campaign_id'), active_campaign=active)
    return None


def save_hero_replan(row, *, fixtures_dir=None):
    """Keep the first checked DG replan as a standalone demo handoff."""
    rules, gate = row.get('canonical_rules', []), row.get('gate', {})
    moved = gate.get('boxes_moved', len(gate.get('what_moved') or []))
    if (row.get('status') != 'checked_plan' or not gate.get('passed') or
            not gate.get('binding') or not moved or moved <= 0 or len(rules) != 1 or
            rules[0]['constraint']['type'] != 'dg_keep_away'):
        return False
    directory = Path(fixtures_dir) if fixtures_dir is not None else HERE/'fixtures'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/'hero-replan.json'
    with (directory/'.hero-replan.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = None
        if previous and previous.get('binding_source_sha256') == row.get('binding_source_sha256'):
            return False
        write_json(path, row)
        reference = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        summary = dict(sentences=row['sentences'], canonical_rules=rules,
                       lifts_before=gate.get('base_lifts'), lifts_after=gate.get('lifts'),
                       boxes_moved=moved, computed_utc=row['computed_utc'], path=reference,
                       instance_path=row['instance_path'], instance_sha256=row['instance_sha256'],
                       plan_sha256=sha256_json(row['plan']), entry_sha256=sha256_file(path),
                       binding_source_sha256=row['binding_source_sha256'],
                       engine_manifest_sha256=row['engine_manifest_sha256'], mode='stored')
        write_json(directory/'hero-instruction.json', summary)
    return True


def _binding_call(instance, answers, *, admission, **kwargs):
    from importlib import import_module
    from unittest.mock import patch
    from .runtime import isolated_binding
    from experiments.mvp.pipeline.binding import plan_with_rules
    with isolated_binding(), ExitStack() as stack:
        # Parent admitted before starting the hard engine clock. Reuse its
        # actual reading at binding's two inner admission sites.
        for module in ('adapter', 'allocation'):
            stack.enter_context(patch.object(import_module('experiments.mvp.pipeline.binding.'+module),
                'wait_for_capacity', lambda: dict(admission, lattice_pre_admitted=True)))
        return plan_with_rules(instance, answers, **kwargs)


def reusable_candidate(row, *, instance, canonical, key, profile, identity, version):
    """Reuse engine work only; this never supplies a release verdict or physics."""
    from stowage.model import MovePlan
    from .store import _binding_source_matches, binding_compatibility, canonical_rule_set
    try:
        if (row.get('status') != 'rerun_required' or not row.get('rerun_required')
                or row.get('reason') != 'Binding source changed during computation or release gates; rerun before storing.'
                or row.get('release_error') or row.get('purpose') == 'off_lattice'
                or any(r['constraint']['type'] == 'one_port_per_hatch' for r in canonical)
                or row.get('profile') != profile or profile['name'] != 'standard'
                or not identity.get('passed')
                or row.get('engine_version') != version
                or row.get('engine_manifest_sha256') != identity['manifest_sha256']
                or row.get('key') != key or row.get('canonical_rules') != canonical
                or canonical_rule_set(row['rules']) != canonical):
            return None
        content = instance.model_dump(mode='json')
        if row.get('instance') != content or row.get('instance_sha256') != sha256_json(content):
            return None
        datetime.fromisoformat(row['computed_utc'])
        if any(not isinstance(row.get(k), (int, float)) or isinstance(row[k], bool)
               or not math.isfinite(row[k]) or row[k] < 0 for k in ('engine_wall_s', 'wall_s')):
            return None
        evidence = row['binding_evidence']
        if not (evidence.get('passed') or evidence.get('recovered_incumbent')):
            return None
        original = row.get('original_binding_plan')
        if not original and row.get('plan_producer'):
            return None
        candidate = original or row['plan']
        plan = MovePlan.model_validate(candidate)
        if plan.model_dump(mode='json') != candidate or plan.instance_id != content['id']:
            return None
        if row.get('gate', {}).get('plan_sha256') != sha256_json(row['plan']):
            return None
        if row.get('plan_producer'):
            if (row['plan_producer'] != 'lattice.sequence'
                    or row.get('sequence_repair', {}).get('original_plan_sha256') != sha256_json(candidate)):
                return None
        elif row['plan'] != candidate:
            return None
        if not _binding_source_matches(row):
            return None
        compatibility = binding_compatibility(row)
        if row['binding_source_sha256'] != binding_fingerprint() and not compatibility:
            return None
        return plan, compatibility
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def compute(job):
    """Called in an outer process: wait, solve, gate, checkpoint, release slot."""
    if 'campaign_id' in job and (blocked := campaign_status(job, control_path=job.get('campaign_control_path'))):
        print(json.dumps(blocked), flush=True)
        return blocked
    if job.get('spec', {}).get('standard_plan_pool_donor_path'):
        from .standard_plan_pool import compute as compute_pool
        return compute_pool(job)
    source_files = binding_source_files()
    source_digest = binding_fingerprint()
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from experiments.mvp.pipeline.runner.profiles import get_profile, engine_kwargs
    from experiments.mvp.pipeline.runner.limits import call_with_limit, StageTimeout, StageCallError, StageProcessError
    from experiments.mvp.pipeline.binding.adapter import verify_manifest, VERSION
    from .gates import gate_plan
    from .store import _binding_source_matches, canonical_rule_set, entry_key, save_entry

    spec = job['spec']
    instance = load_instance(job['instance_path'])
    digest = sha256_json(instance.model_dump(mode='json'))
    canonical = canonical_rule_set(spec['rules'])
    key = entry_key(digest, canonical)
    run_dir = Path(job.get('run_dir', HERE/'runs'))
    target = run_dir / (key+'.json')
    cached, archived = None, None
    if target.exists() and not job.get('retry'):
        raw_checkpoint = target.read_bytes()
        cached = json.loads(raw_checkpoint)
        if (cached.get('status') in ('checked_plan', 'checked_live', 'proved_refusal')
                and not cached.get('rerun_required') and _binding_source_matches(cached)):
            save_hero_replan(cached, fixtures_dir=job.get('fixtures_dir'))
            refresh_progress(run_dir)
            return {k: cached.get(k) for k in ('id', 'key', 'status', 'lifts', 'wall_s')}
        archive = run_dir/'archive'
        archive.mkdir(parents=True, exist_ok=True)
        archived = archive/(key+'.'+str(time.time_ns())+'.json')
        with archived.open('xb') as stream:
            stream.write(raw_checkpoint)
            stream.flush()
            os.fsync(stream.fileno())
    # Admission happens before the900s engine cap begins.
    admission = admit(job.get('state_path', STATE))
    if 'campaign_id' in job and (blocked := campaign_status(job, control_path=job.get('campaign_control_path'))):
        print(json.dumps(blocked), flush=True)
        return blocked
    answers = [validate_answer(json.dumps(r), context_from_instance(instance)) for r in canonical]
    name = 'live' if spec['purpose'] == 'off_lattice' else 'standard'
    profile = get_profile(name)
    profile_record = {k: asdict(v) if k == 'config' else v for k, v in profile.items()}
    identity = verify_manifest()
    reuse = reusable_candidate(cached, instance=instance, canonical=canonical, key=key,
        profile=profile_record, identity=identity, version=VERSION) if cached else None
    row = dict(spec, key=key, instance_path=str(job['instance_path']), instance_sha256=digest,
               campaign_id=job.get('campaign_id'),
               canonical_rules=canonical, computed_utc=datetime.now(timezone.utc).isoformat(),
               engine_version=VERSION, engine_manifest_sha256=identity['manifest_sha256'],
               binding_source_sha256=source_digest,
               binding_source_scope=BINDING_SOURCE_SCOPE, binding_source_files=list(source_files),
               profile=profile_record, admission=admission, process_pid=os.getpid(),
               mode='live' if name == 'live' else 'stored', measurement='measured', status='running')
    if reuse is not None:
        plan, compatibility = reuse
        row = dict(cached)
        for field in ('gate', 'plan', 'sequence_repair', 'plan_producer', 'producer_label',
                      'reason', 'rerun_required', 'worker_released', 'release_utc', 'release_error'):
            row.pop(field, None)
        row.update(status='running', process_pid=os.getpid(), release_admission=admission,
                   release_started_utc=datetime.now(timezone.utc).isoformat(),
                   planning_origin_binding_source_sha256=cached.get('planning_origin_binding_source_sha256',
                                                                          cached['binding_source_sha256']),
                   release_binding_source_sha256=source_digest,
                   engine_candidate_reuse=dict(archive_path=str(archived), archive_sha256=sha256_file(archived),
                       original_plan_sha256=sha256_json(plan.model_dump(mode='json')),
                       original_computed_utc=cached['computed_utc'],
                       historical_physics_reused=False, full_fresh_gate_required=True))
        if compatibility:
            row['binding_compatibility'] = compatibility
    write_json(target.with_suffix('.running.json'), row)
    began = time.monotonic()
    incumbent = None
    def observed(event):
        nonlocal incumbent
        incumbent = event
        write_json(target.with_suffix('.incumbent.json'), dict(row, event=event,
                   elapsed_s=time.monotonic()-began))
    if reuse is not None:
        evidence = cached['binding_evidence']
    else:
        try:
            result, evidence = call_with_limit(_binding_call, instance, answers,
                timeout_s=profile['engine_timeout_s'], stage='engine', on_event=observed,
                event_kw='on_incumbent', admission=admission, **engine_kwargs(profile))
            plan = result.plan if result is not None else None
        except (StageTimeout, StageCallError, StageProcessError) as error:
            plan = MovePlan.model_validate(incumbent['plan']) if incumbent and incumbent.get('plan') else None
            evidence = dict(status='no_checked_plan', passed=False, reason=str(error),
                            timed_out=isinstance(error, StageTimeout), recovered_incumbent=plan is not None,
                            engine_version=VERSION, engine_manifest_sha256=identity['manifest_sha256'])
        row['engine_wall_s'] = time.monotonic()-began
    row['binding_evidence'] = evidence
    if reuse is None:
        row['wall_s'] = row['engine_wall_s']
    row.update(status='release_gate_pending' if plan is not None else 'post_engine_pending',
               release_stage='gate_admission_pending' if plan is not None else 'post_engine_screen')
    if plan is not None:
        row['original_binding_plan'] = plan.model_dump(mode='json')
    write_json(target.with_suffix('.running.json'), row)
    base = MovePlan.model_validate(job['base_plan']) if job.get('base_plan') else None
    if plan is not None and (evidence.get('passed') or evidence.get('recovered_incumbent')):
        try:
            frame_path = target.with_suffix('.frames.json')
            gate = gate_plan(instance, plan, canonical, base_plan=base, frame_checkpoint_path=frame_path)
            row['gate'] = gate
            if not gate['passed'] and frame_path.exists() and job.get('sequence_repair', True) \
                    and binding_fingerprint() == source_digest:
                from . import sequence, sequence_blocks
                source_frames = json.loads(frame_path.read_text())
                row['sequence_repair_attempts'] = []
                for module, directory, bounds in (
                        (sequence, run_dir/'sequence'/key, {}),
                        (sequence_blocks, run_dir/'sequence-blocks'/key,
                         dict(window=32, lookback=4, beam_width=4, max_candidates=12, budget_s=180.))):
                    if binding_fingerprint() != source_digest:
                        break
                    attempt = dict(producer='lattice.'+module.__name__.rsplit('.', 1)[-1],
                        producer_label=module.LABEL, checkpoint_dir=str(directory), bounds=bounds,
                        original_plan_sha256=sha256_json(plan.model_dump(mode='json')),
                        source_frame_checkpoint=str(frame_path), source_frame_sha256=sha256_file(frame_path),
                        source_physics_epoch=source_frames.get('physics_epoch'),
                        status='admission_pending', historical_physics_is_release_certificate=False)
                    row['sequence_repair_attempts'].append(attempt)
                    row['release_stage'] = attempt['producer']+'_admission_pending'
                    write_json(target.with_suffix('.running.json'), row)
                    attempt['admission'] = admit(job.get('state_path', STATE))
                    if module is sequence:
                        row['sequence_repair_admission'] = attempt['admission']
                    started = time.monotonic()
                    attempt['status'] = 'repair_running'
                    write_json(target.with_suffix('.running.json'), row)
                    repaired = module.repair_candidate(instance, plan, source_frames,
                        source_frames.get('physics_epoch'), rules=canonical, base_plan=base,
                        checkpoint_dir=directory, profile=profile_record, **bounds)
                    attempt.update(wall_s=time.monotonic()-started,
                                   status='passed_fresh_gate' if repaired is not None else 'no_checked_repair')
                    if repaired is None:
                        attempt['reason'] = 'No candidate passed this bounded repair and its fresh release gate; not an impossibility proof.'
                        attempt['trial_checkpoints'] = []
                        for path in sorted(directory.glob('*.json')):
                            if path.name.endswith(('.trial-frames.json', '.native-frames.json')):
                                continue
                            trial = json.loads(path.read_text())
                            if trial.get('status'):
                                attempt['trial_checkpoints'].append(dict(path=str(path), sha256=sha256_file(path),
                                    **{k: trial[k] for k in ('status', 'reason', 'trial_failed_steps') if k in trial}))
                    row['sequence_repair_wall_s'] = sum(a.get('wall_s', 0.) for a in row['sequence_repair_attempts'])
                    write_json(target.with_suffix('.running.json'), row)
                    if repaired is not None:
                        row.update(plan_producer=attempt['producer'], producer_label=module.LABEL)
                        plan, gate = repaired
                        row['sequence_repair'] = gate['sequence_repair']
                        break
            row['gate'] = gate
            row.update(status='checked_plan' if gate['passed'] and name != 'live' else
                       'checked_live' if gate['passed'] else 'gate_failed',
                       lifts=gate.get('lifts'), base_lifts=gate.get('base_lifts'),
                       lift_delta=gate.get('lift_delta'), what_moved=gate.get('what_moved'),
                       base_already_obeys=gate.get('base_already_obeys'),
                       plan=plan.model_dump(mode='json'), instance=instance.model_dump(mode='json'),
                       base_plan=job.get('base_plan'))
        except Exception as error:
            row.update(status=release_failure_status(error), reason=f'{type(error).__name__}: {error}',
                       release_error=dict(type=type(error).__name__, message=str(error)),
                       rerun_required=release_failure_status(error) == 'rerun_required')
    else:
        row['status'] = refusal_status(evidence)
        if row['status'] == 'proved_refusal':
            row['instance'] = instance.model_dump(mode='json')
            row['proof'] = dict(reason=evidence.get('reason'), scope='necessary conditions, not search exhaustion')
            match = re.search(r'needs (\d+(?:\.\d+)?) TEU of cells but only (\d+) remain open', evidence.get('reason', ''))
            if match:
                row['proof']['arithmetic'] = dict(demand_cells=float(match[1]), open_cells=int(match[2]),
                    proven_impossible=True, source='binding necessary aggregate capacity screen',
                    equation=f'{match[1]} needed > {match[2]} open')
            if len(answers) == 1 and answers[0].type != 'one_port_per_hatch':
                from experiments.mvp.reader.refusal_evidence import build_static_proof
                row['proof']['arithmetic'] = build_static_proof(instance, answers[0])
    if reuse is not None:
        row['release_wall_s'] = time.monotonic()-began
    else:
        row['total_wall_s'] = time.monotonic()-began
    row.update(worker_released=True, release_utc=datetime.now(timezone.utc).isoformat())
    after_field = 'release_binding_source_after_sha256' if reuse is not None else 'binding_source_after_sha256'
    row[after_field] = binding_fingerprint()
    if row[after_field] != source_digest:
        row.update(status='rerun_required', rerun_required=True,
                   reason='Binding source changed during computation or release gates; rerun before storing.')
    if name != 'live' and row['status'] in ('checked_plan', 'proved_refusal'):
        save_entry(row, store_dir=Path(job.get('store_dir', HERE/'store')))
        save_hero_replan(row, fixtures_dir=job.get('fixtures_dir'))
        backup = Path(job.get('fixtures_dir', HERE/'fixtures'))/'shot6-backup.json'
        max_tiers = len(canonical) == 1 and canonical[0]['constraint']['type'] == 'max_tiers'
        if row['status'] == 'checked_plan' and (spec.get('backup_shot_6') or max_tiers and not backup.exists()):
            # Self-contained fallback is independent of the store/index.
            write_json(backup, row)
    write_json(target, row)
    target.with_suffix('.running.json').unlink(missing_ok=True)
    refresh_progress(run_dir)
    print(json.dumps(dict(id=spec['id'], key=key, status=row['status'],
                          lifts=row.get('lifts'), wall_s=row['wall_s'])), flush=True)
    return {k: row.get(k) for k in ('id', 'key', 'status', 'lifts', 'wall_s')}


def ordered_requests(manifest, *, include_live=True):
    stored = list(manifest['instructions'])
    live = list(manifest.get('off_lattice', [])) if include_live else []
    skipped = set(manifest.get('resume_skip_ids', []))
    return [spec for spec in stored[:4] + live + stored[4:] if spec['id'] not in skipped]


def screen_finished(row):
    """An early frame rejection can precede cleanup; keep its CPU reserved."""
    if (row.get('worker_released') is True and row.get('release_utc')
            and row.get('status') in ('checked_plan', 'checked_live', 'proved_refusal',
                                      'budget_failed', 'gate_failed', 'rerun_required', 'error')):
        return True
    if isinstance(row.get('gate'), dict) or isinstance(row.get('gauge'), dict):
        return True
    if 'early_gauge_rejection' in row:
        return False
    if row.get('passed') is False and not row.get('plan'):
        return True
    finished_engine = bool(row.get('computed_utc')) and isinstance(row.get('wall_s'), (int, float))
    return (row.get('status') == 'rerun_required' and finished_engine
            or row.get('passed') is False and bool(row.get('screening_error')))


def reserved_screen_count(paths):
    pending = 0
    normalized = {str((Path(path) if Path(path).is_absolute() else ROOT/Path(path)).resolve()) for path in paths}
    for path in normalized:
        try:
            row = json.loads(Path(path).read_text())
            complete = isinstance(row, dict) and screen_finished(row)
        except (OSError, UnicodeError, ValueError, TypeError):
            complete = False
        pending += not complete
    return pending


def worker_slots(workers, reserved_paths, *, allow_six=False):
    """Reservation campaigns always share at most four active CPU slots."""
    if reserved_paths:
        return min(workers, max(0, 4-reserved_screen_count(reserved_paths)))
    return min(workers, 6 if allow_six else 4)


def next_request_index(queue, manifest):
    """A draining predecessor retains exclusive ownership of its exact keys."""
    owned = manifest.get('reserved_request_paths', {})
    for index, spec in enumerate(queue):
        path = owned.get(spec['id'])
        if not path or reserved_screen_count([path]) == 0:
            return index
    return None


def checkpoint_worker_error(spec, error, run_dir, *, campaign_id=None):
    """Fallback release receipt when a worker failed outside its guarded gate."""
    run_dir = Path(run_dir)
    held, running = {}, None
    for path in run_dir.glob('*.running.json'):
        try:
            candidate = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if candidate.get('id') == spec['id'] and (campaign_id is None or candidate.get('campaign_id') == campaign_id):
            held, running = candidate, path
            break
    elapsed = None
    try:
        elapsed = max(0., time.time()-datetime.fromisoformat(held['computed_utc']).timestamp())
    except (KeyError, ValueError, TypeError, OSError):
        pass
    status = release_failure_status(error)
    after = binding_fingerprint()
    if held.get('binding_source_sha256') and held['binding_source_sha256'] != after:
        status = 'rerun_required'
    row = dict(held, id=spec['id'], status=status, reason=f'{type(error).__name__}: {error}',
               error=f'{type(error).__name__}: {error}', worker_released=True,
               release_utc=datetime.now(timezone.utc).isoformat(), total_wall_s=elapsed,
               total_wall_scope='scheduler observation since recorded computation timestamp',
               rerun_required=status == 'rerun_required', binding_source_after_sha256=after,
               release_stage='scheduler_worker_exception')
    if held.get('key'):
        target = run_dir/(held['key']+'.json')
        incumbent = target.with_suffix('.incumbent.json')
        if incumbent.exists():
            row['held_incumbent_checkpoint'] = dict(path=str(incumbent), sha256=sha256_file(incumbent))
        write_json(target, row)
    write_json(run_dir/(spec['id']+'.error.json'), row)
    if running is not None:
        running.unlink(missing_ok=True)
    refresh_progress(run_dir)
    return row


def run_batch(manifest, *, workers=4, include_live=True, run_dir=None, control_path=None):
    if blocked := campaign_status(manifest, control_path=control_path):
        print(json.dumps(blocked), flush=True)
        return blocked
    if workers not in (1, 2, 3, 4, 6):
        raise ValueError('workers must be1–4 or6')
    queue = ordered_requests(manifest, include_live=include_live)
    pending = {}
    low_since = None
    superseded = None
    # A retry imports the current binding instead of reusing stale worker code.
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'),
                             max_tasks_per_child=1) as pool:
        while queue or pending:
            if superseded is None and (blocked := campaign_status(manifest, control_path=control_path)):
                superseded = blocked
                queue.clear()
                print(json.dumps(blocked), flush=True)
            if not queue and not pending:
                break
            reading = capacity_reading()
            now = time.monotonic()
            low_since = (low_since or now) if reading and reading['load1'] < 30 else None
            slots = worker_slots(workers, manifest.get('reserved_screen_paths', []),
                                 allow_six=workers == 6 and low_since is not None and now-low_since >= 120)
            while queue and len(pending) < slots and reading:
                index = next_request_index(queue, manifest)
                if index is None:
                    break
                spec = queue.pop(index)
                job = dict(instance_path=manifest['instance_path'], base_plan=manifest['base_plan'], spec=spec,
                           campaign_id=manifest.get('campaign_id'),
                           campaign_control_path=str(control_path) if control_path is not None else None)
                if run_dir:
                    job['run_dir'] = str(run_dir)
                pending[pool.submit(compute, job)] = spec
            if pending:
                finished, _ = wait(pending, timeout=2., return_when=FIRST_COMPLETED)
                for future in finished:
                    spec = pending.pop(future)
                    identifier = spec['id']
                    try:
                        result = future.result()
                        if isinstance(result, dict) and result.get('status') == 'rerun_required' and superseded is None:
                            queue.append(spec)
                            print(json.dumps(dict(id=identifier, status='requeued_binding_change')), flush=True)
                    except Exception as error:
                        # One failed request cannot hold up any remaining rule.
                        result = checkpoint_worker_error(spec, error, Path(run_dir) if run_dir else HERE/'runs',
                                                         campaign_id=manifest.get('campaign_id'))
                        if result['status'] == 'rerun_required' and superseded is None:
                            queue.append(spec)
            else:
                time.sleep(2.)
    return superseded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--skip-live', action='store_true')
    parser.add_argument('--campaign-control', type=Path)
    options = parser.parse_args()
    run_batch(json.loads(options.manifest.read_text()), workers=options.workers,
              include_live=not options.skip_live, control_path=options.campaign_control)


if __name__ == '__main__':
    main()
