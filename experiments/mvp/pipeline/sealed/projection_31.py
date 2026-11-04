"""D276 planning range from the 44 development sample rows, without map reads.

The range is conditional arithmetic, not a confidence interval or a guaranteed
wall bound. Supplemental baselines never enter this calculation. Reproduce with
``python -m experiments.mvp.pipeline.sealed.projection_31``.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

from .common import HERE, ROOT, HarnessError, atomic_json, object_hash, sha256

STUDY = HERE/'measurements/native-development-20261102T070532Z'
OUTPUT = HERE/'measurements/projection-3.1.json'
GUARD_MEASUREMENT = HERE/'measurements/guard-cost-D276/measurement.json'
TARGETS = {('tiny15','pipeline'):15, ('tiny15','twostep'):15,
           ('small30','pipeline'):30, ('small30','twostep'):30,
           ('envelope24','pipeline'):24, ('envelope24','twostep'):24,
           ('envelope24','instruction'):24, ('envelope24','twostep_300'):24}
SAMPLE = {key: 8 if key[0] == 'envelope24' else 3 for key in TARGETS}


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value) and value >= 0


def _stats(values):
    return dict(n=len(values), mean=statistics.mean(values) if values else None,
        median=statistics.median(values) if values else None,
        minimum=min(values) if values else None, maximum=max(values) if values else None,
        total=sum(values))


def validate_sample(rows):
    if len(rows) != 44 or Counter((r['cohort'],r['arm']) for r in rows) != Counter(SAMPLE):
        raise HarnessError('D276 projection requires exactly the 44 sample rows, not supplemental baselines')
    identities = [(r['map_id'],r['arm']) for r in rows]
    if len(set(identities)) != 44:
        raise HarnessError('Duplicate sample map/arm')


def _components(row):
    required = ('child_elapsed_s','driver_elapsed_s','driver_admission_wait_s')
    missing = [name for name in required if not _finite(row.get(name))]
    audit = row.get('audit_elapsed_s')
    audit_skipped = audit is None and row.get('child_timed_out') is True
    if not _finite(audit) and not audit_skipped:
        missing.append('audit_elapsed_s')
    if missing:
        return dict(available=False, missing=missing)
    child, elapsed, wait = (row[k] for k in required)
    if elapsed < wait:
        raise HarnessError('Admission wait exceeds total driver elapsed')
    child_timeout, audit_timeout = row.get('child_timed_out') is True, row.get('audit_timed_out') is True
    if child_timeout and (not _finite(row.get('child_timeout_s')) or child+.01 < row['child_timeout_s']):
        raise HarnessError('Deadline-censored child must retain its full configured wall time')
    audit = 0. if audit_skipped else audit
    # Elapsed subtraction isolates measured parent verification/I/O overhead.
    # Rounding/clock overlap never subtracts observed child or audit work.
    discrepancy = max(0., child+audit-(elapsed-wait))
    overhead = max(0., elapsed-wait-child-audit)
    return dict(available=True, child_s=child, audit_s=audit,
        parent_verification_io_s=overhead, admission_wait_s=wait,
        observed_service_s=child+audit+overhead, clock_overlap_s=discrepancy,
        child_cap_s=row.get('child_timeout_s'), audit_cap_s=row.get('audit_timeout_s'),
        child_deadline_censored=child_timeout, audit_deadline_censored=audit_timeout,
        audit_skipped_after_child_deadline=audit_skipped)


def project(rows, *, targets=None, workers=4, serial_reader_reserve_s=1800.,
            audit_reserve_after_censor_s=300., optimistic_speedup=2.):
    """Project per-map means once onto worker slots, retaining deadline censoring."""
    targets = TARGETS if targets is None else targets
    if (type(workers) is not int or workers < 1 or not _finite(serial_reader_reserve_s)
            or not _finite(audit_reserve_after_censor_s) or not _finite(optimistic_speedup)
            or optimistic_speedup < 1):
        raise ValueError('Invalid worker/reserve/speedup configuration')
    groups, summaries, problems = defaultdict(list), [], []
    for row in rows:
        key = (row['cohort'],row['arm'])
        if key not in targets:
            raise HarnessError('Row outside registered projection cells')
        identity = (row['map_id'],row['arm'])
        if any((r['map_id'],r['arm']) == identity for r in summaries):
            raise HarnessError('Duplicate projection map/arm')
        values = _components(row)
        record = dict(map_id=row['map_id'],map_sha256=row.get('map_sha256'),cohort=key[0],arm=key[1],
            status=row.get('status'),instruction_type=row.get('instruction_type'),**values)
        profile = row.get('profile')
        if isinstance(profile,dict):
            record['recorded_profile_sha256'] = object_hash(profile)
            kwargs = {k:profile.get(k) for k in ('budget_s','finalize_s','seed','config')}
            record['recorded_engine_kwargs_sha256'] = object_hash(kwargs)
            record['recorded_profile'] = {k:profile.get(k) for k in
                ('name','budget_s','finalize_s','seed','engine_timeout_s','selection_kind','data_sha256')}
        else:
            record['recorded_profile_sha256'] = None
            record['recorded_engine_kwargs_sha256'] = None
        summaries.append(record)
        groups[key].append(record)
        if not values['available']:
            problems.append(dict(map_id=row['map_id'],arm=row['arm'],missing=values['missing']))
    table = []
    for (cohort,arm), count in targets.items():
        if type(count) is not int or count < 1:
            raise ValueError('Target trial counts must be positive integers')
        group = groups[cohort,arm]
        usable = [r for r in group if r['available']]
        if not group:
            problems.append(dict(cohort=cohort,arm=arm,missing='unmeasured cohort/arm cell'))
        table.append(dict(cohort=cohort,arm=arm,target_trials=count,sample_rows=len(group),
            usable_timing_rows=len(usable),
            statuses=dict(Counter(r['status'] for r in group)),
            child_wall_s=_stats([r['child_s'] for r in usable]),
            audit_wall_s_including_explicit_skips=_stats([r['audit_s'] for r in usable]),
            audit_executed_rows=sum(not r['audit_skipped_after_child_deadline'] for r in usable),
            parent_verification_io_s=_stats([r['parent_verification_io_s'] for r in usable]),
            service_s=_stats([r['observed_service_s'] for r in usable]),
            admission_wait_s=_stats([r['admission_wait_s'] for r in usable]),
            child_deadlines=sum(r['child_deadline_censored'] for r in usable),
            audit_deadlines=sum(r['audit_deadline_censored'] for r in usable),
            audit_skipped_after_child_deadline=sum(r['audit_skipped_after_child_deadline'] for r in usable)))
    result = dict(schema_version='sealed-runtime-projection/3',decision='D276',
        label='Conditional planning projection from loaded-machine development measurements',
        status='incomplete' if problems else 'conditional_projection',
        workers=workers,registered_trials=sum(targets.values()),
        registered_trials_by_arm=dict(Counter({arm:sum(n for (c,a),n in targets.items() if a==arm)
                                              for arm in {a for c,a in targets}})),
        sample_rows=len(rows),per_cohort_arm=table,rows=summaries,
        unmeasured_or_incomplete=problems,projection_hours_range=None,planning_only_hours_range=None,scenarios=[],
        overall_projection_status='awaiting_full_pipeline_guard_measurement',
        confidence_interval=False,
        conditions=dict(enabled_cores=18,workers=workers,numerical_threads_per_worker=1,
            gpu_idle=True,load1_max=workers+2,other_fleet_work=[],fresh_telemetry_required=True),
        assumptions=dict(optimistic_uncensored_child_audit_speedup=optimistic_speedup,
            conservative_uncensored_child_audit_speedup=1.,
            speedup_is_measured=False,
            speedup_basis='Explicit planning assumption; not inferred from load1 or the 18-core count.',
            deadline_censored_child_audit_times='Hold at their observed full wall cap in both scenarios; never divide them by a speedup.',
            future_audit_reserve_per_skipped_censored_trial_s=audit_reserve_after_censor_s,
            future_audit_reason='A trial that formerly timed out may finish just before its cap on a quieter machine and then require an audit; reserve its full 300s audit cap.',
            parent_verification_io='Hold measured overhead fixed in both scenarios.',
            serial_reader_reserve_s=serial_reader_reserve_s,
            serial_reader='One sequential prepass reserve; real adopted-reader inference is unmeasured by this recorded-reader sample.',
            scheduling_tail='Reserve one maximum modelled job after dividing summed worker service by workers once.',
            admission_wait='Additional and unbounded; excluded from projected range.',
            new_pipeline_freeze_full_weight_guard='Additional unmeasured launch/per-open verification overhead; excluded from range, not assumed zero.'),
        limitations=['This is not a statistical confidence interval or a guarantee for unseen sealed maps.',
            'The loaded-machine no-speedup case is the D276 conservative planning anchor, not a proved upper bound.',
            'Eight envelope and three tiny/small maps per arm leave substantial map/load uncertainty.',
            'Timeouts measure the registered capped trial runtime, not uncensored solver completion time.',
            'Four workers remain four workers on 18 cores; do not divide by cores or by workers twice.',
            'Supplemental baseline rows and their time never enter this projection.'])
    if problems:
        return result
    observed_work = sum(cell['target_trials']*cell['service_s']['mean'] for cell in table)
    observed_max = max(r['observed_service_s'] for r in summaries)
    result['loaded_empirical_anchor'] = dict(weighted_worker_seconds=observed_work,
        ideal_slot_seconds=observed_work/workers,scheduling_tail_s=observed_max,
        serial_reader_reserve_s=serial_reader_reserve_s,
        projected_hours_excluding_admission_and_new_guard=(observed_work/workers+observed_max+serial_reader_reserve_s)/3600,
        note='Uses actual skipped-audit zeros only where a child deadline prevented audit dispatch; future audit reserve appears in the scenario range.')
    for label,speedup in [('optimistic_quiet_assumption',optimistic_speedup),('conservative_loaded_anchor',1.)]:
        work, longest, audit_reserve_work, scenario_table = 0.,0.,0.,[]
        for key,count in targets.items():
            group = groups[key]
            values, child_values, audit_values, source_values, reserves = [],[],[],[],[]
            for row in group:
                child = row['child_s'] if row['child_deadline_censored'] else row['child_s']/speedup
                audit = row['audit_s'] if row['audit_deadline_censored'] else row['audit_s']/speedup
                reserve = audit_reserve_after_censor_s if row['audit_skipped_after_child_deadline'] else 0.
                service = child+audit+reserve+row['parent_verification_io_s']
                values.append(service);child_values.append(child);audit_values.append(audit)
                source_values.append(row['parent_verification_io_s']);reserves.append(reserve)
            mean = statistics.mean(values)
            work += count*mean
            audit_reserve_work += count*statistics.mean(reserves)
            longest = max(longest,max(values))
            scenario_table.append(dict(cohort=key[0],arm=key[1],target_trials=count,
                mean_service_s=mean,mean_child_s=statistics.mean(child_values),
                mean_executed_audit_s=statistics.mean(audit_values),
                mean_parent_verification_io_s=statistics.mean(source_values),
                mean_future_audit_reserve_s=statistics.mean(reserves)))
        total = work/workers+longest+serial_reader_reserve_s
        result['scenarios'].append(dict(name=label,uncensored_speedup=speedup,
            weighted_worker_seconds=work,ideal_slot_seconds=work/workers,
            scheduling_tail_s=longest,serial_reader_reserve_s=serial_reader_reserve_s,
            future_audit_reserve_worker_seconds=audit_reserve_work,
            future_audit_reserve_ideal_slot_seconds=audit_reserve_work/workers,
            projected_hours_excluding_admission_and_new_guard=total/3600,per_cohort_arm=scenario_table))
    result['planning_only_hours_range'] = [min(s['projected_hours_excluding_admission_and_new_guard'] for s in result['scenarios']),
                                        max(s['projected_hours_excluding_admission_and_new_guard'] for s in result['scenarios'])]
    return result



def add_guard_model(projection, seconds_per_guard_range, *, provenance=None, cache_multiplier=2.):
    """Add measured full-guard cost, exposing concurrency/cache assumptions.

    Four controller checks per trial are outside process caps; one planning and
    one audit check consume their existing caps. The serial prepass's 24 checks
    consume its single 1800s reserve. No deadline-censored duration is uncensored.
    """
    if (not isinstance(seconds_per_guard_range, (list,tuple)) or len(seconds_per_guard_range) != 2
            or any(not _finite(v) or v <= 0 for v in seconds_per_guard_range)
            or seconds_per_guard_range[0] > seconds_per_guard_range[1]
            or not _finite(cache_multiplier) or cache_multiplier < 1):
        raise ValueError('Guard seconds require an explicit positive ordered range')
    result = copy.deepcopy(projection)
    lo,hi = seconds_per_guard_range
    workers = result['workers']
    targets = {(c['cohort'],c['arm']):c['target_trials'] for c in result['per_cohort_arm']}
    groups = defaultdict(list)
    for row in result['rows']:
        groups[row['cohort'],row['arm']].append(row)
    reserve = result['assumptions']['serial_reader_reserve_s']
    trial_count = result['registered_trials']
    parent_calls = 4*trial_count
    result['guard_model'] = dict(seconds_per_full_guard_range=list(seconds_per_guard_range),
        provenance=provenance or {},fixed_launch_guard_calls=4,parent_guard_calls_per_trial=4,
        inner_planning_guard_calls_per_trial=1,inner_audit_guard_calls_per_audited_trial=1,
        serial_prepass_guard_calls=24,sequential_prepass_guard_s_range=[24*lo,24*hi],
        serial_prepass_inside_existing_reader_reserve=True,
        prepass_guard_alone_exceeds_reserve=24*hi >= reserve,
        concurrency='Lower: four caller guards overlap at single-call cost. Upper: parent guard work serializes; inner guard queue may cost four isolated calls.',
        cache='One warm-cache observation. A separate 2x-per-guard sensitivity is an assumption, not a second measurement.',
        measured_file_inventory_may_differ_from_final=True,
        conservative_parent_reserve='Four parent checks charged even where a timeout could skip later checks.')
    result['sealed_release_ready'] = False
    result['runtime_budget_receipt'] = False
    result['release_readiness_note'] = 'Timing projection only; does not satisfy cap calibration, frozen-manifest, reader or launch authorization gates.'
    result['label'] = 'Conditional 3.1 wall-time projection from loaded-machine development measurements'
    result['assumptions']['new_pipeline_freeze_full_weight_guard'] = (
        'Measured full-weight require_frozen cost is included through the explicit guard model; '
        'adapter/path-normalization latency is not separately measured. Warm-cache overlap and '
        'serialization scenarios are assumptions, not a guaranteed runtime bound.')
    if result['planning_only_hours_range'] is None:
        result['overall_projection_status'] = 'incomplete_timing_cells'
        return result
    guarded = []
    options = [('optimistic_quiet_guard_overlap',result['scenarios'][0],lo,False,1.),
               ('conservative_loaded_guard_serialization',result['scenarios'][-1],hi,True,1.),
               ('cache_uncertainty_sensitivity',result['scenarios'][-1],hi,True,cache_multiplier)]
    for name,base,g,serialized,multiplier in options:
        g *= multiplier
        effective_inner_g = g*workers if serialized else g
        inner_work, longest = 0.,0.
        per_cell = []
        for key,count in targets.items():
            increments=[];durations=[]
            for row in groups[key]:
                speedup=base['uncensored_speedup']
                child=row['child_s'] if row['child_deadline_censored'] else row['child_s']/speedup
                audit=row['audit_s'] if row['audit_deadline_censored'] else row['audit_s']/speedup
                if row['audit_skipped_after_child_deadline']:
                    audit += result['assumptions']['future_audit_reserve_per_skipped_censored_trial_s']
                child_cap=row.get('child_cap_s')
                audit_cap=row.get('audit_cap_s')
                # Recorded skipped-audit rows lack an audit deadline object;
                # their explicit future reserve already occupies that full cap.
                if not _finite(audit_cap) and row['audit_skipped_after_child_deadline']:
                    audit_cap=result['assumptions']['future_audit_reserve_per_skipped_censored_trial_s']
                child_extra=(0. if row['child_deadline_censored'] else
                    min(effective_inner_g,max(0.,child_cap-child)) if _finite(child_cap) else effective_inner_g)
                audit_extra=(0. if row['audit_deadline_censored'] else
                    min(effective_inner_g,max(0.,audit_cap-audit)) if _finite(audit_cap) else effective_inner_g)
                extra=child_extra+audit_extra
                increments.append(extra)
                duration=child+audit+row['parent_verification_io_s']+extra
                if not serialized:
                    duration += 4*g
                durations.append(duration)
            added=count*statistics.mean(increments)
            inner_work += added
            longest=max(longest,max(durations))
            per_cell.append(dict(cohort=key[0],arm=key[1],target_trials=count,
                                 inner_guard_added_worker_seconds=added))
        parent_worker_seconds=parent_calls*g
        parent_wall=parent_worker_seconds if serialized else parent_worker_seconds/workers
        launch_wall=4*g
        total=base['weighted_worker_seconds']/workers+inner_work/workers+parent_wall+longest+reserve+launch_wall
        arm_projection = {}
        for arm in sorted({arm for cohort,arm in targets}):
            arm_trials = sum(n for (cohort,a),n in targets.items() if a == arm)
            service = sum(c['target_trials']*c['mean_service_s']
                          for c in base['per_cohort_arm'] if c['arm'] == arm)/workers
            inner = sum(c['inner_guard_added_worker_seconds']
                        for c in per_cell if c['arm'] == arm)/workers
            parent = arm_trials*4*g/(1 if serialized else workers)
            arm_projection[arm] = dict(target_trials=arm_trials,
                ideal_slot_service_seconds=service,
                inner_guard_slot_seconds=inner,parent_guard_wall_seconds=parent,
                attributable_wall_seconds=service+inner+parent,
                attributable_hours=(service+inner+parent)/3600,
                standalone_arm_campaign_wall=False)
        shared = dict(scheduling_tail_s=longest,serial_reader_reserve_s=reserve,
                      fixed_launch_guard_s=launch_wall,
                      wall_seconds=longest+reserve+launch_wall,
                      hours=(longest+reserve+launch_wall)/3600)
        guarded.append(dict(name=name,warm_guard_measurement_s=g/multiplier,
            per_guard_cache_multiplier=multiplier,cache_multiplier_is_measured=multiplier==1.,
            uncensored_child_audit_speedup=base['uncensored_speedup'],
            parent_guard_calls=parent_calls,parent_guard_worker_seconds=parent_worker_seconds,
            parent_guard_wall_seconds_assumption=parent_wall,
            inner_guard_additional_worker_seconds=inner_work,inner_guard_additional_slot_seconds=inner_work/workers,
            fixed_launch_guard_seconds=launch_wall,scheduling_tail_s=longest,
            serial_reader_reserve_s=reserve,sequential_prepass_guard_s=24*g,
            serial_prepass_guards_fit_alone=24*g < reserve,
            projected_hours_excluding_unbounded_admission=total/3600,per_cohort_arm=per_cell,
            per_arm_projection=arm_projection,shared_projection=shared))
    result['guard_scenarios']=guarded
    result['projection_hours_range']=[guarded[0]['projected_hours_excluding_unbounded_admission'],
                                      guarded[1]['projected_hours_excluding_unbounded_admission']]
    result['cache_uncertainty_sensitivity_hours']=guarded[2]['projected_hours_excluding_unbounded_admission']
    result['per_arm_projection'] = {
        arm:dict(target_trials=guarded[0]['per_arm_projection'][arm]['target_trials'],
                 conditional_attributable_hours_range=[s['per_arm_projection'][arm]['attributable_hours']
                                                        for s in guarded[:2]],
                 cache_uncertainty_sensitivity_hours=guarded[2]['per_arm_projection'][arm]['attributable_hours'],
                 standalone_arm_campaign_wall=False)
        for arm in guarded[0]['per_arm_projection']}
    result['shared_projection'] = dict(
        conditional_hours_range=sorted(s['shared_projection']['hours'] for s in guarded[:2]),
        conditional_hours_by_scenario={s['name']:s['shared_projection']['hours'] for s in guarded[:2]},
        cache_uncertainty_sensitivity_hours=guarded[2]['shared_projection']['hours'],
        includes=['serial_reader_reserve','fixed_launch_guards','scheduling_tail'],
        admission_wait='Additional and unbounded; excluded.')
    result['assumptions']['per_arm_projection'] = (
        'Additive contributions within the mixed four-worker campaign, not standalone arm campaign wall times: '
        'target-weighted service and inner guards divided by four once, plus attributable parent guard wall time. '
        'Shared prepass/startup/tail appears once separately; no arm-specific scheduling guarantee is implied.')
    result['overall_projection_status']='conditional_projection_including_measured_guard_model'
    result['limitations'].extend([
        'The guard range is a warm-cache overlap/serialization scenario range, not measured four-caller throughput.',
        'Inner guard work may turn a formerly completed plan into a deadline_exceeded/no-plan budget outcome; the projection makes no pass-rate prediction.',
        'The cache multiplier sensitivity is explicit assumed overhead, not a measured cold-cache bound.'])
    return result


def _safe_input(path):
    """Only metadata/results from this explicitly public development study."""
    path = Path(path).resolve()
    if not path.is_relative_to(STUDY.resolve()):
        raise HarnessError('Projection inputs must stay in the authorized native development study')
    if path.is_symlink():
        raise HarnessError('Projection metadata must not be symlinked')
    return path


def build_projection(*, study=STUDY, guard_measurement=GUARD_MEASUREMENT):
    study = Path(study).resolve()
    if study != STUDY.resolve():
        raise HarnessError('D276 source is the fixed native-development sample campaign')
    source = _safe_input(study/'run/rows.json')
    payload = source.read_bytes()
    rows = json.loads(payload)
    validate_sample(rows)
    result = project(rows)
    inputs = {str(source):hashlib.sha256(payload).hexdigest()}
    readings, missing_readings = [],[]
    # Map job directories by their committed request metadata, not filename order.
    identities = {(r['map_id'],r['arm']) for r in rows}
    for target in sorted((study/'run/jobs').iterdir()):
        if not target.is_dir():
            continue
        request = _safe_input(target/'job.json')
        if not request.is_file():
            continue
        raw = request.read_bytes();job = json.loads(raw)
        identity = (job.get('map_id'),job.get('arm'))
        if identity not in identities:
            continue
        inputs[str(request)] = hashlib.sha256(raw).hexdigest()
        for name,phase in [('DISPATCHED.json','child'),('AUDIT-DISPATCHED.json','audit')]:
            path = _safe_input(target/name)
            if not path.is_file():
                if phase == 'child':
                    missing_readings.append(dict(map_id=identity[0],arm=identity[1],phase=phase))
                continue
            raw = path.read_bytes();record = json.loads(raw);snapshot = record.get('admission',{})
            inputs[str(path)] = hashlib.sha256(raw).hexdigest()
            readings.append(dict(map_id=identity[0],arm=identity[1],phase=phase,
                load1=snapshot.get('load1'),enabled_cores=snapshot.get('cores'),
                age_s=snapshot.get('age_s'),waited_s=snapshot.get('waited_s')))
    result['observed_admission'] = dict(readings=readings,missing_child_readings=missing_readings,
        load1=_stats([r['load1'] for r in readings if _finite(r['load1'])]),
        enabled_core_counts=sorted({r['enabled_cores'] for r in readings if type(r['enabled_cores']) is int}),
        note='Real child and audit dispatch readings from these 44 rows only; supplementary baselines excluded.')
    start = _safe_input(study/'run/STARTED.json');finish = _safe_input(study/'run/FAILED.json')
    if start.is_file() and finish.is_file():
        for path in (start,finish):inputs[str(path)] = sha256(path)
        result['observed_44_sample_wall_s'] = json.loads(finish.read_text())['t']-json.loads(start.read_text())['t']
    configuration = _safe_input(study/'run/config.json')
    if configuration.is_file():
        inputs[str(configuration)] = sha256(configuration)
        config = json.loads(configuration.read_text())
        result['recorded_study_configuration'] = {k:config.get(k) for k in
            ('mode','workers','profile','arm_deadlines_s','audit_timeout_s','prepass_timeout_s','reader_mode')}
    by_profile = defaultdict(list)
    for row in result['rows']:
        if row['recorded_profile_sha256']:
            by_profile[row['recorded_profile_sha256']].append(row)
    result['recorded_profiles'] = [dict(profile_sha256=key,
        engine_kwargs_sha256=group[0]['recorded_engine_kwargs_sha256'],
        rows=len(group),profile=group[0]['recorded_profile']) for key,group in sorted(by_profile.items())]
    result['profile_accounting'] = dict(explicit_standard_profile_rows=sum(len(v) for v in by_profile.values()),
        missing_pipeline_instruction_profile_rows=sum(r['recorded_profile_sha256'] is None
            and r['arm'] in {'pipeline','instruction'} for r in result['rows']),
        note='Missing timeout-row profile objects are not invented. The recorded job/config names standard; explicit completed rows show D265 300/100 and 1800s engine wall. The separate 1200s outer process deadline can terminate that engine allowance earlier.')
    result['instruction_type_sample_counts'] = dict(Counter(r.get('instruction_type') or 'unavailable'
                                                           for r in rows if r['arm']=='instruction'))
    result['instruction_type_coverage_limit'] = 'The eight r0 timing cases are not balanced six/type like the sealed cohort; per-type timing samples are sparse and are not additional projection cells.'
    result['source'] = dict(path=str(source),sha256=inputs[str(source)],sample_rows=44,
        supplemental_rows_used=0,all_input_hashes=inputs,
        code_sha256={str(Path(__file__).resolve()):sha256(__file__),str(HERE/'common.py'):sha256(HERE/'common.py')})
    if guard_measurement is not None:
        guard_path=Path(guard_measurement).resolve()
        if not guard_path.is_relative_to(HERE/'measurements'):
            raise HarnessError('Guard measurement must stay in sealed/measurements')
        if guard_path.is_file():
            raw=guard_path.read_bytes();measurement=json.loads(raw)
            g=measurement.get('full_guard_s')
            if (not _finite(g) or g <= 0 or measurement.get('full_weights') is not True
                    or measurement.get('model_inference') is not False or measurement.get('sealed_inputs_opened') != 0):
                raise HarnessError('Guard timing must describe a full-weight check without inference or sealed access')
            digest=hashlib.sha256(raw).hexdigest()
            result['source']['all_input_hashes'][str(guard_path)]=digest
            result=add_guard_model(result,[g,g],provenance=dict(path=str(guard_path),sha256=digest,measurement=measurement))
    result['computed_utc'] = datetime.now(timezone.utc).isoformat()
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--out',type=Path,default=OUTPUT)
    cli.add_argument('--guard-measurement',type=Path,default=GUARD_MEASUREMENT)
    args = cli.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(HERE.resolve()):
        raise HarnessError('Projection output must stay inside sealed/')
    result = build_projection(guard_measurement=args.guard_measurement)
    atomic_json(out,result)
    print(json.dumps(dict(path=str(out),range_hours=result['projection_hours_range'],planning_only_hours_range=result['planning_only_hours_range'],
        loaded_anchor_hours=result.get('loaded_empirical_anchor',{}).get('projected_hours_excluding_admission_and_new_guard'),
        sample_rows=result['sample_rows'],registered_trials=result['registered_trials'],
        label=result['label'])))


if __name__ == '__main__':
    main()
