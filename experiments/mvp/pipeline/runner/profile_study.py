"""Resumable synthetic development budget study; never reads sealed maps.

Run this coordinator with nohup. Each map/rule/budget cell owns a fresh Python
process and an atomic row checkpoint. Native defaults remain an untouched arm.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DEV = ROOT / 'experiments/mvp/pipeline/maps/dev'
STATE = ROOT / 'experiments/fleet/core-state.json'
SENTENCE = 'Each physical hatch cover must serve only one discharge port.'
CONFIRMATION_SOURCE = 'profile_study_batch_confirm_all'
AUTH_FAILURE = 'StageCallError: Engine could not complete (ValueError). Check the service and retry.'
MIGRATION = 'explicit_batch_confirmation_and_partial_hatch_v1'
EXTRA_MIGRATION_HASHES = {
    'experiments/mvp/pipeline/binding/allocation.py':
        'fbeb7049881e04eb067b624f7ad6dee57019f0c53874f9e642e9eeff560696c5',
    'experiments/mvp/pipeline/binding/hooks.py':
        '855927b2eaf5571c141b381bc362683d9b6a59fed26718b2822789c2efbf31a8',
    'experiments/mvp/pipeline/binding/measurement_lifecycle.py':
        '38c7f9fb8442b55e881922c2af4e37a104b4a7e4702e4e54cf1523a178b2c5de',
    'experiments/mvp/pipeline/binding/test_standing_gate.py':
        'fc53100418f5e0cd1c6ea115182c6f7dcf5a788b6f601018c8d3660362c001d6',
    'experiments/mvp/pipeline/binding/test_partial_hatch.py':
        '9c1330cd9bbad2d80bb487bbe0b17f87bbe4efb1900e55c5c566061be159e7cc',
    'experiments/mvp/pipeline/binding/test_review_provenance.py':
        '76810bf8e8d0d7a0234ca85594dd167f9b0088ac8e1f3cfbf064e49514d3bd98',
    'experiments/mvp/pipeline/binding/provenance.py':
        '98d54f71219483b5e6d5d642be098ab5d6610d836a7313d20527244991f5d9bd',
    'experiments/mvp/pipeline/binding/measurement_identity.py':
        'fa3466908422ee2cce40e81a65c0ad97162856032f42927fc952d6038f17abf1',
    'experiments/mvp/pipeline/binding/_logic3_activation.py':
        '10e65ef4242a9bdbb6123eafa02453a4f4ef2143229dc91dd7a44cceceb7bcbe',
    'experiments/mvp/pipeline/runner/limits.py':
        '4acfbbdcb4ae816023c0182bb7820a1c044b81de33d3c6123119f7eb24423250',
}
POLICY_DEPENDENCIES = {
    'activation.py': 'e0a70b14c1b870520f421d0809c31e979d8501c0f47f502b0857b8ad5db924cb',
    'api.py': 'df491918cc90df91079ab5ce10268faf324235e2f2540a956e8ed39230e60379',
    'campaign.py': 'a01f30c324e4f65323c93b6ace13b5d8ce63a8709896d07b15b0477ea08ba6dd',
    'measurement.py': '8c0041c7b19f4ac9e6d0235c7b89324c15693b156766366bab5fbf070d286498',
    'hatch.py': '7c82c4fa3c248b6d977439e382ee715d926488e040ba731ab9a3559f8c916cad',
    'recorded.py': '9c198320db05e1c30053626f912547ac7db8c204a30715edf946797626eea862',
    '__init__.py': '53b897a4df90b891fb1a48f4870047d21a8ccc8b81a28cbb63c147688c9518f0',
    'init-export-delta.json': '2e7e36643ad2284a40a58efff11be0124fb47c86cb92bb7c55580ebb737e9c40',
}
POLICY_CAVEAT = ('Reviewed logic3 hatch portfolio is a new numerical search epoch: at most three sequential '
                 'strict mappings, with additive configured work per mapping. Legacy single-mapping hatch rows '
                 'remain immutable and cannot be pooled with portfolio rows to calibrate profiles. '
                 'A fresh quiet study is required for clean budget comparisons.')
SOURCE_CAVEAT = ('Mixed source epochs: earlier rows precede explicit batch confirmation and the '
                 'hatch instruction correction that leaves missing-load completion to the full checker. '
                 'The reviewed migration also adds stronger frozen provenance checks, owned ballast-pool cleanup, '
                 'and durable measurement-attempt evidence. '
                 'Authorized runner B2/B6 presentation and API changes include earlier-plan display, ladder reporting, '
                 'and provisional live-profile peel budgets; the explicitly scheduled study_profile configurations '
                 'are unchanged, and execute_row does not use provisional profile defaults. '
                 'D261 changes runner-owned deadline/checker reporting; frozen study configurations and native search are unchanged. '
                 'Finished rows are preserved; each new row records its actual execution source. '
                 'Comparisons across source epochs may be confounded. Every passing row still requires '
                 'the full checker and every instruction recheck. ' + POLICY_CAVEAT)
LOADED_METADATA_CAVEAT = ('Reviewed source-baseline compatibility bridge: affected worker metadata helpers were '
                          'loaded before runner B2/B6 changes. Their frozen study configurations are unchanged; '
                          'binding imports after the source gate are fresh. The source snapshot identifies the '
                          'reviewed files on disk, while the metadata helper itself remains the earlier loaded version.')
MIGRATION_FILES = {
    'binding/' + name for name in ('confirmation.py', 'adapter.py', '__init__.py',
                                    'test_confirmation.py', 'test_adapter.py')
} | {'runner/' + name for name in ('profile_study.py', 'test_profile_study.py',
                                    'benchmark.py', '__main__.py', 'core.py',
                                    'test_logic_review.py', 'test_offline.py', 'test_cli_profiles.py', 'test_admission.py',
                                    'profile_finalize.py', 'profiles.py', 'evidence.py',
                                    'test_earlier_plan.py', 'test_ladder_work.py', 'test_profile_finalize.py')}
NONEXECUTED_BINDING = {'measure.py', 'measurement_lifecycle.py', 'measurement_identity.py',
                       'live_campaign.py', '_logic3_watchdog.py', '_logic3_reporting.py'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    from .evidence import json_evidence
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    with temporary.open('w') as stream:
        json.dump(json_evidence(value), stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def telemetry(state_path=STATE, *, now=None):
    """An invalid sample is evidence, never invented capacity or an exception."""
    sample = dict(observed_utc=utc(), valid=False, load1=None, enabled_cores=None)
    try:
        raw = json.loads(Path(state_path).read_text())
        value = raw['mask']
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError('invalid mask')
        mask = int(value, 0) if isinstance(value, str) else value
        if isinstance(raw['load1'], bool):
            raise ValueError('invalid load')
        load = float(raw['load1'])
        age = (time.time() if now is None else now) - float(raw['t'])
        if mask < 0 or not math.isfinite(load) or load < 0 or not math.isfinite(age):
            raise ValueError('invalid telemetry')
        sample.update(mask=mask, enabled_cores=mask.bit_count(), load1=load,
                      reading_age_s=age, reading_t=raw['t'], valid=0 <= age < 120)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, OverflowError) as error:
        sample['message'] = f'Capacity telemetry unavailable ({type(error).__name__}); waiting.'
    return sample


def wait_for_capacity(state_path=STATE, *, poll_s=5., sleep=time.sleep, sample=None):
    started = time.monotonic()
    sample = sample or (lambda: telemetry(state_path))
    while True:
        row = sample()
        if row.get('valid') and row['enabled_cores'] >= 8 and row['load1'] <= 45:
            return dict(row, waited_s=time.monotonic() - started)
        sleep(poll_s)


def worker_limit(samples, *, base=4, maximum=6):
    """Six only after three distinct fresh low-load samples spanning 60 seconds."""
    recent = samples[-3:]
    quiet = (len(recent) == 3 and recent[-1]['sampled_monotonic'] - recent[0]['sampled_monotonic'] >= 60
             and len({r.get('reading_t') for r in recent}) == 3
             and all(r.get('valid') and r.get('enabled_cores', 0) >= 8
                     and r.get('load1', math.inf) < 30 for r in recent))
    return maximum if quiet else base


def study_profile(label):
    from engine.config import Config
    defaults = Config()
    if str(label) == 'default':
        return dict(budget_label='default', budget_s=None, finalize_s=None, seed=0,
                    config=None, resolved_config=asdict(defaults), untouched_defaults=True)
    budget = float(label)
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError('Budgets must be positive finite seconds or default')
    config = Config(assignment_budget_s=budget, assignment_finalize_s=budget / 3,
                    assignment_tries=1, lift_polish_s=budget / 12,
                    peel_proposal_s=budget / 3, peel_finalize_s=budget / 6,
                    peel_candidates=1, proof_outer_s=budget / 6,
                    post_pass_exchange_s=budget / 12, area_securing_s=budget / 6,
                    warm_polish=False, work_count_search=False)
    return dict(budget_label=f'{budget:g}', budget_s=budget, finalize_s=budget / 3,
                seed=0, config=asdict(config), resolved_config=asdict(config),
                untouched_defaults=False)


class SyntheticBenchmarkParser:
    """Exact synthetic fixture mapping; no model, network, or GPU."""
    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import OnePortPerHatch, refusal, validate_context
        if sentence == SENTENCE:
            answer = validate_context(OnePortPerHatch(type='one_port_per_hatch'), context)
        elif sentence == '':
            answer = refusal('not_an_instruction')
        else:
            raise ValueError('Synthetic benchmark parser accepts its one fixture sentence only')
        return answer, dict(parser='SyntheticBenchmarkParser', adapter_sha256=None,
                            synthetic=True, model=None, cost_usd=0.,
                            label='Synthetic exact rule fixture; not a model measurement')


def dev_maps(dev_root=DEV):
    """Resolve and fence every filename before opening bytes."""
    dev_root = Path(dev_root).resolve()
    if 'sealed' in dev_root.parts:
        raise ValueError('Sealed inputs are forbidden')
    paths = []
    for candidate in sorted(dev_root.glob('*.json')):
        path = candidate.resolve()
        if 'sealed' in path.parts or not path.is_relative_to(dev_root):
            raise ValueError('Development map resolves outside its approved folder')
        paths.append(path)
    return paths


def map_order(paths):
    """Put one f60/f70 × random/simulated example ahead of repeat seeds."""
    first, rest, seen = [], [], set()
    for path in paths:
        key = tuple(part for part in path.stem.split('-') if part in
                    {'f60', 'f70', 'random_yard', 'simulated_yard'})
        (rest if key in seen else first).append(path)
        seen.add(key)
    return first + rest


def make_jobs(paths, labels, *, cohort_sha256, timeout_s=7200.):
    jobs = []
    for path in paths:
        digest = sha256_file(path)
        # Complete all cells of the first map before moving to the next map.
        for label in labels:
            for mode in ('none', 'one_port_per_hatch'):
                profile = study_profile(label)
                job = dict(map=str(path.relative_to(ROOT)), map_id=path.stem,
                           map_sha256=digest, cohort_sha256=cohort_sha256,
                           rule_mode=mode, profile=profile, engine_timeout_s=timeout_s)
                job['job_id'] = sha256_json(job)[:24]
                jobs.append(job)
    return jobs


def source_snapshot():
    files = sorted(HERE.glob('*.py')) + sorted((HERE.parent / 'binding').glob('*.py'))
    files.append(ROOT / 'experiments/mvp/frontier/engine/source-manifest-0.13.0-review-fixes.json')
    hashes = {str(path.relative_to(ROOT)): sha256_file(path) for path in files}
    return dict(files=hashes, sha256=sha256_json(hashes))


def pinned_evidence(evidence):
    return not any(isinstance(evidence.get(key), dict) and evidence[key].get('passed') is False
                   for key in ('engine_manifest', 'engine_manifest_after'))


def policy_dependencies():
    """Hash reviewed policy files only; never import activation or campaign."""
    base = ROOT / 'experiments/mvp/pipeline/binding/review3'
    for name, expected in POLICY_DEPENDENCIES.items():
        if sha256_file(base / name) != expected:
            raise ValueError(f'Unreviewed hatch policy dependency: {name}; no optimizer may start')
    files = {'experiments/mvp/pipeline/binding/review3/' + name: digest
             for name, digest in POLICY_DEPENDENCIES.items()}
    return dict(schema_version='stowlm.policy-dependencies/1',
                portfolio_policy_version='logic3-hatch-portfolio-v1', files=files,
                sha256=sha256_json(files), source_epoch_caveat=POLICY_CAVEAT)


def execute_row(job, out_dir, *, executor=None, admit=None, limited=None):
    """One independent cell, including fresh original-instance release gates."""
    out_dir = Path(out_dir).resolve()
    if not out_dir.is_relative_to(HERE):
        raise ValueError('Study output must stay inside runner/')
    policy = policy_dependencies()
    from .assembly import assemble
    from .envelope import check as envelope_check
    from .benchmark import _validated_plan, _validated_relaxed_plan
    from .runtime import configure_caches
    from .admission import _binding_runtime_scope, call_admitted
    from . import admission as admission_module
    from .limits import call_with_limit, StageTimeout
    from experiments.mvp.reader.public_brief import VoyageBrief, read_brief
    from engine.config import Config
    from stowage.frozen import active_stubs
    if executor is None:
        from experiments.mvp.pipeline.binding import execute_brief
        executor = execute_brief
    limited = limited or call_with_limit
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
        os.environ[name] = '1'
    cache_root = out_dir / 'cache'
    admission_module.CACHE_ROOT = cache_root
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(cache_root / 'vessels')
    configure_caches()
    profile = job['profile']
    row = dict(job, **{key: profile[key] for key in ('budget_label', 'budget_s', 'finalize_s', 'seed')},
               actual_kwargs={key: profile[key] for key in ('budget_s', 'finalize_s', 'seed', 'config')},
               config=profile['resolved_config'], started_utc=utc(), worker_pid=os.getpid(),
               measurement='measured', size_class='medium', full_pass=False, passed=False, lifts=None,
               first_legal_s=None, total_s=None, engine_s=None, status='running',
               with_rules=job['rule_mode'] != 'none', independent_check=None)
    row['policy_dependencies_sha256'] = policy['sha256']
    row_path = out_dir / 'rows' / f'{job["job_id"]}.json'
    progress_path = out_dir / 'progress' / f'{job["job_id"]}.json'
    started = None
    best = []
    readings = []
    try:
        path = (ROOT / job['map']).resolve()
        if path not in dev_maps():
            raise ValueError('Map is no longer in the approved development folder')
        if sha256_file(path) != job['map_sha256']:
            raise ValueError('Map bytes changed since the frozen cohort manifest')
        packet = dict(packet_id=f'profile-{job["job_id"]}', created_utc=utc(), synthetic=True,
                      base_instance=dict(path=str(path), sha256=job['map_sha256']),
                      standing='', voyage_sentences=[], profile='live')
        instance = assemble(packet)
        envelope = envelope_check(instance)
        row.update(size_class=envelope['size_class'], envelope=envelope)
        if not envelope['in_envelope'] or row['size_class'] != 'medium':
            raise ValueError('Profile study requires an in-envelope medium development map')
        row['admission'] = (admit or wait_for_capacity)()
        started = time.perf_counter()
        row['start'] = telemetry()
        row['started_utc'] = utc()
        write_json(progress_path, row)
        with _binding_runtime_scope(cache_root):
            if active_stubs():
                raise ValueError('Active checker stubs prevent a full-checker profile study')
            sentences = (SENTENCE,) if row['with_rules'] else ()
            receipt = read_brief(instance, VoyageBrief(standing='', voyage=sentences),
                                 parser=SyntheticBenchmarkParser())
            row['reading'] = receipt.evidence()
            row['reading_sha256'] = sha256_json(row['reading'])
            row['confirmation'] = 'Synthetic benchmark instruction explicitly batch confirmed'
            readings = [r for r in row['reading']['sentences'] if r['role'] == 'voyage']
            if any(r['status'] != 'accepted' for r in readings):
                raise ValueError('Synthetic fixture reading was not accepted')
            from experiments.mvp.pipeline.binding import confirm_brief
            receipt = confirm_brief(receipt, source=CONFIRMATION_SOURCE)
            row['confirmation_source'] = CONFIRMATION_SOURCE
            from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
            row['confirmation'] = verify_confirmation(receipt)
            write_json(progress_path, row)

            def incumbent(event):
                if not isinstance(event, dict) or event.get('plan') is None:
                    return
                verdict = _validated_plan(instance, event['plan'], readings)
                if verdict is None or (best and verdict['lifts'] >= best[0]['verdict']['lifts']):
                    return
                if row['first_legal_s'] is None:
                    row['first_legal_s'] = time.perf_counter() - started
                best[:] = [dict(plan=event['plan'], verdict=verdict, event=event)]
                write_json(out_dir / 'incumbents' / f'{job["job_id"]}.json',
                           dict(job_id=job['job_id'], first_legal_s=row['first_legal_s'],
                                observed_s=time.perf_counter() - started, **best[0]))
                write_json(progress_path, dict(row, latest_lifts=verdict['lifts']))

            kwargs = {key: profile[key] for key in ('budget_s', 'finalize_s', 'seed')}
            kwargs['config'] = Config(**profile['config']) if profile['config'] is not None else None
            engine_started = time.perf_counter()
            try:
                result, evidence = limited(call_admitted, executor, instance, receipt,
                    timeout_s=job['engine_timeout_s'], stage='engine', on_event=incumbent,
                    event_kw='on_incumbent', admission=row['admission'], **kwargs)
                raw = getattr(result, 'plan', None) if result is not None else None
                if raw is not None and hasattr(raw, 'model_dump'):
                    raw = raw.model_dump(mode='json')
                row.update(evidence=evidence, engine_timed_out=False)
                relaxed = evidence.get('status') == 'relaxed_plan'
                verdict = (_validated_relaxed_plan if relaxed else _validated_plan)(instance, raw, readings) if raw is not None else None
                pin_ok = pinned_evidence(evidence)
                full = pin_ok and verdict is not None and evidence.get('status') == 'checked_plan' and evidence.get('passed') is True
                # Binding retains incumbents, but preserve a separately gated callback too.
                if not full and best and pin_ok and evidence.get('status') in {'checked_plan', 'no_checked_plan', 'relaxed_plan', 'error'}:
                    raw = best[0]['plan']
                    verdict = _validated_plan(instance, raw, readings)
                    full, relaxed = verdict is not None, False
                row.update(status='checked_plan' if full else ('relaxed_plan' if relaxed and verdict else evidence.get('status', 'no_checked_plan')),
                           full_pass=full, passed=full, independent_check=verdict,
                           lifts=verdict['lifts'] if verdict else None, plan=raw if verdict else None)
                if not pin_ok:
                    row.update(status='constraint_unavailable', full_pass=False, passed=False,
                               plan=None, lifts=None, independent_check=None,
                               message='Pinned engine identity changed; no plan may be released.')
                if row['status'] == 'checked_plan' and not full:
                    row['status'] = 'no_checked_plan'
                if verdict and relaxed:
                    row.update({key: verdict[key] for key in ('level', 'completed', 'boxes_aboard_percent')})
                if full and row['first_legal_s'] is None:
                    row['first_legal_s'] = time.perf_counter() - started
            except StageTimeout:
                # Emergency censoring is not a measured failure or a normal profile cap.
                row.update(status='interrupted', engine_timed_out=True, censored=True,
                           message='Emergency study deadline reached; outcome unknown and excluded from pass totals.',
                           checked_incumbent=best[0] if best else None)
            finally:
                row['engine_s'] = time.perf_counter() - engine_started
    except Exception as error:
        row.update(status='error', message=f'{type(error).__name__}: {error}')
    row.update(end=telemetry(), finished_utc=utc(),
               total_s=time.perf_counter() - started if started is not None else 0.)
    row['wall_s'] = row['total_s']
    write_json(row_path, row)
    write_json(progress_path, row)
    return row


def refresh_summary(out_dir, jobs):
    rows = [json.loads(path.read_text()) for path in sorted((out_dir / 'rows').glob('*.json'))]
    baseline = {(r['map'], r['budget_label']): r for r in rows
                if r.get('rule_mode') == 'none' and r.get('full_pass') and r.get('plan')}
    for row in rows:
        if row.get('rule_mode') != 'one_port_per_hatch':
            continue
        paired = baseline.get((row['map'], row['budget_label']))
        if paired is None:
            row.update(baseline_audit_status='no_passing_matched_baseline', baseline_violates_rule=None)
            continue
        audit_path = out_dir / 'baseline-audits' / f'{row["job_id"]}.json'
        if audit_path.exists():
            audit = json.loads(audit_path.read_text())
        else:
            try:
                from stowage.model import Instance, MovePlan
                from experiments.mvp.reader.pipeline import intrinsic_violations
                from experiments.mvp.reader.schema import OnePortPerHatch
                path = (ROOT / row['map']).resolve()
                if path not in dev_maps() or sha256_file(path) != row['map_sha256']:
                    raise ValueError('Baseline map changed or left development folder')
                instance = Instance.model_validate_json(path.read_text())
                violations = list(intrinsic_violations(instance, OnePortPerHatch(type='one_port_per_hatch'),
                                                       MovePlan.model_validate(paired['plan'])))
                audit = dict(baseline_audit_status='checked', baseline_job_id=paired['job_id'],
                             baseline_violations=violations, baseline_violates_rule=bool(violations))
                write_json(audit_path, audit)
            except Exception as error:
                audit = dict(baseline_audit_status='error', baseline_violates_rule=None,
                             baseline_audit_message=f'{type(error).__name__}: {error}')
        row.update(audit)
    source_epochs = sorted({r.get('execution_source_sha256', r.get('source_sha256')) for r in rows
                            if r.get('execution_source_sha256', r.get('source_sha256'))})
    metadata = dict(source_epochs=source_epochs, source_epoch_caveat=SOURCE_CAVEAT)
    write_json(out_dir / 'source-epochs.json', metadata)
    write_json(out_dir / 'measurements.json', dict(rows=rows, completed=len(rows), expected=len(jobs), **metadata))
    try:
        from .profile_analysis import analyze
        write_json(out_dir / 'summary.json', dict(analyze(rows, expected_jobs=jobs), **metadata))
    except ImportError:
        pass
    except Exception as error:
        write_json(out_dir / 'summary-error.json', dict(message=f'{type(error).__name__}: {error}'))


def recorded_pid_alive(pid):
    """Probe only a PID saved by this study; never signal it to stop work."""
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def migration_provenance(job, out_dir):
    """Attribute new workers honestly while retaining original successful rows."""
    policy_dependencies()  # Validate before even the legacy source fast path.
    out_dir = Path(out_dir)
    snapshot = source_snapshot()
    manifest = json.loads((out_dir / 'jobs.json').read_text())
    initial = manifest['source_sha256']
    target = snapshot['sha256']
    if target == initial:
        return dict(job, source_sha256=target, execution_source_sha256=target,
                    original_source_sha256=job.get('source_sha256', initial))
    previous = json.loads((out_dir / 'source-snapshots' / f'{initial}.json').read_text())
    changed = sorted(name for name in set(previous['files']) | set(snapshot['files'])
                     if previous['files'].get(name) != snapshot['files'].get(name))
    prefix = 'experiments/mvp/pipeline/'
    reviewed = dict(EXTRA_MIGRATION_HASHES)
    approval_path = out_dir / 'migrations' / 'reviewed-extra-hashes.json'
    if approval_path.exists():
        approval = json.loads(approval_path.read_text())
        if approval.get('kind') == MIGRATION and approval.get('from_source_sha256') == initial:
            reviewed.update(approval.get('hashes', {}))
    def permitted(name):
        relative = name.removeprefix(prefix)
        binding_name = relative.removeprefix('binding/')
        attribution_only = (name.startswith(prefix) and relative.startswith('binding/') and
                            (binding_name in NONEXECUTED_BINDING
                             or (binding_name.startswith('test_') and '/' not in binding_name and binding_name.endswith('.py'))))
        return (attribution_only or (name.startswith(prefix) and relative in MIGRATION_FILES)
                or (name in reviewed and snapshot['files'].get(name) == reviewed[name]))
    rejected = [name for name in changed if not permitted(name)]
    if not changed or rejected:
        raise ValueError('Source drift exceeds the explicit confirmation migration: '
                         + ', '.join(rejected or ['snapshot identity']) + '; use a fresh study directory')
    write_json(out_dir / 'source-snapshots' / f'{target}.json', snapshot)
    migration = dict(kind=MIGRATION, from_source_sha256=initial, to_source_sha256=target,
                     changed_files=changed, recorded_utc=utc(),
                     extra_reviewed_hashes={name: snapshot['files'][name] for name in changed if name in reviewed},
                     source_epoch_caveat=SOURCE_CAVEAT,
                     nonexecuted_attribution_changes=[name for name in changed
                         if name.removeprefix(prefix).startswith('binding/')
                         and (name.removeprefix(prefix + 'binding/') in NONEXECUTED_BINDING
                              or Path(name).name.startswith('test_'))],
                     reason='Explicit authenticated batch confirmation plus reviewed partial-plan hatch semantic correction: '
                            'missing-load completion remains a full-checker obligation instead of a mandatory hatch instruction violation. '
                            'Additional reviewed hardening covers exact owned ballast-pool cleanup, durable attempt evidence, '
                            'stronger frozen pins and before/after fail-closed provenance checks, an unused measurement identity helper, '
                            'and standing/partial/provenance regression tests. '
                            'Authorized runner B2/B6 presentation/API and provisional default-profile edits leave '
                            'the explicitly scheduled study_profile settings unchanged. '
                            'Engine budgets are unchanged; source epochs are not claimed identical.')
    write_json(out_dir / 'migrations' / f'{initial}-{target}.json', migration)
    write_json(out_dir / 'source-epochs.json', dict(source_epochs=sorted({initial, target}), source_epoch_caveat=SOURCE_CAVEAT))
    return dict(job, source_sha256=target, original_source_sha256=job.get('source_sha256', initial),
                execution_source_sha256=target, source_migration=migration)


def register_migration(out_dir):
    """Publish exact current authorization hashes for already-loaded workers."""
    out_dir = Path(out_dir)
    manifest = json.loads((out_dir / 'jobs.json').read_text())
    write_json(out_dir / 'policy-dependencies.json', policy_dependencies())
    snapshot = source_snapshot()
    runner_and_api = {name: digest for name, digest in snapshot['files'].items()
                      if name.removeprefix('experiments/mvp/pipeline/') in MIGRATION_FILES}
    attribution = {name: digest for name, digest in snapshot['files'].items()
                   if name.startswith('experiments/mvp/pipeline/binding/')
                   and (Path(name).name in NONEXECUTED_BINDING or Path(name).name.startswith('test_'))}
    write_json(out_dir / 'migrations' / 'reviewed-extra-hashes.json',
               dict(kind=MIGRATION, from_source_sha256=manifest['source_sha256'],
                    hashes={**EXTRA_MIGRATION_HASHES, **attribution, **runner_and_api},
                    authorized_runner_hashes={name: digest for name, digest in runner_and_api.items()
                                              if name.startswith('experiments/mvp/pipeline/runner/')},
                    recorded_utc=utc()))
    return migration_provenance(manifest['jobs'][0], out_dir)


def bridge_reviewed_baseline(out_dir):
    """Advance metadata admission only, retaining exact historical manifest bytes."""
    import hashlib
    out_dir = Path(out_dir)
    register_migration(out_dir)  # All changed compute files must already be reviewed.
    snapshot = source_snapshot()
    path = out_dir / 'jobs.json'
    original = path.read_bytes()
    manifest = json.loads(original)
    initial, target = manifest['source_sha256'], snapshot['sha256']
    if initial == target:
        return dict(status='unchanged', source_sha256=target)
    digest = hashlib.sha256(original).hexdigest()
    archive = out_dir / 'manifest-history' / f'{digest}.json'
    archive.parent.mkdir(exist_ok=True)
    if archive.exists():
        if archive.read_bytes() != original:
            raise ValueError('Archived source manifest bytes do not match their hash')
    else:
        with archive.open('xb') as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    activity_path = out_dir / 'activity.json'
    activity = json.loads(activity_path.read_text()) if activity_path.exists() else {}
    affected = []
    for worker in activity.get('active', []):
        log = out_dir / 'logs' / f'{worker["job_id"]}.log'
        if log.exists() and '"status": "waiting_source_review"' in log.read_text():
            affected.append(worker)
    note = dict(kind='reviewed_source_baseline_bridge/1', from_source_sha256=initial,
                to_source_sha256=target, archived_manifest_sha256=digest,
                archived_manifest=str(archive.relative_to(out_dir)), recorded_utc=utc(),
                job_payload_sha256=sha256_json(manifest['jobs']), affected_workers=affected,
                loaded_metadata_version_caveat=LOADED_METADATA_CAVEAT,
                policy_dependencies_sha256=policy_dependencies()['sha256'],
                hatch_policy_epoch_caveat=POLICY_CAVEAT,
                approved_reason='Explicit executive decision: unblock idle legacy source-gate metadata helpers '
                                'without signals, optimizer restarts, job/config changes, or finished-row edits.')
    updated = dict(manifest, original_source_sha256=manifest.get('original_source_sha256', initial),
                   source_sha256=target, source_baseline_migration=note)
    if path.read_bytes() != original:
        raise ValueError('Study manifest changed during source bridge; retry without overwriting new jobs')
    write_json(out_dir / 'source-baseline-bridge.json', note)
    write_json(path, updated)
    # Keep a dedicated bridge record: older loaded summary writers may replace
    # source-epochs.json, but cannot erase this admission history or caveat.
    return dict(status='bridged', **note)


def eligible_auth_failure(row):
    seconds = row.get('total_s')
    return (row.get('status') == 'error' and row.get('message') == AUTH_FAILURE
            and not row.get('confirmation_source') and not row.get('full_pass')
            and row.get('plan') is None and row.get('lifts') is None
            and isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
            and math.isfinite(seconds) and 0 <= seconds < 1
            and isinstance(row.get('reading'), dict)
            and not row.get('evidence')
            and bool(row['reading'].get('sentences'))
            and all(r.get('status') == 'accepted' for r in row['reading']['sentences'] if r.get('role') == 'voyage')
            and row.get('reading_sha256') == sha256_json(row['reading']))


def wait_for_migration_provenance(job, out_dir, *, sleep=time.sleep):
    """Hold the existing slot instead of turning an unaudited code drift into a failure."""
    while True:
        try:
            return migration_provenance(job, out_dir)
        except ValueError as error:
            if 'Source drift exceeds the explicit confirmation migration' not in str(error):
                raise
            print(json.dumps(dict(status='waiting_source_review', job_id=job['job_id'],
                                  message='Binding source changed outside the reviewed migration; waiting without starting an optimizer.')), flush=True)
            sleep(15.)


def repair_auth_failures(out_dir, *, executor=execute_row, max_repairs=None):
    """Repair only the exact obsolete-auth signature inside an existing slot.

    The old reading is evidence only: its process-local authentication cannot be
    transported. execute_row obtains and authenticates a fresh synthetic reading
    against the unchanged, hashed map, then explicitly confirms that receipt.
    """
    out_dir = Path(out_dir)
    manifest_path = out_dir / 'jobs.json'
    if not manifest_path.exists():
        return 0
    jobs = json.loads(manifest_path.read_text())['jobs']
    by_id = {job['job_id']: job for job in jobs}
    ledger_path = out_dir / 'owned-pids.jsonl'
    owned = set()
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            try:
                record = json.loads(line)
                owned.add((record['job_id'], record['pid']))
            except (ValueError, TypeError, KeyError):
                continue
    repaired = 0
    for path in sorted((out_dir / 'rows').glob('*.json')):
        try:
            row = json.loads(path.read_text())
            if not eligible_auth_failure(row):
                continue
            job_id, pid = row['job_id'], row.get('worker_pid')
            if job_id not in by_id or (job_id, pid) not in owned or recorded_pid_alive(pid):
                continue
            claim = out_dir / 'repair-locks' / f'{job_id}.lock'
            claim.parent.mkdir(exist_ok=True)
            try:
                acquire_lock(claim)
            except ValueError:
                continue
            try:
                original = path.read_bytes()
                row = json.loads(original)
                if not eligible_auth_failure(row) or recorded_pid_alive(row['worker_pid']):
                    continue
                job = by_id[job_id]
                if row.get('map_sha256') != job['map_sha256'] or row.get('profile') != job['profile']:
                    continue
                digest = sha256_file(path)
                archive = out_dir / 'archive' / 'unconfirmed-auth' / f'{job_id}-{digest}.json'
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    with archive.open('xb') as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                elif archive.read_bytes() != original:
                    raise ValueError('Authentication-failure archive hash conflict')
                job = wait_for_migration_provenance(job, out_dir)
                job['repair'] = dict(kind=MIGRATION, original_row_sha256=digest,
                                     archived_original=str(archive.relative_to(out_dir)),
                                     original_worker_pid=pid, repair_worker_pid=os.getpid(),
                                     fresh_reader_receipt=True, started_utc=utc())
                write_json(out_dir / 'repair-progress' / f'{job_id}.json', job['repair'])
                executor(job, out_dir)
                repaired += 1
                refresh_summary(out_dir, jobs)
            finally:
                claim.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, KeyError) as error:
            print(json.dumps(dict(repair_skipped=str(path.name), message=f'{type(error).__name__}: {error}')), flush=True)
        if max_repairs is not None and repaired >= max_repairs:
            break
    return repaired


def wait_for_study_tail(out_dir, *, sleep=time.sleep, poll_s=15.):
    """A read-only tail watcher admits no optimizer while old jobs are alive."""
    out_dir = Path(out_dir)
    while True:
        lock = out_dir / 'coordinator.lock'
        alive = False
        if lock.exists():
            try:
                alive = recorded_pid_alive(int(lock.read_text()))
            except (OSError, ValueError):
                alive = True
        if not alive:
            ledger = out_dir / 'owned-pids.jsonl'
            if ledger.exists():
                for line in ledger.read_text().splitlines():
                    try:
                        record = json.loads(line)
                        if not (out_dir / 'rows' / f'{record["job_id"]}.json').exists():
                            alive |= recorded_pid_alive(record['pid'])
                    except (ValueError, TypeError, KeyError):
                        alive = True
            for claim in (out_dir / 'repair-locks').glob('*.lock'):
                try:
                    alive |= recorded_pid_alive(int(claim.read_text()))
                except (OSError, ValueError):
                    alive = True
            if not alive:
                return
        sleep(poll_s)


def acquire_lock(path):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            pid = int(path.read_text())
        except (OSError, ValueError):
            raise ValueError('Malformed coordinator lock; choose a fresh study output directory') from None
        if recorded_pid_alive(pid):
            raise ValueError('This study coordinator is still running; use its checkpoints or a fresh output directory')
        path.unlink()
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)


def coordinate(out_dir, *, labels=('60', '120', '300', 'default'), max_maps=None,
               timeout_s=7200., poll_s=5., dev_root=DEV, state_path=STATE):
    out_dir = Path(out_dir).resolve()
    if not out_dir.is_relative_to(HERE):
        raise ValueError('Study output must stay inside runner/')
    if Path(dev_root).resolve() != DEV.resolve():
        raise ValueError('Only the approved development folder is allowed')
    out_dir.mkdir(parents=True, exist_ok=True)
    lock_path = out_dir / 'coordinator.lock'
    acquire_lock(lock_path)
    jobs = []
    active = {}
    known = set()
    detached = {}
    samples = []
    pending = deque()
    try:
        # A fresh namespace prevents older unrelated grid files from changing
        # the native newest-mtime dispatch of the pinned numerical snapshot.
        from .cache_seed import seed
        seed(cache_root=out_dir / 'cache', report_path=out_dir / 'cache-seeding.json')
        snapshot = source_snapshot()
        snapshot_path = out_dir / 'source-snapshots' / f'{snapshot["sha256"]}.json'
        if not snapshot_path.exists():
            write_json(snapshot_path, snapshot)
        manifest_path = out_dir / 'jobs.json'
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text())
            if previous['labels'] != list(labels) or previous['timeout_s'] != timeout_s:
                raise ValueError('Resume requires identical budgets and emergency deadline')
            jobs = previous['jobs']
            if previous.get('source_sha256') != snapshot['sha256']:
                migration_path = out_dir / 'migrations' / f'{previous.get("source_sha256")}-{snapshot["sha256"]}.json'
                migration = json.loads(migration_path.read_text()) if migration_path.exists() else {}
                if (migration.get('kind') != MIGRATION
                        or migration.get('from_source_sha256') != previous.get('source_sha256')
                        or migration.get('to_source_sha256') != snapshot['sha256']):
                    raise ValueError('Study source changed outside a recorded confirmation migration; use a fresh directory')
            known = {j['map'] for j in jobs}
            ledger = out_dir / 'owned-pids.jsonl'
            recorded = {}
            if ledger.exists():
                for line in ledger.read_text().splitlines():
                    try:
                        item = json.loads(line)
                        recorded[item['job_id']] = item['pid']
                    except (ValueError, KeyError, TypeError):
                        continue
            for job in jobs:
                if (out_dir / 'rows' / f'{job["job_id"]}.json').exists():
                    continue
                pid = recorded.get(job['job_id'])
                if pid and recorded_pid_alive(pid):
                    detached[pid] = job
                else:
                    pending.append(job)

        def discover():
            paths = map_order(dev_maps(dev_root))
            if max_maps is not None:
                paths = paths[:max_maps]
            fresh = [p for p in paths if str(p.relative_to(ROOT)) not in known]
            if not fresh:
                return
            cohort = dict(created_utc=utc(), maps=[dict(path=str(p.relative_to(ROOT)), sha256=sha256_file(p)) for p in fresh])
            digest = sha256_json(cohort)
            write_json(out_dir / 'cohorts' / f'{digest}.json', cohort)
            added = make_jobs(fresh, labels, cohort_sha256=digest, timeout_s=timeout_s)
            for job in added:
                job['source_sha256'] = snapshot['sha256']
            jobs.extend(added)
            pending.extend(added)
            known.update(j['map'] for j in added)
            write_json(manifest_path, dict(labels=list(labels), timeout_s=timeout_s, jobs=jobs,
                                          source_sha256=snapshot['sha256']))

        discover()
        if not jobs:
            raise ValueError('No development maps are available')
        write_json(out_dir / 'coordinator.json', dict(pid=os.getpid(), started_utc=utc(), status='running',
                   base_workers=4, quiet_workers=6, quiet_condition='Three fresh samples below load1 30 over at least 60 seconds',
                   load_context='GPU job uses six cores; concurrent engine benchmark until about21:00; quiet-machine rerun needed'))
        last_sample = -math.inf
        while pending or active or detached:
            now = time.monotonic()
            if now - last_sample >= 30:
                current = dict(telemetry(state_path), sampled_monotonic=now)
                samples.append(current)
                samples = samples[-3:]
                last_sample = now
            current = telemetry(state_path)
            limit = worker_limit(samples)
            if not current.get('valid') or current.get('load1', math.inf) >= 30:
                limit = 4
            for pid, job in list(detached.items()):
                completed = (out_dir / 'rows' / f'{job["job_id"]}.json').exists()
                if completed or not recorded_pid_alive(pid):
                    del detached[pid]
                    if not completed:
                        pending.appendleft(job)
                    refresh_summary(out_dir, jobs)
            for pid, (process, stream, job) in list(active.items()):
                code = process.poll()
                if code is None:
                    continue
                stream.close()
                if not (out_dir / 'rows' / f'{job["job_id"]}.json').exists():
                    progress = out_dir / 'progress' / f'{job["job_id"]}.json'
                    row = json.loads(progress.read_text()) if progress.exists() else dict(job)
                    row.update(status='interrupted', full_pass=False, passed=False, censored=True,
                               message=f'Owned study worker exited with code {code}; outcome unknown.', finished_utc=utc(), end=telemetry(state_path))
                    write_json(out_dir / 'rows' / f'{job["job_id"]}.json', row)
                del active[pid]
                refresh_summary(out_dir, jobs)
                print(json.dumps(dict(finished=job['job_id'], exitcode=code, remaining=len(pending), active=len(active))), flush=True)
            discover()
            if current.get('valid') and current['enabled_cores'] >= 8 and current['load1'] <= 45:
                while pending and len(active) + len(detached) < limit:
                    job = pending.popleft()
                    spec = out_dir / 'specs' / f'{job["job_id"]}.json'
                    write_json(spec, job)
                    log = out_dir / 'logs' / f'{job["job_id"]}.log'
                    log.parent.mkdir(exist_ok=True)
                    stream = log.open('a')
                    process = subprocess.Popen([sys.executable, '-m', 'experiments.mvp.pipeline.runner.profile_study',
                        '--worker', str(spec), '--out', str(out_dir)], cwd=ROOT, stdin=subprocess.DEVNULL,
                        stdout=stream, stderr=subprocess.STDOUT)
                    active[process.pid] = (process, stream, job)
                    with (out_dir / 'owned-pids.jsonl').open('a') as ledger:
                        ledger.write(json.dumps(dict(pid=process.pid, job_id=job['job_id'], started_utc=utc())) + '\n')
                    print(json.dumps(dict(started=job['job_id'], pid=process.pid, active=len(active), worker_limit=limit,
                                          load1=current['load1'], enabled_cores=current['enabled_cores'])), flush=True)
            write_json(out_dir / 'activity.json', dict(updated_utc=utc(), active=[dict(pid=p, job_id=v[2]['job_id']) for p, v in active.items()],
                       detached=[dict(pid=p, job_id=j['job_id']) for p, j in detached.items()],
                       pending=len(pending), expected=len(jobs), telemetry=current, worker_limit=limit))
            if pending or active or detached:
                time.sleep(poll_s)
        refresh_summary(out_dir, jobs)
        write_json(out_dir / 'finished.json', dict(finished_utc=utc(), expected=len(jobs), status='complete'))
    finally:
        # Never signal processes from a previous coordinator or another owner.
        # Active jobs keep their per-row checkpoints if the coordinator exits.
        lock_path.unlink(missing_ok=True)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cli.add_argument('--out', type=Path, required=True)
    cli.add_argument('--budgets', nargs='+', default=['60', '120', '300', 'default'])
    cli.add_argument('--max-maps', type=int)
    cli.add_argument('--timeout', type=float, default=7200., help='Emergency censoring cap, not a profile budget')
    cli.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    cli.add_argument('--repair-tail', action='store_true', help='Wait until the original study and owned workers end, then repair exact authentication failures sequentially')
    cli.add_argument('--register-confirmation-migration', action='store_true', help='Record only the explicitly permitted confirmation source migration; launch no optimizer')
    cli.add_argument('--bridge-reviewed-baseline', action='store_true', help='Archive the current manifest and advance only its reviewed source-admission baseline')
    args = cli.parse_args(argv)
    try:
        if args.bridge_reviewed_baseline:
            print(json.dumps(bridge_reviewed_baseline(args.out)), flush=True)
        elif args.register_confirmation_migration:
            register_migration(args.out)
        elif args.repair_tail:
            wait_for_study_tail(args.out)
            repair_auth_failures(args.out)
        elif args.worker:
            job = wait_for_migration_provenance(json.loads(args.worker.read_text()), args.out)
            repair_auth_failures(args.out, max_repairs=1)
            execute_row(job, args.out)
            repair_auth_failures(args.out)
        else:
            if args.max_maps is not None and args.max_maps < 1:
                raise ValueError('--max-maps must be positive')
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise ValueError('--timeout must be positive finite seconds')
            for label in args.budgets:
                study_profile(label)
            coordinate(args.out, labels=tuple(args.budgets), max_maps=args.max_maps, timeout_s=args.timeout)
        return 0
    except Exception as error:
        print(json.dumps(dict(status='error', message=f'Profile study could not run ({type(error).__name__}): {error}')), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
