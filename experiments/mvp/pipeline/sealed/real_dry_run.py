"""Detached four-worker native development study; no sealed input or search stub.

Eight r0 envelope cells exercise both arms, instructions and the300s sensitivity.
Six tiny/small development sections measure those timings. Remaining envelope
baselines complete the32-map cap check. Every numerical call has a driver wall
deadline and an independently audited result; timed-out trials are not retried.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import time
from threading import Event

from . import driver
from .common import HERE, ROOT, HarnessError, atomic_json, canonical, exclusive_json, object_hash, read_json, sha256
from .driver import public_path
from .instructions import CAP_REDUCTION, RULE_TYPES, public_facts, select_rule, sentence_for

WORKERS = 4
DEADLINES = dict(pipeline=1200,instruction=1200,twostep=180,twostep_300=420)


def admission(path=None, *, sleep=time.sleep, now=time.time, stop=None):
    """Real development admission: four slots, fresh >=8 cores, load<=45.

    Development is explicitly allowed alongside the busy GPU. The final sealed
    release instead requires18enabled cores, load<=6 and no other fleet work.
    """
    path = public_path(path or ROOT/'experiments/fleet/core-state.json')
    began = time.monotonic()
    while True:
        if stop is not None and stop():
            return None
        try:
            row = read_json(path)
            mask, load = row['mask'], float(row['load1'])
            age = now()-float(row['t'])
            if (type(mask) is int and mask >= 0 and mask.bit_count() >= 8 and math.isfinite(load)
                    and 0 <= load <= 45 and 0 <= age < 120):
                return dict(row,cores=mask.bit_count(),age_s=age,waited_s=time.monotonic()-began,
                            policy='development four workers; GPU may be busy; not sealed-idle admission')
        except (OSError,ValueError,TypeError,KeyError,OverflowError):
            pass
        sleep(5)


def prepare(out):
    from .worker import _load
    from experiments.mvp.reader.schema import context_from_instance
    request = read_json(public_path(HERE.parent/'maps/requests.json'))
    listing = public_path(HERE.parent/'maps/dev.txt')
    declared = {str(public_path(line)) for line in listing.read_text().splitlines() if line.strip()}
    specs = [row for row in request['specs'] if row['split']=='dev']
    if len(specs) != 32 or {str(public_path(r['path'])) for r in specs} != declared:
        raise HarnessError('Real study requires the complete registered32-map development list')
    maps, loaded = [], {}
    sample = []
    for spec in specs:
        path = public_path(spec['path'])
        item = dict(path=str(path),sha256=sha256(path),map_id='dev:'+str(path.relative_to(ROOT)),
            cohort='envelope24',ship_id=spec['ship'],cell=[spec['ship'],spec['fill_target'],spec['generator']])
        loaded[item['map_id']] = _load(dict(item,arm='pipeline',mode='dry-run'))
        maps.append(item)
        if spec['rep']==0:
            sample.append(item['map_id'])
    ordered = sorted(maps,key=lambda m:(canonical(public_facts(loaded[m['map_id']])),m['map_id']))
    readings = {}
    for rank,item in enumerate(ordered):
        instance = loaded[item['map_id']]
        rule = select_rule(instance,rank)
        item['calibration_rules'] = [select_rule(instance,4*(rank//4)+kind) for kind in range(4)]
        text = sentence_for(rule)
        key = object_hash(dict(sentence=text,context=context_from_instance(instance)))
        readings[key] = rule
    # Tiny/small budget probes stay within the public quick sets; one per ship.
    for size in ('tiny','small'):
        path = public_path(ROOT/f'experiments/mvp/frontier/lab/sets/quick_{size}.txt')
        by_ship = {}
        for line in sorted(path.read_text().splitlines()):
            line = line.split('#')[0].strip()
            if line:
                source = public_path(line)
                by_ship.setdefault(driver._ship_id({'path':str(source)}),source)
        for ship,source in sorted(by_ship.items()):
            item = dict(path=str(source),sha256=sha256(source),map_id='dev:'+str(source.relative_to(ROOT)),
                cohort='tiny15' if size=='tiny' else 'small30',ship_id=ship)
            maps.append(item)
            sample.append(item['map_id'])
    recording = out/'recorded-readings.json'
    atomic_json(recording,dict(origin='Authorized development expected-answer recordings, not model inference',
        adopted_reader='adapter-v2, D264; actual GPU inference runs at3.1',readings=readings))
    config = dict(mode='dry-run',workers=WORKERS,maps=maps,dry_sample_map_ids=sample,
        profile='standard',twostep_budget_s=30,twostep_s=15,twostep_finalize_s=15,
        arm_deadlines_s=DEADLINES,audit_timeout_s=300,prepass_timeout_s=1800,
        reader_factory='experiments.mvp.pipeline.sealed.readers:development_recorded',
        parser_options=dict(path=str(recording),expected_sha256=sha256(recording)),recordings_sha256=sha256(recording),
        fixture_surrogate=False,reader_mode='recorded development only; no planner/checker stubs',
        native_cache_root=str(HERE/'runtime-cache'))
    atomic_json(out/'study-config.json',config)
    return config


def _supplement(out, config):
    """Search each remaining no-rule map once, with the same frozen recipe."""
    run = out/'run'
    driver.verify_freeze(run)
    allocation = read_json(run/'instruction-allocation.json')
    by_id = {r['map_id']:r for r in allocation}
    sampled = set(config['dry_sample_map_ids'])
    cfg = dict(config)
    cfg.pop('dry_sample_map_ids')
    cfg['maps'] = [by_id[m['map_id']] for m in config['maps']
                   if m['cohort']=='envelope24' and m['map_id'] not in sampled]
    jobs = [j for j in driver.jobs_for(cfg,run) if j['arm']=='pipeline']
    rows = []
    halted = Event()
    def execute(job):
        began = time.monotonic()
        waited = 0.
        target = out/'calibration-baselines'/object_hash(job['map_id'])[:20]
        target.mkdir(parents=True,exist_ok=True)
        exclusive_json(target/'STARTED.json',dict(t=time.time(),job=job))
        if halted.is_set():
            return driver._failure_row(job,'Stopped after unconfirmed owned-group cleanup',status='not_run')
        try:
            reading = admission(stop=halted.is_set)
            if reading is None:
                return driver._failure_row(job,'Study cancelled',status='not_run')
            waited += reading['waited_s']
            driver.verify_freeze(run)
            atomic_json(target/'DISPATCHED.json',dict(t=time.time(),admission=reading))
            raw = driver.run_child(job,target,DEADLINES['pipeline'])
            waited += admission()['waited_s']
            row = driver.audit_child(job,target,timeout_s=300)
            row.update(driver._execution_metrics(target))
        except Exception as error:
            try:
                raw = read_json(target/'row.json')
            except (OSError,ValueError):
                raw = driver._failure_row(job,str(error),dispatched=True)
            row = driver._unverified(raw,error,phase='supplement',job=job)
            row.update(driver._execution_metrics(target))
        row.update(driver_elapsed_s=time.monotonic()-began,driver_admission_wait_s=waited)
        if row.get('child_cleanup_failed') or row.get('audit_cleanup_failed'):
            halted.set()
        atomic_json(target/'result-row.json',row)
        return row
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        pending = [pool.submit(execute,job) for job in jobs]
        for future in as_completed(pending):
            rows.append(future.result())
            atomic_json(out/'supplement-rows.json',rows)
            atomic_json(out/'STATUS.json',dict(phase='supplemental-baselines',completed=44+len(rows),expected=68,
                workers=WORKERS,updated=time.time()))
    return rows


def cap_receipt(out, rows, config):
    """Flatten independently audited max-tier checks, preserving source hashes."""
    from .budget import standard_profile_hash
    from .calibration import verify_calibration
    from .worker import _load
    by_id = {r['map_id']:r for r in rows if r['arm']=='pipeline' and r['cohort']=='envelope24'}
    envelope = [m for m in config['maps'] if m['cohort']=='envelope24']
    evidence = []
    for item in envelope:
        row = by_id.get(item['map_id'],driver._failure_row(dict(item,arm='pipeline'),'No native baseline result'))
        rule = item['calibration_rules'][1]
        nested = (row.get('calibration_rechecks') or {}).get('max_tiers')
        if nested is not None:
            row = dict(row,**nested)
        else:
            row = dict(row,counterfactual_rule=rule,counterfactual_rule_sha256=object_hash(rule),
                       counterfactual_instruction_recheck=None)
        target = out/'cap-evidence'/(object_hash(item['map_id'])[:20]+'.json')
        atomic_json(target,row)
        evidence.append(dict(map_path=item['path'],map_sha256=item['sha256'],audit_row=str(target),
            audit_sha256=sha256(target),rule_sha256=object_hash(rule),cap_reduction=CAP_REDUCTION))
    receipt = dict(schema_version='sealed-tier-calibration/1',split='development',fixture_surrogate=False,
        cap_reduction=CAP_REDUCTION,profile_sha256=standard_profile_hash(),
        binding_sha256=sha256(HERE.parent/'binding/adapter.py'),instructions_sha256=sha256(HERE/'instructions.py'),
        development_list_sha256=sha256(HERE.parent/'maps/dev.txt'),evidence=evidence)
    path = out/'cap-calibration.json'
    atomic_json(path,receipt)
    try:
        verified = verify_calibration(path)
        atomic_json(out/'cap-calibration-verified.json',verified)
    except (HarnessError,OSError,ValueError) as error:
        verified = dict(passed=False,reason=str(error),release_blocked=True)
        atomic_json(out/'cap-calibration-blocked.json',verified)
    return verified


def measured_projection(rows, *, prepass_bound_s=1800):
    """Measured worker-slot times; hard bound and admission are named separately."""
    cells = {}
    for row in rows:
        elapsed = row.get('child_elapsed_s')
        if type(elapsed) not in (int,float):
            continue
        audit = row.get('audit_elapsed_s') or 0.
        service = max(elapsed+audit,(row.get('driver_elapsed_s') or 0.)-(row.get('driver_admission_wait_s') or 0.))
        cell = cells.setdefault((row['cohort'],row['arm']),[])
        cell.append(service)
    work = 0.
    table = []
    populations = dict(tiny15=15,small30=30,envelope24=24)
    for cohort,n in populations.items():
        arms = ('pipeline','twostep','instruction','twostep_300') if cohort=='envelope24' else ('pipeline','twostep')
        for arm in arms:
            values = cells.get((cohort,arm),[])
            if not values:
                return dict(status='incomplete',missing=[cohort,arm],workers=WORKERS,estimated_hours=None)
            mean = statistics.mean(values)
            work += n*mean
            table.append(dict(cohort=cohort,arm=arm,n=len(values),mean_s=mean,max_s=max(values)))
    longest = max(v for values in cells.values() for v in values)
    return dict(status='measured development projection',workers=WORKERS,
        ideal_slot_hours=work/WORKERS/3600,
        estimated_hours=(work/WORKERS+longest+prepass_bound_s)/3600,
        prepass_seconds_reserved=prepass_bound_s,scheduling_tail_seconds_reserved=longest,
        per_map=table,admission_hours='unbounded waiting; excluded from arithmetic',
        conditions='Final fleet idle:18 enabled cores,GPU idle,load1<=6,other_fleet_work:[],fresh telemetry. Four workers,one thread each.',
        limitations='Development GPU busy and measured admission load retained. Recorded reader only; actual3.1 reader gets hard1800s reserve. Projection, not a clean-machine measurement.')


def finish(out, rows, config):
    from .budget import standard_profile_hash
    from .readers import ADAPTER
    try:
        driver.verify_freeze(out/'run')
    except (HarnessError,OSError,ValueError) as error:
        atomic_json(out/'FINISH-BLOCKED.json',dict(reason=str(error),rows=len(rows),source_integrity=False))
        return
    readings=[]
    for parent in (out/'run/jobs',out/'calibration-baselines'):
        for path in parent.glob('*/DISPATCHED.json'):
            reading=read_json(path).get('admission',{})
            if type(reading.get('load1')) in (int,float):
                readings.append(reading)
    projection = measured_projection(rows)
    atomic_json(out/'runtime-projection.json',projection)
    loads=[r['load1'] for r in readings]
    observed=dict(load1_min=min(loads) if loads else None,load1_max=max(loads) if loads else None,
        enabled_cores=sorted({r['cores'] for r in readings}),dispatch_readings=len(readings))
    atomic_json(out/'observed-load.json',observed)
    calibration = cap_receipt(out,rows,config)
    harness_errors=sum(r.get('status') in {'error','not_run'} for r in rows)
    clean=len(rows)==68 and harness_errors==0
    if projection.get('status') == 'measured development projection':
        receipt = dict(schema_version='sealed-runtime-budget/2',split='development',fixture_surrogate=False,
            planners_stubbed=False,workers=WORKERS,profile_sha256=standard_profile_hash(),
            study_complete=len(rows)==68,harness_errors=harness_errors,
            binding_sha256=sha256(HERE.parent/'binding/adapter.py'),twostep_s=15,twostep_finalize_s=15,
            arm_deadlines_s=DEADLINES,serial_reader_reserve_s=1800,reader_inference_measured=False,
            per_map=projection['per_map'],final_idle_condition=dict(enabled_cores=18,max_load1=6,other_fleet_work=[]),
            observed_development_load=observed,
            original_freeze_sha256=sha256(out/'run/freeze.json'),
            development_conditions='GPU busy; real fresh >=8-core/load<=45 admission; four native workers',
            caveat=projection['limitations'])
        atomic_json(out/'runtime-budget.json',receipt)
        launch = dict(mode='sealed',workers=WORKERS,profile='standard',required_enabled_cores=18,
            twostep_budget_s=30,twostep_s=15,twostep_finalize_s=15,arm_deadlines_s=DEADLINES,
            audit_timeout_s=300,prepass_timeout_s=1800,runtime_measurement=str(out/'runtime-budget.json'),
            cap_calibration_receipt=str(out/'cap-calibration.json'),
            reader_factory='experiments.mvp.pipeline.sealed.readers:adopted_reader',
            parser_options=dict(adapter_sha256=sha256(ADAPTER/'adapters.safetensors')))
        if clean:
            atomic_json(out/'launch-config.json',launch)
    atomic_json(out/'FINAL.json',dict(t=time.time(),rows=len(rows),workers=WORKERS,
        expected_trials=68,trial_population_complete=len(rows)==68,
        harness_errors=harness_errors,launch_config_ready=clean,
        cap_calibration=calibration,runtime=projection,sealed_maps_opened=0,planners_stubbed=False,
        reader='authorized recorded development answers; adapter-v2 prepass at3.1'))
    lines = ['# Real development dry run','',f'Four workers; {len(rows)} native map/arm rows. No planner stubs.',
        'Reader: recorded expected answers only; adopted adapter-v2 runs at3.1.','',
        'Observed dispatch load: '+json.dumps(observed,sort_keys=True),'',
        f"Projected hours: {projection.get('estimated_hours')} (includes1800s reader reserve and one scheduling tail; admission waits additional).",'',
        projection.get('conditions','Timings incomplete; no runtime claim.'),'',
        '| Cohort | Arm | n | Mean worker+audit seconds | Maximum seconds |','|---|---|---:|---:|---:|']
    for row in projection.get('per_map',[]):
        lines.append(f"| {row['cohort']} | {row['arm']} | {row['n']} | {row['mean_s']:.2f} | {row['max_s']:.2f} |")
    lines += ['', 'Cap calibration: '+json.dumps(calibration,sort_keys=True),'']
    (out/'RESULTS.md').write_text('\n'.join(lines))


def run(out_dir):
    out = public_path(out_dir)
    if not out.is_relative_to(HERE):
        raise HarnessError('Study output must be inside sealed/')
    out.mkdir(parents=True,exist_ok=True)
    exclusive_json(out/'STUDY-STARTED.json',dict(t=time.time(),workers=WORKERS))
    atomic_json(HERE/'measurements/latest-native-study.json',dict(path=str(out),started=time.time()))
    rows, config = [], None
    try:
        config = prepare(out)
        atomic_json(out/'STATUS.json',dict(phase='admission-and-cache-seeding',workers=WORKERS,expected=68,t=time.time()))
        reading = admission()
        from .native_runtime import seed
        atomic_json(out/'seed-admission.json',reading)
        seed(out)
        driver.freeze(config,out/'run')
        atomic_json(out/'STATUS.json',dict(phase='native-sample',workers=WORKERS,expected=68,t=time.time()))
        driver.run(out/'run',admission_wait=admission)
        rows = read_json(out/'run/rows.json')
        if any(row.get('child_cleanup_failed') or row.get('audit_cleanup_failed') for row in rows):
            raise HarnessError('Owned group cleanup unconfirmed; supplemental CPU work refused')
        supplemental = _supplement(out,config)
        rows += supplemental
        driver.verify_freeze(out/'run')
    except BaseException as error:
        atomic_json(out/'STUDY-FAILED.json',dict(t=time.time(),reason=f'{type(error).__name__}: {error}'))
        raise
    finally:
        if not rows and (out/'run/rows.json').exists():
            rows = read_json(out/'run/rows.json')
        atomic_json(out/'all-rows.json',rows)
        if config is not None and rows:
            finish(out,rows,config)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--out-dir',required=True)
    args = cli.parse_args()
    run(args.out_dir)


if __name__=='__main__':
    main()
