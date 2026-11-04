"""Resumable, admission-controlled hero screening on unsealed development maps."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, indent=2, default=str, allow_nan=False) + '\n')
    tmp.replace(path)


def record_path(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def resumed_frames(previous, instance, plan, epoch):
    """A frame memo is usable only for every exact input and its numerical epoch."""
    from app import stage_physics
    expected = stage_physics.input_digests(instance, plan)
    if not isinstance(previous, dict) or previous.get('physics_epoch') != epoch or \
            any(previous.get(key) != value for key, value in expected.items()):
        return {}
    frames = previous.get('frames')
    if not stage_physics._frames_ok(frames, len(plan.moves)):
        return {}
    return {frame['step']: frame for frame in frames}


def wait_owned_call(child, progress, *, allowance_s=900):
    """Measure this owned worker after admission; shutdown is separate work."""
    progress = Path(progress)
    began = None
    while True:
        try:
            admitted = json.loads(progress.read_text())
            recorded = admitted.get('call_started_monotonic')
            if admitted.get('pid') == child.pid and isinstance(recorded, (int, float)) and \
                    not isinstance(recorded, bool) and math.isfinite(recorded) and 0 <= recorded <= time.monotonic():
                began = recorded
        except (OSError, ValueError, TypeError):
            pass
        if began is not None or child.poll() is not None:
            break
        time.sleep(.2)
    timed_out = False
    shutdown_s = 0.
    try:
        child.wait(timeout=max(.1, allowance_s-(time.monotonic()-began)) if began is not None else None)
        stopped = time.monotonic()
        ended = stopped
    except subprocess.TimeoutExpired:
        timed_out = True
        stopped = time.monotonic()
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        ended = time.monotonic()
        shutdown_s = ended-stopped
    return timed_out, dict(timeout_allowance_s=allowance_s,
        owned_call_wall_s=stopped-began if began is not None else None,
        shutdown_wall_s=shutdown_s,
        owned_call_total_wall_s=ended-began if began is not None else None,
        timing_scope='Monotonic admitted owned-worker elapsed until observed completion or timeout; shutdown measured separately. Not a measurement of native search alone.')


def admit():
    while True:
        try:
            data = json.loads((ROOT/'experiments/fleet/core-state.json').read_text())
            mask = int(data['mask'], 0) if isinstance(data['mask'], str) else int(data['mask'])
            age = time.time() - float(data['t'])
            if mask >= 0 and mask.bit_count() >= 8 and 0 <= float(data['load1']) <= 45 and 0 <= age < 120:
                return dict(data, age_s=age)
        except (OSError, KeyError, TypeError, ValueError):
            pass
        print('WAIT fleet admission', flush=True)
        time.sleep(15)


def binding_fingerprint():
    from experiments.mvp.pipeline.lattice.store import binding_fingerprint as fingerprint
    return fingerprint()


@contextmanager
def isolated_runtime():
    """Redirect adapter-owned derived caches inside this feature's write fence."""
    from experiments.mvp.pipeline.binding import runtime
    from experiments.mvp.pipeline.lattice.runtime import isolated_binding
    with isolated_binding():
        with runtime.runtime_cache_scope():
            yield


@contextmanager
def withheld_plot_scope():
    """Compatibility alias for the shared discarded-plot optimization."""
    from experiments.mvp.pipeline.lattice.runtime import discarded_plot_scope
    with discarded_plot_scope() as policy:
        yield policy


def describe(path):
    from stowage.io import load_instance
    from experiments.mvp.pipeline.runner.envelope import check
    from experiments.mvp.reader.schema import context_from_instance
    from experiments.mvp.pipeline.contract.schema import sha256_json
    instance = load_instance(path)
    boxes = [c for c in instance.containers if c.id in instance.load_list]
    return dict(path=str(Path(path).relative_to(ROOT)), instance_id=instance.id,
                instance_sha256=sha256_json(instance.model_dump(mode='json')),
                file_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                boxes=len(boxes), reefers=sum(c.reefer for c in boxes),
                dg=[dict(id=c.id, imdg_class=c.dg.imdg_class) for c in boxes if c.dg],
                pods=sorted({c.pod for c in boxes}), envelope=check(instance),
                context=context_from_instance(instance))


