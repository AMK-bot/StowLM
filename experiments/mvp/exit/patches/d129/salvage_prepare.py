"""CPU-only D129 migration of saved generation0 outcomes into a fresh run.

This never trains, decodes, launches a controller, or fabricates children. The
old directory is read-only evidence; only current-epoch verified rows are ready
for the host's later inherit-only GPU phase.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
from time import perf_counter

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent/'pyproject.toml').is_file())

# Historical baseline comparability requires this precise inference recipe.
# Extra explanatory/compatibility fields are allowed; core behavior cannot vary.
REBASELINE_RECIPE_CORE=dict(name='D131-prior-loop-baseline',greedy=True,
    temperature=0.,samples=1,batched=True,batch_size=4,max_tokens=20000,
    layers=['production','lookahead','securing'],backtracking=False,
    ballast='deterministic scoped V4',ballast_s=90.,checker_watchdog_s=300.,
    lns=False,solver_cargo_fallback=False)


def _read(path):return json.loads(Path(path).read_text())


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                     allow_nan=False).encode()).hexdigest()


def _write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def _services():
    from llm.exit.code_epoch import source_snapshot
    from llm.exit.replay import checker_epoch
    try:
        from llm.exit import reverify
    except ImportError:
        # Staging entry point is CPU-only; actual activation still supplies the
        # fresh guarded llm.exit.selection/checker_watchdog implementation.
        path=Path(__file__).with_name('reverify.py')
        spec=importlib.util.spec_from_file_location('_d129_staged_reverify',path)
        reverify=importlib.util.module_from_spec(spec);spec.loader.exec_module(reverify)
    return dict(source_snapshot=source_snapshot,checker_epoch=checker_epoch,
        reverify_training_rows=reverify.reverify_training_rows,
        reverify_audit_rows=reverify.reverify_audit_rows,
        assert_training_receipt=reverify.assert_training_receipt)


def adapter_identity(parent):
    parent=Path(parent)
    return {name:_sha(parent/name) for name in ('adapters.safetensors',
        'adapters.safetensors.provenance.json','adapter_config.json')}


@contextmanager
def _released_runner(out):
    """Keep old runner absent throughout migration; never signal another PID."""
    with (Path(out)/'.runner.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('old runner still active; finish decode/selection before salvage') from error
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def _closed_source_proof(out):
    """A terminal original run can have concurrent read-only migration readers."""
    out=Path(out)
    cancel,status,compute=(out/name for name in
        ('deployment.cancel','deployment-status.json','compute.json'))
    if not cancel.is_file():raise RuntimeError('closed source requires deployment cancellation')
    deployment,finished=_read(status),_read(compute)
    if deployment.get('state') not in ('failed','completed'):
        raise RuntimeError('closed source requires terminal deployment state')
    if finished.get('status') not in ('failed','completed'):
        raise RuntimeError('closed source requires final original compute record')
    return dict(deployment_state=deployment['state'],compute_status=finished['status'],
                files={str(path.resolve()):_sha(path) for path in (cancel,status,compute)})


@contextmanager
def _source_read_lease(out, *, allow_closed_reader=False):
    """Default exclusive runner gate; D131 additionally permits terminal readers.

    The exception requires cancellation and final records, takes the original
    controller lock throughout, and hashes the complete source inventory. It
    grants no permission to write to the original run or interrupt another PID.
    """
    out=Path(out)
    with (out/'.runner.lock').open('a+') as runner:
        try:fcntl.flock(runner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            if not allow_closed_reader:
                raise RuntimeError('old runner still active; finish decode/selection before salvage') from error
            with (out/'.deployment.lock').open('a+') as controller:
                try:fcntl.flock(controller,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError as lock_error:
                    raise RuntimeError('closed source controller must be released for read-only salvage') from lock_error
                try:
                    proof=_closed_source_proof(out)
                    yield proof
                    if _closed_source_proof(out)!=proof:
                        raise RuntimeError('closed source terminal proof changed during salvage')
                finally:fcntl.flock(controller,fcntl.LOCK_UN)
        else:
            try:yield None
            finally:fcntl.flock(runner,fcntl.LOCK_UN)


def _copy(path, target):
    path,target=Path(path),Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if _sha(target)!=_sha(path):raise RuntimeError(f'salvage copy collision: {target}')
    else:shutil.copy2(path,target)


@contextmanager
def _cpu_reservation(out, estimate_s, *, resources=None, workers=1):
    """Reserve one CPU without taking a GPU lease or importing MLX.

    Public resources.reservation also serializes GPU ownership for CPU jobs.
    This migration deliberately uses its ledger-only inner context, guarded by
    a distinct per-output preparation lock; another GPU owner may work meanwhile.
    """
    if resources is None:
        from llm.exit import resources
    if type(workers) is not int or not 1<=workers<=4:
        raise ValueError('CPU reverification workers must be from1to4')
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    with (out.parent/'.salvage-preparation.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another CPU salvage preparation owns this output') from error
        owner=resources.OWNER
        resources.OWNER=f'mvp-exit/d129-reverify-{os.getpid()}'
        try:
            kwargs=dict(cores=workers,ram_gb=4 if workers==1 else 8,gpu=False,estimate_s=estimate_s)
            if workers>1:kwargs['load_policy']='cpu_reverification_d131'
            with resources._reservation(out,**kwargs):
                yield
        finally:
            resources.OWNER=owner
            fcntl.flock(lock,fcntl.LOCK_UN)


def prepare(source_out, new_out, parent, *, workers=1, defer_baseline=False):
    """Return a persisted salvage-prepared marker, ready or explicitly blocked.

    Caller reserves one CPU before invoking (CLI does this). Native libraries and
    all full-check subprocesses use one thread. Call only AFTER D124 activation
    and old runner release, in a fresh process under the new source epoch.
    """
    source_out,new_out,parent=map(lambda p:Path(p).resolve(),(source_out,new_out,parent))
    if source_out==new_out or new_out.is_relative_to(source_out) or source_out.is_relative_to(new_out):
        raise ValueError('salvage needs separate, non-nested old and new output directories')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    with _source_read_lease(source_out,allow_closed_reader=defer_baseline and workers>1) as proof:
        return _prepare_released(source_out,new_out,parent,workers=workers,
            defer_baseline=defer_baseline,closed_source_proof=proof)


def _prepare_released(source_out,new_out,parent,*,workers=1,defer_baseline=False,closed_source_proof=None):
    started=perf_counter();s=_services();sources=s['source_snapshot']()
    manifest_path=source_out/'gen-000/instances.json'
    manifest=_read(manifest_path)
    identity=adapter_identity(parent)
    population=manifest['population']
    if (not population or str(parent) not in manifest['adapter_identity']
            or manifest['adapter_identity'][str(parent)]!=identity
            or Path(population[0]['adapter']).resolve()!=parent):
        raise ValueError('original parent does not match generation0 manifest content identity')
    # No old child, checkpoint, summary or terminal evidence is imported.
    paths=[Path(path).resolve() for path in manifest['instances']]
    if not paths or len(set(paths))!=len(paths):
        raise ValueError('generation0 manifest requires distinct maps')
    if len(paths)!=manifest['config']['tiny']+manifest['config']['small']:
        raise ValueError('generation0 manifest map count differs from configured complete generation')
    selections=[];decodes=[]
    for index,path in enumerate(paths):
        selection=source_out/'gen-000/cases'/f'{index:03d}'/'selection.json'
        row=_read(selection)
        if Path(row['instance_path']).resolve()!=path:
            raise ValueError('selection identity differs from manifest map')
        selections.append(selection)
        for member,config in enumerate(population):
            adapter=Path(config['adapter']).resolve()
            if adapter_identity(adapter)!=manifest['adapter_identity'].get(str(adapter)):
                raise ValueError('decode population adapter changed since generation0')
            generation=selection.parent/f'decode-{member}'/'generation.json'
            value=_read(generation) # Existence + valid completed publication required.
            if not isinstance(value,dict) or 'samples' not in value or 'candidates' not in value:
                raise ValueError('saved decode is not a complete generation artifact')
            decodes.append(generation)
    schedule=source_out/'schedule.json';schedule_state=_read(schedule)
    used_generation=schedule_state.get('generations',{}).get('0',[])
    used_paths={(Path(manifest['config']['corpus'])/entry['path']).resolve()
                for entry in used_generation}
    if not set(paths).issubset(used_paths):
        raise ValueError('copied schedule does not reserve all generation0 maps')
    baseline_path=source_out/'audit-baseline/summary.json'
    old_baseline=_read(baseline_path)
    registration=_read(source_out/'audit-rule.json')
    frozen=[Path(row['path']).resolve() for row in registration['maps']]
    if len(frozen)!=30 or len(set(frozen))!=30:
        raise ValueError('original audit registration is not frozen30')
    for row in registration['maps']:
        if _sha(row['path'])!=row['sha256']:
            raise ValueError('frozen audit map content changed')
    binding=_read(source_out/'audit-baseline/baseline-binding.json')
    if binding['adapter']!=identity or Path(binding['baseline_adapter']).resolve()!=parent:
        raise ValueError('original baseline adapter differs from generation0 parent')
    if binding['maps']!=registration['maps']:
        raise ValueError('baseline and frozen audit registrations differ')
    inputs=set([manifest_path,schedule,baseline_path,source_out/'audit-rule.json',
        source_out/'audit-baseline/baseline-binding.json',*decodes,*selections,*paths,*frozen])
    if closed_source_proof:inputs.update(Path(path) for path in closed_source_proof['files'])
    for selection in selections:
        row=_read(selection)
        if row.get('winner_record'):inputs.add(Path(row['winner_record']).resolve())
        inputs.update(Path(c['plan_path']).resolve() for c in row.get('candidates',()) if c.get('plan_path'))
    optional=['baseline-timing.json','gen-000/phase-status.json']
    inputs.update(source_out/name for name in optional if (source_out/name).exists())
    inventory={str(path):_sha(path) for path in sorted(inputs)}
    signature=dict(schema='exit-salvage-input-v1',source_out=str(source_out),new_out=str(new_out),
        parent=str(parent),parent_identity=identity,current_sources=sources,inputs=inventory)
    if workers>1 or defer_baseline:
        signature.update(workers=workers,defer_baseline=defer_baseline)
    if closed_source_proof:signature['closed_source_read_lease']=closed_source_proof
    signature_path=new_out/'salvage-input.json'
    if new_out.exists() and not signature_path.exists() and any(new_out.iterdir()):
        # CLI's reservation has its own subdirectory, never a runner artifact.
        allowed={'salvage-compute','.salvage-preparation.lock'}
        if defer_baseline:
            allowed|={'rebaseline-current','audit-baseline','baseline-timing.json','rebaseline-ready.json','RESUME-HOST.sh'}
        if any(path.name not in allowed and not (
                defer_baseline and path.name.startswith('audit-rule') and path.suffix=='.json')
                for path in new_out.iterdir()):
            raise ValueError('fresh salvage output already contains unrelated artifacts')
    if signature_path.exists() and _read(signature_path)!=signature:
        raise RuntimeError('salvage resume source/parent/epoch identity changed; use a fresh output')
    _write(signature_path,signature)
    marker_path=new_out/'salvage-prepared.json'
    if marker_path.exists():
        marker=_read(marker_path)
        if (marker.get('schema')!='exit-salvage-prepared-v1'
                or marker.get('prepared_sha256')!=_digest({k:v for k,v in marker.items() if k!='prepared_sha256'})
                or marker.get('source_out')!=str(source_out) or marker.get('new_out')!=str(new_out)
                or marker.get('parent_identity')!=identity or marker.get('current_sources')!=sources):
            raise ValueError('prepared marker content/identity mismatch')
        for path,sha in marker.get('output_sha256',{}).items():
            if _sha(path)!=sha:raise ValueError('prepared output content changed')
        if marker['status']=='ready' or marker.get('training_rows_ready'):
            s['assert_training_receipt'](marker['receipt_path'],selections=selections,
                training_epoch=sources,model_identity={'adapter':identity})
        return marker
    for name in (('schedule.json',) if defer_baseline else ('schedule.json','audit-rule.json')):
        _copy(source_out/name,new_out/name)
    historical=new_out/'historical-generation0'
    for name in ('gen-000/instances.json', 'gen-000/phase-status.json', 'baseline-timing.json',
                 'audit-baseline/summary.json','audit-baseline/baseline-binding.json','audit-rule.json'):
        if (source_out/name).is_file():_copy(source_out/name,historical/name)
    for path in decodes:
        _copy(path,new_out/path.relative_to(source_out))
    manifest_new={**manifest,'source_pins':sources,
        'config':{**manifest['config'],'out':str(new_out),'stop_file':str(new_out/'deployment.cancel')},
        'salvaged_from':dict(source_out=str(source_out),manifest_sha256=_sha(manifest_path),
            source_pins=manifest['source_pins'],decode_epoch_preserved=True,
            training_rows_require_current_epoch_receipt=True)}
    _write(new_out/'gen-000/instances.json',manifest_new)
    training_start=perf_counter()
    reverify_dir=new_out/'gen-000'/('reverification-d131' if workers>1 else 'reverification')
    verified=s['reverify_training_rows'](selections,
        output_dir=reverify_dir,training_epoch=sources,
        model_identity={'adapter':identity},corpus_root=manifest['config']['corpus'],
        ballast_s=manifest['config'].get('ballast_s',90),check_timeout_s=300,workers=workers)
    training_wall=perf_counter()-training_start
    audit_start=perf_counter()
    if defer_baseline:
        old_passes=[row for row in old_baseline if row.get('passed')]
        audit=dict(status='pending_host_fresh_decode',rows=None,
            original_measurement=dict(maps=len(old_baseline),pass_count=len(old_passes),
                mean_weighted_moves=sum(row['weighted_moves'] for row in old_passes)/len(old_passes)
                    if old_passes else None),
            current_epoch_reverification=None,changed_to_fail=None,changed_to_pass=None,unavailable=None)
        _write(new_out/'baseline-pending.json',dict(schema='exit-baseline-pending-v1',
            status='pending_host_fresh_decode',parent=str(parent),parent_identity=identity,
            current_sources=sources,maps=registration['maps'],historical=audit['original_measurement'],
            reason='D131 parallel CPU train-row recheck; separate host GPU rebaseline required'))
    else:
        audit=s['reverify_audit_rows'](old_baseline,frozen_paths=frozen,
            output_dir=new_out/'audit-baseline',training_epoch=sources,
            model_identity={'adapter':identity},corpus_root=manifest['config']['corpus'],
            ballast_s=manifest['config'].get('ballast_s',90),check_timeout_s=300)
    audit_wall=perf_counter()-audit_start
    if (s['source_snapshot']()!=sources or adapter_identity(parent)!=identity or any(
            _sha(path)!=sha for path,sha in inventory.items())):
        raise RuntimeError('source/parent/old artifacts changed during salvage; no ready marker published')
    if verified['status']=='ready':
        s['assert_training_receipt'](reverify_dir/'receipt.json',
            selections=selections,training_epoch=sources,model_identity={'adapter':identity})
    baseline_binding=dict(adapter=identity,baseline_adapter=str(parent),
        checker_epoch=s['checker_epoch'](),maps=registration['maps'])
    if not defer_baseline:
        _write(new_out/'audit-baseline/summary.json',audit['rows'])
        _write(new_out/'audit-baseline/baseline-binding.json',baseline_binding)
    old_timing=_read(source_out/'baseline-timing.json') if (source_out/'baseline-timing.json').exists() else {}
    if not defer_baseline:
        _write(new_out/'baseline-timing.json',dict(audit_s=0.,historical_audit=old_timing,
            cpu_epoch_reverification_s=audit_wall,baseline_pending=False,
            measurement_kind='saved own cargo actions rechecked; no new baseline GPU decode'))
    marker=dict(schema='exit-salvage-prepared-v1',status=(
        'training_ready_baseline_pending' if defer_baseline and verified['status']=='ready' else verified['status']),
        source_out=str(source_out),new_out=str(new_out),parent=str(parent),parent_identity=identity,
        current_sources=sources,original_selection_paths=[str(path) for path in selections],
        receipt_path=str(reverify_dir/'receipt.json'),
        selections=verified['selections'],winner_records=verified['winner_records'],pairs=verified['pairs'],
        counts=verified['counts'],baseline_counts=dict(original=audit['original_measurement'],
            current=audit['current_epoch_reverification'],changed_to_fail=audit['changed_to_fail'],
            changed_to_pass=audit['changed_to_pass'],unavailable=audit['unavailable']),
        original_timing_progress=_read(source_out/'gen-000/phase-status.json')
            if (source_out/'gen-000/phase-status.json').exists() else None,
        original_baseline_timing=old_timing,old_manifest_sha256=_sha(manifest_path),
        original_timings=_read(source_out/'gen-000/phase-status.json')
            if (source_out/'gen-000/phase-status.json').exists() else None,
        baseline_reverification=audit,config=manifest_new['config'],population=population,
        timings=dict(training_reverification_s=training_wall,baseline_reverification_s=audit_wall,
                     preparation_wall_s=perf_counter()-started),
        generation=0,next_phase='await_host_baseline_then_inherit' if defer_baseline else 'inherit_only',
        children_trained=False,training_rows_ready=verified['status']=='ready',baseline_pending=defer_baseline,
        workers=workers,closed_source_read_lease=closed_source_proof,
        capacity_choice=(f'D131 {workers} new CPU workers; original terminal-run migration reader remains reserved separately'
                         if closed_source_proof else f'{workers} reserved CPU workers'))
    critical_outputs=[new_out/name for name in ('gen-000/instances.json','schedule.json')]
    critical_outputs.extend([new_out/'baseline-pending.json'] if defer_baseline else
        [new_out/'audit-baseline/summary.json',new_out/'audit-baseline/baseline-binding.json',
         new_out/'audit-rule.json',new_out/'baseline-timing.json'])
    marker['output_sha256']={str(path):_sha(path) for path in critical_outputs}
    if closed_source_proof:
        # Bind the terminal-source proof through host attach and every training
        # update too, so a later re-opened original deployment cannot reuse it.
        if _closed_source_proof(source_out)!=closed_source_proof:
            raise RuntimeError('closed source terminal proof changed before preparation commit')
        marker['output_sha256'].update(closed_source_proof['files'])
    marker['prepared_sha256']=_digest(marker)
    _write(marker_path,marker)
    return marker


def attach_rebaseline(new_out):
    """CPU-only join: admit a host's measured new-epoch audit before inheritance."""
    new_out=Path(new_out).resolve()
    prepared_path=new_out/'salvage-prepared.json'
    marker=_read(prepared_path)
    if marker.get('prepared_sha256')!=_digest({k:v for k,v in marker.items() if k!='prepared_sha256'}):
        raise ValueError('prepared marker content hash mismatch')
    if not marker.get('training_rows_ready'):
        raise ValueError('no verified training rows to attach baseline to')
    s=_services()
    if marker['current_sources']!=s['source_snapshot']() or adapter_identity(marker['parent'])!=marker['parent_identity']:
        raise ValueError('prepared source/parent identity changed')
    for path,sha in marker['output_sha256'].items():
        if _sha(path)!=sha:raise ValueError('prepared output content changed')
    s['assert_training_receipt'](marker['receipt_path'],selections=marker['original_selection_paths'],
        training_epoch=marker['current_sources'],model_identity={'adapter':marker['parent_identity']})
    ready_path=new_out/'rebaseline-ready.json';ready=_read(ready_path)
    if (ready.get('schema')!='exit-d131-rebaseline-v1' or ready.get('status')!='ready'
            or ready.get('ready_sha256')!=_digest({k:v for k,v in ready.items() if k!='ready_sha256'})):
        raise ValueError('host rebaseline marker schema/status/content mismatch')
    spec=ready['specification']
    recipe=spec.get('recipe',{})
    if not isinstance(recipe,dict):
        raise ValueError('host rebaseline recipe core must be a JSON object')
    for key,expected in REBASELINE_RECIPE_CORE.items():
        if (key not in recipe or type(recipe[key]) is not type(expected)
                or recipe[key]!=expected):
            raise ValueError(f'host rebaseline recipe core mismatch: {key}')
    expected_maps=_read(new_out/'historical-generation0/audit-rule.json')['maps']
    if (Path(spec['parent']).resolve()!=Path(marker['parent']).resolve()
            or spec['parent_identity']!=marker['parent_identity']
            or spec['current_sources']!=marker['current_sources'] or spec['maps']!=expected_maps):
        raise ValueError('host rebaseline source/parent/maps identity mismatch')
    audit_list=Path(marker['config']['audit_list'])
    if spec['audit_list_sha256']!=_sha(audit_list):
        raise ValueError('host rebaseline frozen audit-list changed')
    for row in expected_maps:
        if _sha(row['path'])!=row['sha256']:raise ValueError('host rebaseline audit map changed')
    for path,sha in ready['outputs'].items():
        resolved=Path(path).resolve()
        if not resolved.is_relative_to(new_out) or _sha(resolved)!=sha:
            raise ValueError('host rebaseline output content/path mismatch')
    summary_path=new_out/'audit-baseline/summary.json'
    binding_path=new_out/'audit-baseline/baseline-binding.json'
    for path in (summary_path,binding_path):
        if str(path) not in ready['outputs']:
            raise ValueError('host rebaseline must bind current summary and baseline binding')
    binding=_read(binding_path)
    if binding!=dict(adapter=marker['parent_identity'],baseline_adapter=marker['parent'],
                     checker_epoch=s['checker_epoch'](),maps=expected_maps):
        raise ValueError('host rebaseline baseline binding mismatch')
    rows=_read(summary_path)
    if len(rows)!=30 or {str(Path(row['instance_path']).resolve()) for row in rows}!={row['path'] for row in expected_maps}:
        raise ValueError('host rebaseline summary does not contain frozen30')
    passes=[row for row in rows if row.get('passed')]
    measured=dict(maps=30,pass_count=len(passes),mean_weighted_moves=(
        sum(row['weighted_moves'] for row in passes)/len(passes) if passes else None))
    marker.update(status='ready',baseline_pending=False,next_phase='inherit_only',
        baseline_host=ready,baseline_reverification=dict(status='fresh_host_current_epoch_decode',
            original_measurement=marker['baseline_counts']['original'],current_epoch_reverification=measured))
    marker['baseline_counts']['current']=measured
    marker['output_sha256'].update(ready['outputs'])
    marker['output_sha256'][str(ready_path)]=_sha(ready_path)
    marker.pop('prepared_sha256',None);marker['prepared_sha256']=_digest(marker)
    # Recheck source after all file reads. The GPU runner independently checks
    # this same source epoch and exact receipt immediately before each update.
    if s['source_snapshot']()!=marker['current_sources']:
        raise RuntimeError('source changed while attaching host baseline')
    _write(prepared_path,marker)
    return marker


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attach-rebaseline',type=Path,
                        help='quick CPU-only join of prepared training rows and host rebaseline')
    parser.add_argument('--source-out',type=Path)
    parser.add_argument('--new-out',type=Path)
    parser.add_argument('--parent',type=Path)
    parser.add_argument('--estimate-seconds',type=float,
                        help='leader/executive estimate from measured tiny check, before heavy CPU run')
    parser.add_argument('--workers',type=int,choices=(1,2,3,4),default=1)
    parser.add_argument('--defer-baseline',action='store_true',
                        help='D131: host must supply a separately measured current-epoch baseline')
    args=parser.parse_args(argv)
    if args.attach_rebaseline is not None:
        if any(value is not None for value in (args.source_out,args.new_out,args.parent,args.estimate_seconds)):
            parser.error('--attach-rebaseline is independent of preparation arguments')
        result=attach_rebaseline(args.attach_rebaseline)
        print(json.dumps(dict(status=result['status'],baseline=result['baseline_counts']),indent=2))
        return 0
    if any(value is None for value in (args.source_out,args.new_out,args.parent,args.estimate_seconds)):
        parser.error('preparation requires --source-out, --new-out, --parent, and --estimate-seconds')
    if args.estimate_seconds<=0:parser.error('estimate must be positive')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    out=args.new_out.resolve()/'salvage-compute';out.mkdir(parents=True,exist_ok=True)
    with _cpu_reservation(out,args.estimate_seconds,workers=args.workers):
        result=prepare(args.source_out,args.new_out,args.parent,workers=args.workers,
                       defer_baseline=args.defer_baseline)
    print(json.dumps(dict(status=result['status'],counts=result['counts'],
        baseline=result['baseline_counts'],timings=result['timings']),indent=2))
    return 0 if result['status']=='ready' or result.get('training_rows_ready') else 2


if __name__=='__main__':raise SystemExit(main())
