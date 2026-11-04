"""Frozen-epoch gold campaign. Each instance is an independently resumable shard.

Historical tiny-gold@1.0 envelopes retain compatibility with the existing gold
writer and SFT admission; campaign membership and epoch pins extend that format.
No model, GPU, cloud call, or optimization approximation is used by this adapter.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import json
from eval.snapshot_io import read_snapshot
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNGS = ('tiny', 'small', 'medium', 'full')


def module_at(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def tiny():
    return module_at('_campaign_gold_tiny', HERE.parent / 'tiny/driver.py')


def machinery():
    return module_at('_campaign_gold_g1', ROOT / 'experiments/campaign-g1/campaign.py')


def freeze_check(args):
    return machinery().freeze_check(args)


def sha(path):
    return machinery().sha(path)


def atomic_json(path, value):
    return machinery().atomic_json(path, value)


def load_inventory_pinned(path):
    from eval.splits_inventory import FrozenInventory
    snapshot = read_snapshot(path)
    manifest, digest = snapshot.json(), snapshot.sha256
    return FrozenInventory(manifest), digest


def cache_fence(output=None):
    """Install the same pinned cache snapshot in workers and spawn children."""
    if output is not None:
        os.environ['CAMPAIGN_CACHE_ROOT'] = str(output)
    adapter = module_at('_campaign_gold_cache', ROOT / 'experiments/campaign-g1/cache_adapter.py')
    adapter.HERE = HERE
    adapter.ROOT = ROOT
    adapter.redirect_cache(Path(os.environ['CAMPAIGN_CACHE_ROOT']))
    adapter.enforce_scope()


def budget_map(values, default, rungs):
    """Accept repeated RUNG=SECONDS or a single SECONDS applying to all rungs."""
    result = {rung: default for rung in RUNGS}
    for value in values or []:
        if '=' in value:
            rung, raw = value.split('=', 1)
            if rung not in RUNGS:
                raise ValueError(f'unknown budget rung: {rung}')
            result[rung] = float(raw)
        else:
            result.update({rung: float(value) for rung in RUNGS})
    if any(not math.isfinite(result[rung]) or result[rung] <= 0 for rung in rungs):
        raise ValueError('per-rung solve budgets must be finite positive seconds')
    return result


def select_inputs(args, frozen):
    """Shared epoch validator owns source, allocation and spec/content binding."""
    from eval.splits_epoch import validate_epoch_manifest, validate_epoch_instance
    inventory, split_digest = load_inventory_pinned(args.split_manifest)
    root = args.instance_root.resolve()
    manifest_path = root / 'manifest.json'
    snapshot = read_snapshot(manifest_path)
    manifest, manifest_digest = snapshot.json(), snapshot.sha256
    embedded = manifest.get('freeze_manifest') or {}
    transport_keys = {'drift', '_manifest_sha256'}
    if manifest.get('epoch_id') != frozen['epoch_id'] or {k: v for k, v in embedded.items() if k not in transport_keys} != {k: v for k, v in frozen.items() if k not in transport_keys}:
        raise ValueError('instance epoch/freeze differs from selected freeze manifest')
    validate_epoch_manifest(root, manifest, inventory, allow_diagnostic=args.diagnostic,
                            check_source=not args.allow_drift)
    if not args.diagnostic:
        machinery().require_evidence(manifest)
    selected = []
    for rung in args.rungs:
        rows = []
        for group, details in sorted(manifest['groups'].items()):
            for row in sorted(details['shards'], key=lambda row: row['spec_key']):
                if row['split'] not in args.split or row['split_spec']['rung'] != rung:
                    continue
                path = (root / group / row['instance']).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('input escapes epoch root')
                rows.append(dict(path=str(path), sha256=row['sha256'], rung=rung,
                    group=group, shard=row))
        # The selected denominator is fixed before solving; never filter on outcomes.
        chosen = []
        for split in args.split:
            eligible = [row for row in rows if row['shard']['split'] == split]
            count = getattr(args, 'rung_counts', {}).get(rung, args.count) if split == 'train' else args.count
            if count is not None:
                if len(eligible) < count:
                    raise ValueError(f'{split}/{rung}: requested {count} inputs but found {len(eligible)}')
                eligible = eligible[:count]
            if not eligible:
                raise ValueError(f'no {split}/{rung} epoch inputs')
            chosen.extend(eligible)
        rows = chosen
        if not rows:
            raise ValueError(f'no {args.split}/{rung} epoch inputs')
        for index, row in enumerate(rows):
            if index % args.shard_count == args.shard_index:
                instance=validate_epoch_instance(row['path'], row['shard'])
                spec=row['shard']['split_spec']
                row['family']=machinery().family_identity({'input':row})
                audit_path=Path(row['path']).with_suffix('.audit.json')
                if audit_path.is_file():
                    audit=read_snapshot(audit_path);row.update(audit_path=str(audit_path),audit_sha256=audit.sha256)
                row['demographics']=input_demographics(instance,row)
                row['rule_binding']=tiny().rule_binding().observe(instance)
                selected.append(row)
    if not selected:
        raise ValueError('selected shard partition is empty')
    args._validated_instance_manifest_sha256 = manifest_digest
    args._validated_split_manifest_sha256 = split_digest
    return selected, manifest_path, inventory


def rule_policy():
    return module_at('_campaign_gold_rules', ROOT/'experiments/campaign-g1/rule_policy.py')


def migration_evidence():
    return module_at('_campaign_gold_migration',ROOT/'experiments/campaign-g1/migration_evidence.py')


def publication_lineage(g1_summary, diagnostic=False):
    mode='diagnostic' if diagnostic else 'production' if g1_summary else 'pipeline_test'
    return dict(data_mode=mode,purpose='diagnostic' if diagnostic else 'sft_training' if g1_summary else 'tiny_g1_exception_pipeline_test',
                production_eligible=mode=='production')


def input_demographics(instance, row):
    """Recorded input characteristics; absent audit evidence remains unknown."""
    values=dict(box_count=len(instance.containers),dg_count=sum(box.dg is not None for box in instance.containers),
                background_b23_ratio=None,arrival_gm_m=None)
    if row.get('audit_path'):
        audit=read_snapshot(row['audit_path'],expected_sha256=row['audit_sha256']).json()
        metrics=(audit.get('background_last') or {}).get('states',{}).get('arrival',{})
        for destination,source in [('background_b23_ratio','b23_ratio'),('arrival_gm_m','gm_m')]:
            value=metrics.get(source)
            if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):values[destination]=value
    return values


def require_g1(args, epoch):
    if args.diagnostic:
        return None
    if args.rungs == ['tiny'] and args.g1_summary is None:
        return None  # Existing 6.1 tiny-first exemption; physical gates still apply.
    if args.g1_summary is None:
        raise ValueError('production gold requires --g1-summary with a same-epoch leader-approved g1_met=true decision')
    snapshot = read_snapshot(args.g1_summary)
    report, digest = snapshot.json(), snapshot.sha256
    machinery().require_evidence(report)
    if report.get('epoch_id') != epoch or report.get('g1_met') is not True:
        raise ValueError('production gold requires a passed G1 decision in the selected frozen epoch')
    approval=rule_policy().validate_g1_approval(report,epoch,args.rungs)
    return dict(path=str(args.g1_summary.resolve()),sha256=digest,approval=approval)


def _clean_candidate(instance, row, config):
    t = tiny()
    t._require_clean(instance)
    if not t._full_hull(instance):
        raise ValueError('full hull geometry required')
    # The production helper owns watchdogs, ballast, bound full checker, D50,
    # structured actions, C2-on token replay, and cross-phase source checks.
    scalable = t._safe_judge(instance, 'scalable', config)
    exact = (t._safe_judge(instance, 'exact', config) if row['rung'] in ('tiny', 'small')
             else dict(accepted=False, status='not_scheduled', reason='exact limited to tiny/small'))
    candidates = [(kind, result) for kind, result in [('scalable', scalable), ('exact', exact)]
                  if result.get('accepted')]
    # Compare only checked complete witnesses; ties prefer exact for its proof metadata.
    source, candidate = min(candidates, key=lambda pair: (pair[1]['objective'], pair[0] != 'exact')) if candidates else ('scalable', scalable)
    return scalable, exact, source, candidate


def worker(job_path):
    """Subprocess entry point; all cache writes are scoped under campaign/."""
    def interrupted(signum, frame):
        raise TimeoutError('owned gold worker received termination; unwinding phase watchdogs')
    signal.signal(signal.SIGTERM, interrupted)
    job = read_snapshot(job_path).json()
    cache_fence(Path(job['output']) / 'cache-work')
    started = time.monotonic()
    row = job['row']
    t = tiny()
    config = t.Config(output=Path(job['output']), scalable_s=job['scalable_s'], search_s=job['exact_s'],
        finalize_s=job['ballast_s'], checker_s=job['check_s'], seed=job['seed'], max_nodes=job['max_nodes'],
        hard_stop_at=(datetime.now(timezone.utc) + timedelta(seconds=job['timeout_s'])).isoformat(),
        split_manifest=Path(job['split_manifest']))
    policy_pin=rule_policy().enforce_rule_policy(ROOT,job.get('required_rules'))
    if not job.get('required_rules'):raise ValueError('gold worker requires frozen required_rules pin')
    components=migration_evidence().assert_component_epoch({'component_epoch':job.get('component_epoch')})
    common = dict(format='gold-campaign-attempt@1.0', accepted=False, spec_key=row['shard']['spec_key'],
        attempt_id=job['attempt_id'],
        split=row['shard']['split'], rung=row['rung'], group=row['group'], input_sha256=row['sha256'],
        epoch_id=job['epoch_id'], diagnostic=job['diagnostic'], evidence_eligible=not job['diagnostic'],
        label='diagnostic, not evidence' if job['diagnostic'] else 'frozen-source gold',
        split_version='v2', split_entries_sha256=job['split_entries_sha256'],
        component_versions=t.component_versions(), relevant_source_sha256=t.source_fingerprint()['sha256'], c2='on/default',
        required_rules=policy_pin,component_epoch=components,g1_approval=(job.get('g1_summary') or {}).get('approval'),
        family=row.get('family'),demographics=row.get('demographics'),
        **publication_lineage(job.get('g1_summary'),job['diagnostic']))
    try:
        from eval.splits_epoch import validate_epoch_instance
        instance = validate_epoch_instance(row['path'], row['shard'])
        common['rule_binding']=t.rule_binding().observe(instance)
        if common['data_mode']=='production':rule_policy().validate_g1_approval(job['g1_summary']['approval'],job['epoch_id'],[row['rung']])
        scalable, exact, source, candidate = _clean_candidate(instance, row, config)
        accepted = candidate.get('accepted', False)
        e, s = exact.get('objective'), scalable.get('objective')
        gap = (s-e)/s if e is not None and s else (0. if e == s == 0 else None)
        optimal = source == 'exact' and exact.get('raw_status') != 'timeout' and exact.get('metrics', {}).get('optimality_status') == 'OPTIMAL'
        common.update(instance_id=instance.id, scalable=scalable, exact=exact, gold_source=source,
            status=('optimal_engine' if optimal else 'feasible') if accepted else candidate.get('status', 'error'),
            reason=candidate.get('error') or candidate.get('message') or candidate.get('status'),
            candidate_accepted=accepted, quality_gap=gap, optimal_quality_gap=gap if optimal else None,
            quality_gap_formula='(scalable_checked-exact_checked)/scalable_checked; best-found unless exact OPTIMAL',
            quality_gap_status='comparison_available' if gap is not None else 'unavailable',
            quality_gap_unavailable_reason=None if gap is not None else 'no two accepted complete candidates',
            mask_verified=candidate.get('mask_verified', False), shippable=candidate.get('shippable', False))
        common['rule_binding']=candidate.get('rule_binding') or common['rule_binding']
        if accepted:
            migration_evidence().assert_component_epoch({'component_epoch':job['component_epoch']})
            if sha(row['path']) != row['sha256']:
                raise ValueError('instance changed during solving')
            if t.source_fingerprint()['sha256'] != common['relevant_source_sha256']:
                raise ValueError('source changed during solving')
            artifact = dict(common, format='tiny-gold@1.0', accepted=not job['diagnostic'],
                plan=candidate['plan'], check_report=candidate['report'], objective=candidate['objective'],
                actions_schema=candidate['plan']['action_space'], gold_status='optimal_engine' if optimal else 'feasible',
                optimality_scope='engine weighted lifts; tertiary margins not certified' if optimal else 'feasible incumbent; no optimum certified',
                tiny_g1_exception=job.get('g1_summary') is None and row['rung'] == 'tiny',
                production_g1_required=row['rung'] != 'tiny', g1_summary=job.get('g1_summary'),
                split_spec=row['shard']['split_spec'], input_relative_path=str(Path(row['group']) / row['shard']['instance']),
                manifest_sha256=job['instance_manifest_sha256'])
            destination = Path(job['output']) / ('diagnostics' if job['diagnostic'] else 'plans') / (row['shard']['spec_key'] + '.json')
            if job['diagnostic']:
                artifact.update(format='gold-campaign-diagnostic@1.0', diagnostic_accepted=True)
            checksum = t.atomic_json(destination, artifact)
            read_snapshot(destination, expected_sha256=checksum)
            common.update(accepted=not job['diagnostic'], diagnostic_accepted=job['diagnostic'],
                **({'diagnostic_file': str(destination), 'diagnostic_sha256': checksum} if job['diagnostic'] else {'gold_file': str(destination), 'gold_sha256': checksum}))
    except Exception as error:
        common.update(accepted=False, status='timeout' if isinstance(error, TimeoutError) else 'error',
                      reason=f'{type(error).__name__}: {error}')
    finally:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()  # Stops only the public helper's owned idle child.
    common.update(wall_time_s=time.monotonic()-started,
        peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**20 if sys.platform == 'darwin' else 1024))
    atomic_json(job['result'], common)
    return 0


def run_shard(args, identity, row, output):
    g1 = machinery()
    frozen = freeze_check(args)
    g1.runner_check(args, identity)
    key = row['shard']['spec_key']
    directory = output / 'shards' / key
    marker = directory / 'done.json'
    shard_identity = dict(campaign=identity, input=row)
    if marker.exists():
        result = g1.verified_done(marker, shard_identity)
        if result is None:
            raise ValueError(f'done marker or result drift: {marker}')
        for path_key, hash_key in [('gold_file', 'gold_sha256'), ('diagnostic_file', 'diagnostic_sha256')]:
            if result.get(path_key) and (not Path(result[path_key]).is_file() or sha(result[path_key]) != result.get(hash_key)):
                raise ValueError('published gold drift on resume')
        atomic_json(output / 'state' / (key+'.json'), result)
        return {**result, 'resumed': True}
    g1.ensure_identity(directory / 'identity.json', shard_identity)
    # A worker may have published its candidate before the interrupted parent
    # committed a checkpoint. Neither that row nor that envelope can be reused.
    attempt_id = g1.quarantine_attempt(directory, [output / 'state' / (key+'.json'),
        output / 'plans' / (key+'.json'), output / 'diagnostics' / (key+'.json')])
    timeout = args.timeout_s or (args.scalable_budgets[row['rung']] +
        (args.exact_budgets[row['rung']] if row['rung'] in ('tiny', 'small') else 0.) + 2*(args.ballast_s+2*args.check_s)+30.)
    result_path = directory / 'result.json'
    job = dict(row=row, output=str(output), result=str(result_path), epoch_id=identity['epoch_id'],
        attempt_id=attempt_id,
        scalable_s=args.scalable_budgets[row['rung']], exact_s=args.exact_budgets[row['rung']],
        ballast_s=args.ballast_s, check_s=args.check_s, timeout_s=timeout,
        split_manifest=str(args.split_manifest.resolve()), diagnostic=args.diagnostic,
        seed=args.seed, max_nodes=args.max_nodes, split_entries_sha256=identity['split_entries_sha256'],
        instance_manifest_sha256=identity['instance_manifest_sha256'],g1_summary=identity['g1_summary'],
        required_rules=identity.get('required_rules'),component_epoch=identity.get('component_epoch'))
    atomic_json(directory / 'job.json', job)
    started = time.monotonic()
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', CAMPAIGN_ATTEMPT_ID=attempt_id)
    if frozen and frozen.get('numerical_caches'):
        transport = module_at('_campaign_gold_pin_transport', ROOT / 'experiments/campaign-g1/pin_transport.py')
        environment = transport.pin_environment(frozen['numerical_caches'], output, environment)
    with (directory / 'worker.log').open('a') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker-job', str(directory / 'job.json')],
            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        atomic_json(directory / 'pid.json', dict(pid=process.pid, owner_pid=os.getpid(),
            started_unix_s=time.time(), command='gold.py --worker-job', spec_key=key))
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout+10.)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Only this Popen's recorded PID is stopped; phase watchdogs own their children.
            process.terminate()
            try:
                returncode = process.wait(timeout=2.)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait()
    eligible = False
    reason = None
    if result_path.exists() and returncode == 0 and not timed_out:
        try:
            result = read_snapshot(result_path).json()
            if not isinstance(result, dict):
                raise ValueError('result must be an object')
            if result.get('attempt_id') != attempt_id:
                raise ValueError('result belongs to another/missing child attempt')
            if not args.diagnostic:
                g1.require_evidence(result)
            for path_key, hash_key in [('gold_file', 'gold_sha256'), ('diagnostic_file', 'diagnostic_sha256')]:
                if result.get(path_key):
                    artifact = Path(result[path_key]).resolve()
                    if not artifact.is_relative_to(output.resolve()) or sha(artifact) != result.get(hash_key):
                        raise ValueError('returned publication artifact missing, outside output, or changed')
            if result.get('accepted') and not result.get('gold_file'):
                raise ValueError('accepted gold has no verified publication artifact')
            eligible = True
        except (OSError, ValueError, TypeError) as exc:
            reason = f'invalid current-attempt result: {exc}'
    if not eligible:
        result = dict(accepted=False, status='timeout' if timed_out else 'worker_error',
            reason=reason or ('outer shard watchdog expired' if timed_out else f'worker exit {returncode}; no valid current-attempt result'),
            spec_key=key, split=row['shard']['split'], rung=row['rung'], diagnostic=args.diagnostic,
            evidence_eligible=False,input_sha256=row['sha256'],family=row.get('family'),demographics=row.get('demographics'),
            rule_binding=row.get('rule_binding'),
            **publication_lineage(identity.get('g1_summary'),args.diagnostic))
        # A failed worker may still have left an envelope. It has no accepted
        # state and cannot be published; retain it solely as attempt audit.
        quarantine = directory / 'quarantine' / attempt_id
        for category in ('plans', 'diagnostics'):
            artifact = output / category / (key+'.json')
            if artifact.exists():
                quarantine.mkdir(parents=True, exist_ok=True)
                artifact.replace(quarantine / f'{category}-{key}.json')
    result.update(process_wall_s=time.monotonic()-started, worker_returncode=returncode, worker_pid=process.pid,
                  configured_wall_cap_s=timeout, resumed=False, attempt_id=attempt_id)
    freeze_check(args)
    g1.runner_check(args, identity)
    result_digest = atomic_json(result_path, result)
    # Every observed terminal outcome, including timeouts, is checkpointed. An
    # interrupted parent leaves no marker and retries that one shard on resume.
    extra_paths = [result[name] for name in ('gold_file', 'diagnostic_file') if result.get(name)]
    g1.write_done(marker, shard_identity, result_path, extra_paths, expected_result_sha256=result_digest)
    persisted = g1.verified_done(marker, shard_identity)
    if persisted is None:
        raise ValueError(f'failed artifact checkpoint verification: {marker}')
    atomic_json(output / 'state' / (key+'.json'), persisted)
    return persisted


def summarize(rows, identity):
    stats = {}
    for rung in RUNGS:
        selected = [row for row in rows if row['rung'] == rung]
        if not selected:
            continue
        elapsed = sum(row.get('process_wall_s', 0.) for row in selected)
        accepted = sum(bool(row.get('accepted')) and not row.get('diagnostic')
            and row.get('evidence_eligible') is not False for row in selected)
        candidates = sum(row.get('candidate_accepted', False) for row in selected)
        stats[rung] = dict(attempts=len(selected), accepted=accepted, candidate_accepted=candidates,
            solve_rate=accepted/len(selected), candidate_solve_rate=candidates/len(selected),
            total_core_s=elapsed, mean_attempt_s=elapsed/len(selected),
            outcomes=dict(Counter(row['status'] for row in selected)),
            reasons=dict(Counter(str(row.get('reason')) for row in selected if not row.get('accepted'))),
            resumed=sum(bool(row.get('resumed')) for row in selected),
            solver_attempts={kind: sum(row.get(kind, {}).get('status') not in (None, 'not_scheduled') for row in selected)
                             for kind in ('scalable', 'exact')},
            solver_timeouts={kind: sum(row.get(kind, {}).get('status') == 'timeout' or row.get(kind, {}).get('raw_status') == 'timeout' for row in selected)
                             for kind in ('scalable', 'exact')})
    families={}
    for row in rows:
        family=row.get('family') or {}
        key=json.dumps(dict(rung=row['rung'],**family),sort_keys=True,separators=(',',':'))
        item=families.setdefault(key,dict(rung=row['rung'],family=family,attempted=0,published=0))
        item['attempted']+=1
        item['published']+=int(bool(row.get('accepted')) and not row.get('diagnostic') and row.get('evidence_eligible') is not False)
    for item in families.values():item['solve_rate']=item['published']/item['attempted']
    comparisons={}
    for key in ('box_count','dg_count','background_b23_ratio','arrival_gm_m'):
        comparisons[key]={}
        for population in ('attempted','published'):
            chosen=[row for row in rows if population=='attempted' or bool(row.get('accepted')) and not row.get('diagnostic') and row.get('evidence_eligible') is not False]
            values=[(row.get('demographics') or {}).get(key) for row in chosen]
            known=[value for value in values if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)]
            comparisons[key][population]=dict(n=len(chosen),known=len(known),unknown=len(chosen)-len(known),
                mean=sum(known)/len(known) if known else None,min=min(known) if known else None,max=max(known) if known else None)
    return dict(epoch_id=identity['epoch_id'], diagnostic=identity['diagnostic'],
        label='diagnostic, not evidence' if identity['diagnostic'] else 'frozen-source gold',
        evidence_eligible=not identity['diagnostic'] and all(
            not row.get('diagnostic') and row.get('evidence_eligible') is not False for row in rows), attempts=len(rows),
        accepted=sum(stats[rung]['accepted'] for rung in stats),per_rung=stats,per_rung_family=families,attempted_vs_published=comparisons,
        rule_binding_yield=tiny().rule_binding().summarize(rows),
        distribution_note='Gold is conditional on solver success. Box/DG counts cover explicit containers; B2.3 ratio and arrival GM are input background-witness audit values. Missing audit metrics remain unknown.',
        **publication_lineage(identity.get('g1_summary'),identity['diagnostic']),
        selected_denominator=len(identity.get('inputs', rows)), pending=len(identity.get('inputs', rows))-len(rows),
        calibration='Measured complete attempts, including failures/timeouts; do not extrapolate between rungs.')


def publication_checkpoint_check(output, identity):
    """The existing writer consumes state; admit only checkpoint-backed states."""
    selected = {row['shard']['spec_key']: row for row in identity['inputs']}
    for state in (output / 'state').glob('*.json'):
        value = read_snapshot(state).json()
        if not value.get('accepted'):
            continue
        row = selected.get(state.stem)
        if row is None:
            raise ValueError(f'published state has no selected input: {state}')
        marker = output / 'shards' / state.stem / 'done.json'
        expected = dict(campaign=identity, input=row)
        committed = machinery().verified_done(marker, expected)
        if committed is None:
            raise ValueError(f'published state has no valid done marker/artifact inventory: {state}')
        if value != committed:
            raise ValueError(f'published state differs from checkpoint result: {state}')
        machinery().require_evidence(value)


def calibration_selection(args, identity, rows, output):
    """Keep the full run identity, executing only the next pending calibration.

    Operational flags are deliberately absent from identity: dropping the flag
    resumes the same campaign, including its measured shard and publication.
    """
    if not args.calibrate_first_shard:
        return rows, []
    completed, pending = [], []
    for row in rows:
        marker = output / 'shards' / row['shard']['spec_key'] / 'done.json'
        shard_identity = dict(campaign=identity, input=row)
        if marker.exists():
            result = machinery().verified_done(marker, shard_identity)
            if result is None:
                raise ValueError(f'done marker or result drift: {marker}')
            for path_key, hash_key in [('gold_file', 'gold_sha256'), ('diagnostic_file', 'diagnostic_sha256')]:
                if result.get(path_key) and (not Path(result[path_key]).is_file() or sha(result[path_key]) != result.get(hash_key)):
                    raise ValueError('published gold drift on calibration resume')
            # The parent can stop after checkpoint commit and before publishing
            # state. Rebuild exactly the same writer input as ordinary resume.
            atomic_json(output / 'state' / (row['shard']['spec_key']+'.json'), result)
            completed.append({**result, 'resumed': True})
        elif args.calibration_rung is None or row['rung'] == args.calibration_rung:
            pending.append(row)
    return pending[:1], completed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-job', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--freeze-manifest', type=Path)
    parser.add_argument('--instance-root', type=Path)
    parser.add_argument('--split-manifest', type=Path, default=ROOT / 'experiments/splits/v2/manifest.json')
    parser.add_argument('--g1-summary', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--split', choices=('train', 'val', 'test'), nargs='+', default=['train'])
    parser.add_argument('--rungs', choices=RUNGS, nargs='+', default=['tiny'])
    parser.add_argument('--count', type=int, help='first N instances per split/rung; default all')
    parser.add_argument('--rung-count', action='append', default=[], help='RUNG=N training mix override; repeat per rung')
    parser.add_argument('--scalable-s', action='append', help='SECONDS or RUNG=SECONDS; repeat per rung, default 60')
    parser.add_argument('--exact-s', action='append', help='SECONDS or RUNG=SECONDS; repeat per rung, default 60')
    parser.add_argument('--ballast-s', type=float, default=30.)
    parser.add_argument('--check-s', type=float, default=30.)
    parser.add_argument('--timeout-s', type=float)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=1)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--max-nodes', type=int, default=100000)
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--allow-drift', action='store_true')
    parser.add_argument('--estimate-only', action='store_true')
    parser.add_argument('--calibrate-first-shard', action='store_true', help='run next pending shard then stop; omit on same-command resume to run all')
    parser.add_argument('--calibration-rung', choices=RUNGS, help='optional rung for --calibrate-first-shard; operational, not an identity change')
    args = parser.parse_args(argv)
    if args.worker_job:
        return worker(args.worker_job)
    machinery().validate_modes(args.diagnostic, args.allow_drift)
    if not args.freeze_manifest or not args.instance_root or not args.output:
        parser.error('--freeze-manifest, --instance-root and --output are required')
    if args.count is not None and args.count < 1 or args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error('positive count/shard-count and 0 <= shard-index < shard-count required')
    if len(args.rungs) != len(set(args.rungs)):
        parser.error('duplicate rungs')
    if len(args.split) != len(set(args.split)):
        parser.error('duplicate splits')
    if args.calibration_rung and (not args.calibrate_first_shard or args.calibration_rung not in args.rungs):
        parser.error('--calibration-rung requires --calibrate-first-shard and a selected rung')
    for key in ('ballast_s', 'check_s', 'timeout_s'):
        value = getattr(args, key)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error('budgets must be positive finite seconds')
    args.scalable_budgets = budget_map(args.scalable_s, 60., args.rungs)
    args.exact_budgets = budget_map(args.exact_s, 60., args.rungs)
    args.rung_counts = {}
    for setting in args.rung_count:
        rung, raw = setting.split('=', 1)
        if rung not in args.rungs or int(raw) < 1:
            parser.error('--rung-count requires a selected RUNG and positive integer N')
        args.rung_counts[rung] = int(raw)
    output = args.output.resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        raise ValueError('output must be a dedicated directory inside gold-plans/campaign')
    frozen = freeze_check(args)
    if not frozen.get('required_rules'):raise ValueError('frozen epoch lacks required_rules policy; create a new freeze')
    required_rules=rule_policy().enforce_rule_policy(ROOT,frozen['required_rules'])
    components=migration_evidence().assert_component_epoch(frozen)
    g1_summary = require_g1(args, frozen['epoch_id'])
    rows, manifest_path, inventory = select_inputs(args, frozen)
    t = tiny()
    scripts = [Path(__file__).resolve(), HERE/'rule_binding.py', HERE.parent / 'tiny/driver.py',
               ROOT / 'experiments/campaign-g1/campaign.py', ROOT / 'experiments/campaign-g1/cache_adapter.py',
               ROOT / 'experiments/campaign-g1/pin_transport.py',
               ROOT / 'experiments/campaign-g1/rule_policy.py',ROOT / 'experiments/campaign-g1/REQUIRED_RULES.json',
               ROOT / 'experiments/campaign-g1/migration_evidence.py',ROOT / 'experiments/campaign-g1/migration_contract.py',
               ROOT / 'experiments/campaign-g1/migration_canaries.py',
               ROOT / 'experiments/campaign-g1/trusted_receipts.py',
               ROOT / 'experiments/campaign-g1/qualified_canary_cases.py',
               ROOT / 'experiments/campaign-g1/admission-trust.json',
               ROOT / 'experiments/campaign-g1/freeze.py']
    from llm.stowlm.format import DEFAULT_TOKENIZER_PATH
    tokenizer = DEFAULT_TOKENIZER_PATH / 'tokenizer.json' if DEFAULT_TOKENIZER_PATH.is_dir() else DEFAULT_TOKENIZER_PATH
    provenance = {str(args.freeze_manifest.resolve()): frozen['_manifest_sha256'],
        str(manifest_path.resolve()): args._validated_instance_manifest_sha256,
        str(args.split_manifest.resolve()): args._validated_split_manifest_sha256}
    if g1_summary:
        provenance[g1_summary['path']] = g1_summary['sha256']
    identity = dict(epoch_id=frozen['epoch_id'], diagnostic=args.diagnostic, allow_drift=args.allow_drift,
        numerical_caches=frozen.get('numerical_caches'),required_rules=required_rules,component_epoch=components,
        **publication_lineage(g1_summary,args.diagnostic),
        inputs=rows, split=args.split, rungs=args.rungs, split_entries_sha256=inventory.entries_sha256,
        source_sha256=frozen['source_sha256'], producer_source_sha256=t.source_fingerprint()['sha256'] if not args.allow_drift else 'diagnostic-moving-source',
        freeze_manifest_sha256=frozen['_manifest_sha256'], instance_manifest_sha256=args._validated_instance_manifest_sha256,
        scripts_sha256={str(p.relative_to(ROOT)): sha(p) for p in scripts},
        artifacts_sha256=({str(tokenizer.resolve()):sha(tokenizer)} if tokenizer.is_file() else {}) |
            {row['audit_path']:row['audit_sha256'] for row in rows if row.get('audit_path')},
        provenance_sha256=provenance,
        budgets=dict(scalable=args.scalable_budgets, exact=args.exact_budgets, ballast=args.ballast_s,
                     check=args.check_s, timeout=args.timeout_s), seed=args.seed, max_nodes=args.max_nodes,
        shard_index=args.shard_index, shard_count=args.shard_count, g1_summary=g1_summary)
    machinery().ensure_identity(output / 'campaign-manifest.json', identity)
    print(json.dumps(dict(phase='admission', selected=len(rows), workers=args.workers,
        uncalibrated_rungs=args.rungs, estimate='first shard supplies same-rung measured wall time and RAM; no solve-rate guess',
        label='diagnostic, not evidence' if args.diagnostic else 'frozen-source gold')), flush=True)
    if args.estimate_only:
        return 0
    metadata = dict(epoch_id=identity['epoch_id'], source_sha256=identity['source_sha256'],instance_root=str(args.instance_root.resolve()),
        diagnostic=args.diagnostic,evidence_eligible=not args.diagnostic,required_rules=required_rules,component_epoch=components,
        g1_approval=(g1_summary or {}).get('approval'),
        **publication_lineage(g1_summary,args.diagnostic),
        label='diagnostic, not evidence' if args.diagnostic else 'frozen-source gold',
        tiny_g1_exception=args.rungs == ['tiny'] and g1_summary is None,
        production_g1_required=any(rung != 'tiny' for rung in args.rungs), g1_summary=g1_summary,
        resume_command=' '.join(__import__('shlex').quote(arg) for arg in ['uv', 'run', 'python', str(Path(__file__).relative_to(ROOT)), *(argv if argv is not None else sys.argv[1:])]))
    # One driver per output: avoids overlapping resumes racing publication/state.
    with (output / 'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        execution_rows, results = calibration_selection(args, identity, rows, output)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_shard, args, identity, row, output) for row in execution_rows]
            for future in as_completed(futures):
                value = future.result()
                results.append(value)
                # Publish and calibrate after each completed shard, even on a later interrupt.
                summary = summarize(results, identity)
                atomic_json(output / 'summary.json', summary)
                publication_checkpoint_check(output, identity)
                t.write_manifest(output, args.split_manifest, allowed_splits=tuple(args.split),
                    allowed_rungs=args.rungs, campaign_metadata=metadata)
                print(json.dumps(dict(spec_key=value['spec_key'], status=value['status'], accepted=value['accepted'],
                    reason=value.get('reason'), resumed=value.get('resumed', False), wall_s=value.get('process_wall_s'))), flush=True)
        freeze_check(args)
        machinery().runner_check(args, identity)
        publication_checkpoint_check(output, identity)
        publication = t.write_manifest(output, args.split_manifest, allowed_splits=tuple(args.split),
            allowed_rungs=args.rungs, campaign_metadata=metadata)
        summary = summarize(results, identity)
        summary.update(campaign_wall_s=time.monotonic()-started, published=publication['accepted'],
                       publication_exclusions=publication['excluded'],rule_binding_yield=publication['rule_binding_yield'])
        atomic_json(output / 'summary.json', summary)
        print(json.dumps(summary), flush=True)
    return 0


if __name__ == '__mp_main__':
    cache_fence()

if __name__ == '__main__':
    raise SystemExit(main())