def story_candidates(instance, plan):
    """DG instructions grounded in the base's actual placements and ship regions."""
    from stowage.conventions import odd_bays_of, parse_slot
    from stowage.moves import apply_plan
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from experiments.mvp.pipeline.lattice.instructions import sentence
    boxes = {box.id: box for box in instance.containers}
    placements = apply_plan(instance,plan).final_state.ship.placements
    context = context_from_instance(instance)
    classes = sorted({boxes[p.container_id].dg.imdg_class for p in placements if boxes[p.container_id].dg})
    candidates=[]
    for cls in classes:
        references=[]
        if context['has_accommodation']:
            references.append((dict(kind='accommodation'),[],True))
        for bay in [b for b in context['bays'] if b%2]:
            real=[p.container_id for p in placements if
                  set(odd_bays_of(parse_slot(p.slot).bay)) & set(odd_bays_of(bay)) and
                  (not boxes[p.container_id].dg or boxes[p.container_id].dg.imdg_class!=cls)]
            if real:
                references.append((dict(kind='bay',bay=bay),real,any(boxes[c].reefer for c in real)))
        for reference,real,preferred in references:
            for radius in (1,2,3):
                body=dict(type='dg_keep_away',imdg_class=cls,min_bays=radius,away_from=reference)
                envelope=dict(schema_version='stowlm.reader/2',constraint=body)
                answer=validate_answer(json.dumps(envelope),context)
                violations=list(intrinsic_violations(instance,answer,plan))
                if violations:
                    candidates.append(dict(sentence=sentence(body),canonical_rules=[envelope],
                        base_violations=violations,anchor=reference,anchor_container_ids=real,
                        anchor_reefer_ids=[c for c in real if boxes[c].reefer],
                        dg_container_ids=[p.container_id for p in placements if boxes[p.container_id].dg and boxes[p.container_id].dg.imdg_class==cls],
                        accommodation_regions=[r.model_dump(mode='json') for r in instance.vessel.exclusion_regions if r.kind=='accommodation'] if reference['kind']=='accommodation' else [],
                        preferred_reference=preferred,status='base_violated_candidate_not_yet_replanned'))
    return sorted(candidates,key=lambda r:(r['anchor']['kind']!='accommodation',not r['preferred_reference'],len(r['base_violations']),r['sentence']))


def solve(path, target, campaign=None):
    # This worker owns only multiprocessing children created by its binding call.
    import multiprocessing
    import signal
    def stopped(signum, frame):
        children = multiprocessing.active_children()
        for child in children:
            if child.is_alive():
                child.terminate()
        for child in children:
            child.join(timeout=.1)
            if child.is_alive():
                child.kill()
                child.join(timeout=.1)
        raise SystemExit('standard budget reached')
    signal.signal(signal.SIGTERM, stopped)
    from stowage.io import load_instance
    from experiments.mvp.pipeline.lattice.batch import _binding_call
    from experiments.mvp.pipeline.runner.profiles import get_profile, engine_kwargs
    target = Path(target)
    row = describe(path)
    row['campaign_id']=campaign
    row['binding_source_sha256'] = binding_fingerprint()
    row['admission'] = admit()
    profile = get_profile('standard')
    row['profile'] = dict(profile, config=asdict(profile['config']))
    row.update(started_utc=datetime.now(timezone.utc).isoformat(), pid=os.getpid(), orchestrator_pid=os.getppid(),
               call_started_monotonic=time.monotonic())
    write(target.with_suffix('.progress.json'), row)
    from experiments.mvp.pipeline.lattice.store import binding_source_files
    write(target.with_suffix('.progress.source.json'),dict(
        binding_source_sha256=row['binding_source_sha256'],captured_unix=time.time(),
        source_files={str((HERE.parent/'binding'/name).relative_to(ROOT)):dict(
            sha256=hashlib.sha256((HERE.parent/'binding'/name).read_bytes()).hexdigest(),
            mtime_s=(HERE.parent/'binding'/name).stat().st_mtime) for name in binding_source_files()},
        pid=row['pid'],orchestrator_pid=row['orchestrator_pid'],
        worker_started_unix=datetime.fromisoformat(row['started_utc']).timestamp(),
        all_source_mtimes_before_worker_start=True,binding_source_at_launch_known=True,
        provenance='Runtime source fingerprint captured before this worker invokes binding.'))
    started = row['call_started_monotonic']
    def incumbent(event):
        write(target.with_suffix('.incumbent.json'), dict(event, profile=row['profile'], admission=row['admission']))
        print(json.dumps(dict(event='incumbent', path=row['path'], lifts=event['lifts'], wall_s=event['wall_s'])), flush=True)
    try:
        instance = load_instance(path)
        result, evidence = _binding_call(instance, [], admission=row['admission'],
            **engine_kwargs(profile), on_incumbent=incumbent)
        row.update(evidence=evidence, passed=bool(evidence.get('passed')))
        if result is not None and result.plan is not None:
            row['plan'] = result.plan.model_dump(mode='json')
    except Exception as exc:
        row.update(passed=False, error=f'{type(exc).__name__}: {exc}')
    elapsed = time.monotonic()-started
    row.update(wall_s=elapsed, owned_call_wall_s=elapsed, owned_call_total_wall_s=elapsed,
               shutdown_wall_s=0., timeout_allowance_s=900,
               timing_scope='Monotonic admitted owned-worker call, including preflight; native search alone is not separately measured.',
               computed_utc=datetime.now(timezone.utc).isoformat())
    row['binding_source_after_sha256'] = binding_fingerprint()
    if row['binding_source_after_sha256'] != row['binding_source_sha256']:
        row.update(passed=False,status='rerun_required',reason='binding sources changed during solve')
    write(target, row)


