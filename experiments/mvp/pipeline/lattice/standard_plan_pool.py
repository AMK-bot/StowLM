"""Select another standard-search plan, then freshly gate the requested rules.

This is a portfolio selection, never an engine result or a sequence repair.
The requested standard search and donor retain their exact independent origins.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .gates import engine_identity, gate_plan, gauge_verdict
from .store import (_atomic, _binding_source_matches, _screen_source_guard, binding_compatibility, binding_fingerprint,
                    canonical_rule_set, entry_key, save_entry)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PRODUCER = 'lattice.standard_plan_pool'
LABEL = 'Stored standard-plan portfolio selection; freshly checked for the requested instructions'
ASSUMPTION = ('An independently computed standard-profile plan may serve another requested rule set only after '
              'that request had its own standard search and the donor passes a fresh original-instance full gate. '
              'The requested binding search did not produce the selected donor plan.')


def _path(value):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path if path.parts[:1] == ('experiments',) else HERE / path
    path = path.resolve()
    if not path.is_relative_to(HERE.resolve()):
        raise ValueError('portfolio paths must remain inside lattice')
    return path


def _standard_profile():
    from experiments.mvp.pipeline.runner.profiles import get_profile
    profile = get_profile('standard')
    return {key: asdict(value) if key == 'config' else value for key, value in profile.items()}


def _admit(job):
    from experiments.mvp.pipeline.binding.admission import wait_for_capacity
    return wait_for_capacity(state_path=job.get('state_path'))


def _source(path, expected):
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError('each portfolio source requires an independently pinned SHA256')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('portfolio source failed its pinned byte authentication')
    return json.loads(raw), raw


def _bound_audit(row):
    gate = row.get('gate') or {}
    rules = canonical_rule_set(row['canonical_rules'])
    audits = gate.get('rules') or []
    if (gate.get('instance_sha256') != row['instance_sha256'] or
            gate.get('plan_sha256') != sha256_json(row['plan']) or
            (gate.get('checker') or {}).get('passed') is not True or
            len(audits) != len(rules) or
            canonical_rule_set([audit['envelope'] for audit in audits]) != rules or
            any(audit.get('passed') is not True for audit in audits)):
        raise ValueError('portfolio source gate is not bound to its exact instance, plan and rules')
    return gate


def _validate(row, profile, identity):
    if row.get('source_screen'):
        _screen_source_guard(deepcopy(row))
    if (row.get('profile') != profile or profile.get('name') != 'standard' or
            profile.get('engine_timeout_s') != 900. or row.get('purpose') == 'off_lattice' or
            row.get('plan_producer') == PRODUCER or
            row.get('engine_version') != identity['engine_version'] or
            row.get('engine_manifest_sha256') != identity['engine_manifest_sha256'] or
            sha256_json(row['instance']) != row['instance_sha256'] or
            row.get('key') != entry_key(row['instance_sha256'], row['canonical_rules']) or
            row['plan'].get('instance_id') != row['instance'].get('id') or
            not _binding_source_matches(row)):
        raise ValueError('portfolio source instance, standard profile, runtime or engine provenance differs')
    if datetime.fromisoformat(row['computed_utc'].replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('portfolio source computed date needs a timezone')
    evidence = row.get('binding_evidence') or {}
    if not (evidence.get('passed') or evidence.get('recovered_incumbent')):
        raise ValueError('portfolio source lacks a checked standard-search candidate')
    gate = _bound_audit(row)
    if any((gate.get('engine_identity') or {}).get(field) != identity[field]
           for field in ('engine_version', 'engine_manifest_sha256', 'passed')):
        raise ValueError('portfolio source native gate engine identity differs')
    return gate


def _provenance(path, digest, archive, row):
    return dict(path=str(path.relative_to(ROOT)), raw_sha256=digest,
                exact_raw_archive=str(archive.relative_to(ROOT)), record=deepcopy(row),
                computed_utc=row['computed_utc'], canonical_rules=row['canonical_rules'],
                plan_sha256=sha256_json(row['plan']), profile=row['profile'],
                engine_version=row['engine_version'], engine_manifest_sha256=row['engine_manifest_sha256'],
                binding_compatibility=binding_compatibility(row))


def _campaign_current(job):
    if not job.get('campaign_id'):
        return True
    control = _path(job.get('campaign_control_path', HERE / 'campaign-control.json'))
    return json.loads(control.read_text()).get('active_campaign') == job['campaign_id']


def _refresh_progress(run_dir):
    from .report import update
    return update(run_dir=run_dir, output_dir=Path(run_dir).parent)


def _failure_status(error):
    text = f'{type(error).__name__}: {error}'.lower()
    return 'rerun_required' if ('epochdrift' in text or 'changed' in text and
                               any(word in text for word in ('source', 'physics', 'checker', 'campaign'))) else 'portfolio_failed'


def compute(job):
    """Gate one pinned requested/donor pair; no binding or live search occurs.

    ``job.spec`` supplies standard_plan_pool_{requested,donor}_{path,sha256}.
    Other batch fields retain their meanings, including base_plan and run_dir.
    Failures checkpoint separately and never replace the requested run or store.
    """
    try:
        return _compute(job)
    except Exception as error:
        identifier = job.get('spec', {}).get('id')
        status = _failure_status(error)
        receipt = dict(id=identifier, status=status, rerun_required=status == 'rerun_required',
                       reason=f'{type(error).__name__}: {error}', producer=PRODUCER,
                       native_engine_output=False, worker_released=True, process_pid=os.getpid(),
                       release_utc=datetime.now(timezone.utc).isoformat(), campaign_id=job.get('campaign_id'))
        name = hashlib.sha256(str(identifier).encode()).hexdigest()[:16] + f'.{time.time_ns()}.json'
        _atomic(HERE/'standard-plan-pool'/'errors'/name, receipt)
        return receipt


def _compute(job):
    spec = job['spec']
    requested_path = _path(spec['standard_plan_pool_requested_path'])
    donor_path = _path(spec['standard_plan_pool_donor_path'])
    requested_pin = spec['standard_plan_pool_requested_sha256']
    donor_pin = spec['standard_plan_pool_donor_sha256']
    own, own_raw = _source(requested_path, requested_pin)
    donor, donor_raw = _source(donor_path, donor_pin)
    rules = canonical_rule_set(spec['rules'])
    key = entry_key(own['instance_sha256'], rules)
    run_dir = _path(job.get('run_dir', HERE/'runs'))
    target = run_dir / (key+'.json')
    running = target.with_suffix('.running.json')
    directory = _path(job.get('checkpoint_dir', HERE / 'standard-plan-pool' / key / str(time.time_ns())))
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.pool.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        row = dict(id=spec['id'], key=key, status='portfolio_validation_pending',
                   producer=PRODUCER, plan_producer=PRODUCER, producer_label=LABEL,
                   native_engine_output=False, assumption=ASSUMPTION,
                   campaign_id=job.get('campaign_id'), mode='stored', label='stored')
        checkpoint = directory / 'result.json'
        began = time.monotonic()
        try:
            if not _campaign_current(job):
                return dict(id=spec['id'], key=key, status='superseded')
            identity = engine_identity()
            source, producer = binding_fingerprint(), sha256_file(__file__)
            profile = _standard_profile()
            if not identity['passed']:
                raise ValueError('pinned engine sources do not match')
            own_gate = _validate(own, profile, identity)
            donor_gate = _validate(donor, profile, identity)
            original_path = Path(own['instance_path'])
            original_path = original_path if original_path.is_absolute() else ROOT / original_path
            supplied_path = Path(job.get('instance_path', original_path))
            supplied_path = supplied_path if supplied_path.is_absolute() else ROOT / supplied_path
            if (supplied_path.resolve() != original_path.resolve() or spec['id'] != own['id'] or
                    not rules or rules != canonical_rule_set(own['canonical_rules']) or
                    own['instance_sha256'] != donor['instance_sha256'] or own['instance'] != donor['instance'] or
                    own.get('status') not in ('gate_failed', 'rerun_required') or own_gate.get('passed') is not False or
                    own.get('plan_producer') or
                    own.get('original_binding_plan', own['plan']) != own['plan'] or
                    (own_gate.get('gauges') or {}).get('passed') is not False or
                    not (own_gate['gauges'].get('outside_limits_steps') or own_gate['gauges'].get('unavailable_steps')) or
                    donor.get('status') != 'checked_plan' or donor_gate.get('passed') is not True or
                    sha256_json(own['plan']) == sha256_json(donor['plan'])):
                raise ValueError('portfolio requires an own failed physical candidate and a different checked donor')
            fresh_donor_gauges = gauge_verdict(donor_gate['gauges']['physics'], len(donor['plan']['moves']))
            if not fresh_donor_gauges['passed'] or any(donor_gate['gauges'].get(field) != fresh_donor_gauges[field]
                    for field in ('passed', 'all_frames', 'n_frames', 'outside_limits_steps', 'unavailable_steps')):
                raise ValueError('donor lacks a complete native green frame sequence')
            for field in ('engine_wall_s',):
                for original in (own, donor):
                    value = original.get(field)
                    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                        raise ValueError('standard-search timing provenance is unavailable')
            base = deepcopy(job.get('base_plan', own.get('base_plan')))
            if not base or own.get('base_plan') != base or donor.get('base_plan') != base:
                raise ValueError('portfolio base plan must match both original runs')
            if own_gate.get('base_already_obeys') is None or all(own_gate['base_already_obeys']):
                raise ValueError('base already obeys this request; it cannot count as an instruction replan')
            for name, raw in (('requested-source.json', own_raw), ('donor-source.json', donor_raw)):
                archive = directory / name
                if archive.exists():
                    if archive.read_bytes() != raw:
                        raise ValueError('portfolio checkpoint already belongs to another source snapshot')
                else:
                    archive.write_bytes(raw)
            proof = dict(producer=PRODUCER, native_engine_output=False, assumption=ASSUMPTION,
                requested=_provenance(requested_path, requested_pin, directory/'requested-source.json', own),
                donor=_provenance(donor_path, donor_pin, directory/'donor-source.json', donor),
                producer_source_sha256=producer, binding_source_sha256=source,
                requested_binding_produced_selected_plan=False, sequence_repair=False,
                move_multiset_equality_claimed=False, final_stowage_equality_claimed=False,
                historical_physics_reused=False, fresh_full_gate_required=True)
            row.update(standard_plan_pool=proof, status='release_gate_pending',
                release_stage='standard_plan_pool_gate_admission_pending', process_pid=os.getpid(),
                computed_utc=donor['computed_utc'], canonical_rules=rules, profile=profile,
                instance_path=own['instance_path'], instance_sha256=own['instance_sha256'],
                portfolio_checkpoint=str(checkpoint), worker_released=False)
            _atomic(checkpoint, row)
            _atomic(running, row)
            row['preflight_wall_s'] = time.monotonic()-began
            admission_began = time.monotonic()
            row['admission'] = _admit(job)
            release_began = time.monotonic()
            row['initial_admission_wall_s'] = release_began-admission_began
            row['release_timing_scope'] = 'after_initial_admission'
            if not _campaign_current(job):
                row['status'] = 'superseded'
                _atomic(checkpoint, row)
                return {field: row[field] for field in ('id', 'key', 'status')}
            row['release_stage'] = 'standard_plan_pool_native_gate_running'
            _atomic(checkpoint, row)
            _atomic(running, row)
            gate_began = time.monotonic()
            gate = gate_plan(own['instance'], donor['plan'], rules, base_plan=base,
                             admit=False, frame_checkpoint_path=directory/'native.frames.json')
            row['native_gate_wall_s'] = time.monotonic()-gate_began
            row['gate'] = gate
            if (binding_fingerprint() != source or engine_identity() != identity or sha256_file(__file__) != producer or
                    sha256_file(requested_path) != requested_pin or sha256_file(donor_path) != donor_pin or
                    not _binding_source_matches(own) or not _binding_source_matches(donor)):
                raise ValueError('source changed during portfolio release; rerun required')
            if (not gate.get('passed') or gate.get('binding') is not True or
                    not gate.get('base_already_obeys') or all(gate['base_already_obeys'])):
                row.update(status='portfolio_gate_failed', reason='Selected donor failed the requested fresh gate or changed no binding instruction.')
                _atomic(checkpoint, row)
                return {field: row[field] for field in ('id', 'key', 'status')}
            _bound_audit(dict(instance_sha256=own['instance_sha256'], plan=donor['plan'], canonical_rules=rules, gate=gate))
            if not gauge_verdict(gate['gauges']['physics'], len(donor['plan']['moves']))['passed']:
                raise ValueError('fresh portfolio gate lacks every native crane frame')
            selected = datetime.now(timezone.utc).isoformat()
            row.update(status='checked_plan', purpose=spec.get('purpose', 'lattice'), sentences=spec['sentences'],
                rules=rules, canonical_rules=rules, instance=own['instance'], instance_path=own['instance_path'],
                instance_sha256=own['instance_sha256'], plan=donor['plan'], base_plan=base,
                engine_version=identity['engine_version'], engine_manifest_sha256=identity['engine_manifest_sha256'],
                profile=profile, computed_utc=donor['computed_utc'], pool_selected_utc=selected,
                requested_computed_utc=own['computed_utc'], donor_computed_utc=donor['computed_utc'],
                requested_engine_wall_s=own['engine_wall_s'], donor_engine_wall_s=donor['engine_wall_s'],
                engine_wall_s=own['engine_wall_s'], wall_s=own.get('wall_s', own['engine_wall_s']),
                total_wall_s=own.get('total_wall_s'), release_wall_s=time.monotonic()-release_began,
                portfolio_total_wall_s=time.monotonic()-began,
                portfolio_native_search_s=0., worker_released=True, release_utc=selected, release_stage='completed',
                binding_source_sha256=source, binding_source_after_sha256=source,
                producer_source_sha256=producer, lifts=gate['lifts'], base_lifts=gate.get('base_lifts'),
                lift_delta=gate.get('lift_delta'), what_moved=gate.get('what_moved'),
                base_already_obeys=gate['base_already_obeys'], measurement='measured',
                confirmation_source='batch', binding_evidence=dict(producer=PRODUCER, native_engine_output=False,
                    native_binding_called=False, passed=True, status='independently_regated_standard_plan_pool_selection'))
            if not _campaign_current(job):
                raise ValueError('campaign changed during portfolio release; rerun required')
            save_entry(row, store_dir=_path(job.get('store_dir', HERE/'store')))
            _atomic(target, row)
            _atomic(checkpoint, row)
            return {field: row[field] for field in ('id', 'key', 'status', 'lifts', 'wall_s')}
        except Exception as error:
            status = _failure_status(error)
            row.update(status=status, rerun_required=status == 'rerun_required',
                       reason=f'{type(error).__name__}: {error}',
                       wall_s=time.monotonic()-began, worker_released=True, release_stage='release_failed',
                       release_utc=datetime.now(timezone.utc).isoformat())
            _atomic(checkpoint, row)
            return {field: row[field] for field in ('id', 'key', 'status', 'reason')}
        finally:
            if checkpoint.exists() and not row.get('worker_released'):
                row.update(worker_released=True, release_utc=datetime.now(timezone.utc).isoformat())
                _atomic(checkpoint, row)
            try:
                active = json.loads(running.read_text())
                if active.get('process_pid') == os.getpid() and active.get('portfolio_checkpoint') == str(checkpoint):
                    running.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError):
                pass
            try:
                _refresh_progress(run_dir)
            except Exception as error:
                row['report_refresh_error'] = f'{type(error).__name__}: {error}'
                _atomic(checkpoint, row)