def sweep(target):
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.checker import check
    from app import stage_physics
    from experiments.mvp.pipeline.lattice.gates import engine_identity, gauge_verdict, runtime_cache_scope
    target = Path(target)
    row = json.loads(target.read_text())
    if not row.get('passed') or not row.get('plan'):
        return row
    instance, plan = load_instance(ROOT/row['path']), MovePlan.model_validate(row['plan'])
    digests = stage_physics.input_digests(instance, plan)
    if row.get('instance_sha256') != digests['instance_sha256']:
        raise ValueError('Screen source row instance identity does not match the current instance')
    row['gauge_admission'] = admit()
    epoch = stage_physics.physics_epoch()
    framefile = target.with_suffix('.frames.json')
    done = {}
    if framefile.exists():
        try:
            done = resumed_frames(json.loads(framefile.read_text()), instance, plan, epoch)
        except (OSError, ValueError, TypeError):
            pass
    started = time.monotonic()
    identity = engine_identity()
    drift_before = stage_physics.physics_drift()
    class RejectedFrame(Exception):
        pass

    def failed(frame):
        return frame.get('error') or frame.get('stability_passed') is not True or frame.get('strength_passed') is not True

    def checkpoint(k, frame):
        if not stage_physics._frames_ok([frame], len(plan.moves)) or frame['step'] != k:
            raise ValueError('Native frame structure or step does not match the requested crane frame')
        done[k] = frame
        write(framefile, dict(**digests, physics_epoch=epoch, frames=[done[k] for k in sorted(done)]))
        if failed(frame):
            print(json.dumps(dict(event='gauge_reject', path=row['path'], frame=k, stability=frame.get('stability_violations'), strength=frame.get('strength_violations'), error=frame.get('error'))), flush=True)
            raise RejectedFrame()
    with runtime_cache_scope(), withheld_plot_scope() as plot_policy:
        report = check(instance, plan)
        from stowage.frozen import require_clean
        require_clean(instance, plan, report, what='lattice hero screening')
        try:
            if any(failed(frame) for frame in done.values()):
                raise RejectedFrame()
            physics = stage_physics.compute_physics(instance, plan, frames=list(range(len(plan.moves)+1)), workers=1, done=done, on_frame=checkpoint)
        except RejectedFrame:
            physics = dict(stage_physics.physics_header(instance,'all_until_first_failure'),
                           frames=[done[k] for k in sorted(done)],n_lifts=len(plan.moves),
                           stopped_after_first_failure=True)
    bad = [f for f in physics['frames'] if f.get('error') or f.get('stability_passed') is not True or f.get('strength_passed') is not True]
    gauges = gauge_verdict(physics, len(plan.moves))
    current_identity = engine_identity()
    source_errors = [reason for reason in (drift_before, stage_physics.physics_drift()) if reason]
    if stage_physics.physics_epoch() != epoch:
        source_errors.append('physics epoch changed during the crane-frame sweep')
    if not identity['passed'] or current_identity != identity:
        source_errors.append('engine identity changed or does not match pinned manifest')
    gate = dict(passed=bool(report.passed and report.count is not None and gauges['passed'] and not source_errors),
                instance_sha256=digests['instance_sha256'], plan_sha256=digests['plan_sha256'],
                checker=report.model_dump(mode='json'), rules=[], gauges=gauges,
                lifts=report.count.weighted_nonproductive if report.count is not None else None,
                total_lifts_now=report.count.total_lifts_now if report.count is not None else None,
                engine_identity=identity, admission=row['gauge_admission'],
                checked_utc=datetime.now(timezone.utc).isoformat(), source_errors=source_errors,
                wall_s=time.monotonic()-started)
    row.update(full_checker=report.model_dump(mode='json'), gauge=dict(passed=not bad, frames=len(physics['frames']), expected_frames=len(plan.moves)+1, bad_steps=[f['step'] for f in bad], wall_s=time.monotonic()-started, physics_epoch=stage_physics.physics_epoch()),
               gate=gate, passed=gate['passed'], gauge_frames=record_path(framefile),
               gauge_policy='Every accepted frame is checked; a candidate is rejected at the first failed frame.',
               plot_optimization=plot_policy)
    if source_errors:
        row.update(status='rerun_required',reason='Gauge or checker sources changed; retry with a fresh process.')
    else:
        row['status']='checked_plan' if gate['passed'] else 'gauge_failed'
    write(target, row)
    return row


def screen(path, target, campaign=None):
    target = Path(target)
    if not target.exists():
        admit()
        log = target.with_suffix('.solve.log').open('a')
        command=[sys.executable, '-m', 'experiments.mvp.pipeline.lattice.screen', 'solve', str(path), str(target)]
        if campaign:
            command += ['--campaign',campaign]
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        write(target.with_suffix('.process.json'), dict(pid=child.pid, started_at=time.time(), timeout_s=900))
        progress = target.with_suffix('.progress.json')
        timed_out, timing = wait_owned_call(child, progress, allowance_s=900)
        if timed_out:
            incumbent = target.with_suffix('.incumbent.json')
            progress = target.with_suffix('.progress.json')
            row = json.loads(progress.read_text()) if progress.exists() else describe(path)
            if incumbent.exists():
                event = json.loads(incumbent.read_text())
                row.update(event, evidence=event, passed=True, budget_status='timeout_with_checked_incumbent')
            else:
                row.update(passed=False, budget_status='no_checked_plan_at_budget')
            row.update(timing, wall_s=timing['owned_call_total_wall_s'],
                       native_search_wall_s=None, computed_utc=datetime.now(timezone.utc).isoformat())
            row['binding_source_after_sha256'] = binding_fingerprint()
            if row.get('binding_source_sha256') != row['binding_source_after_sha256']:
                row.update(passed=False,status='rerun_required',reason='binding runtime changed during the standard call')
            write(target, row)
        if not target.exists():
            row=describe(path)
            row.update(passed=False,status='no_checked_plan',reason='Owned worker exited without a checked result; not an impossibility proof.')
            write(target,row)
        log.close()
    try:
        row = sweep(target)
    except Exception as error:
        row = json.loads(target.read_text())
        row.update(passed=False, screening_error=f'{type(error).__name__}: {error}')
        write(target,row)
    print(json.dumps({k:row.get(k) for k in ('path','passed','boxes','gauge','budget_status')}),flush=True)


def compare_plot(target,step=10):
    """Measure exact real-frame equality; no search and no plan release."""
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from app import stage_physics
    from experiments.mvp.pipeline.lattice.gates import runtime_cache_scope
    target=Path(target)
    row=json.loads(target.read_text())
    admission=admit()
    instance,plan=load_instance(ROOT/row['path']),MovePlan.model_validate(row['plan'])
    before=stage_physics.physics_drift()
    with runtime_cache_scope():
        start=time.monotonic()
        normal=stage_physics.compute_frame(instance,plan,step)
        normal_s=time.monotonic()-start
        with withheld_plot_scope() as policy:
            start=time.monotonic()
            optimized=stage_physics.compute_frame(instance,plan,step)
            optimized_s=time.monotonic()-start
    result=dict(admission=admission,pid=os.getpid(),frame=step,
        instance_sha256=row['instance_sha256'],plan_sha256=stage_physics.input_digests(instance,plan)['plan_sha256'],
        normal_wall_s=normal_s,optimized_wall_s=optimized_s,full_frame_dict_equal=normal==optimized,
        normal=normal,optimized=optimized,plot_optimization=policy,
        source_drift=[r for r in (before,stage_physics.physics_drift()) if r],
        computed_utc=datetime.now(timezone.utc).isoformat())
    write(target.with_suffix('.plot-comparison.json'),result)
    print(json.dumps({k:result[k] for k in ('pid','frame','normal_wall_s','optimized_wall_s','full_frame_dict_equal','source_drift')}),flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('screen','solve','sweep','describe','compare'))
    parser.add_argument('paths',nargs='+')
    parser.add_argument('--campaign')
    parser.add_argument('--campaign-control',type=Path,default=HERE/'campaign-control.json')
    args = parser.parse_args()
    if args.action == 'describe':
        for path in args.paths:
            print(json.dumps(describe(Path(path).resolve())))
    elif args.action == 'sweep':
        for path in args.paths:
            sweep(path)
    elif args.action == 'compare':
        compare_plot(args.paths[0],int(args.paths[1]) if len(args.paths)>1 else 10)
    else:
        path, target = args.paths
        if args.action == 'screen':
            if args.campaign_control.exists():
                active=json.loads(args.campaign_control.read_text()).get('active_campaign')
                if active!=args.campaign:
                    row=describe(Path(path).resolve())
                    row.update(passed=False,status='superseded',gauge={},campaign_id=args.campaign,
                               active_campaign=active,reason='Superseded campaign; no CPU planning or gauge work started.')
                    write(Path(target).resolve(),row)
                    return
            write(Path(target).with_suffix('.orchestrator.json'),dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat()))
        (screen if args.action == 'screen' else solve)(Path(path).resolve(), Path(target).resolve(),campaign=args.campaign)


if __name__ == '__main__':
    main()
