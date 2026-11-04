"""Current-binding recovery, two screens beside legacy gauges, then one batch."""
from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .screen import HERE, ROOT, describe, write
from .screen_bootstrap import INITIAL, promote, selection_reason

DEFAULT_MAPS = [
    'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-low/seed-192200002.json',
    'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_800/f60-low/seed-192200100.json',
]
BACKUP_PRIORITY = [
    'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f70-low/seed-192200001.json',
    'experiments/mvp/pipeline/maps/dev/feeder_500-simulated_yard-f60-r0.json',
    'experiments/mvp/pipeline/maps/dev/feeder_500-simulated_yard-f60-r1.json',
]


def active_campaign(control):
    try:
        return json.loads(Path(control).read_text()).get('active_campaign')
    except (OSError,ValueError):
        return None


def new_searches_allowed(control):
    try:
        return json.loads(Path(control).read_text()).get('allow_new_searches',True) is True
    except (OSError,ValueError):
        return False


def legacy_quiescent():
    # These genuine gauge fields are written only after the old parent finishes.
    if not all(path.exists() and 'gauge' in json.loads(path.read_text()) for path in INITIAL):
        return False
    state=HERE/'screening/bootstrap-state.json'
    if not state.exists():
        return False
    row=json.loads(state.read_text())
    return ('queue_b' in row or row.get('status') in
            {'no_hero_from_first_two','no_hero_from_screened_candidates',
             'two_disjoint_batches_running_four_workers'})


def launch_screen(path,target,campaign,control):
    target.parent.mkdir(parents=True,exist_ok=True)
    command=['nohup',sys.executable,'-m','experiments.mvp.pipeline.lattice.screen',
             'screen',str(path),str(target),'--campaign',campaign,
             '--campaign-control',str(control)]
    logfile=target.with_suffix('.log')
    with logfile.open('a') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    return dict(instance_path=str(path),target=str(target),pid=child.pid,command=command,
                log=str(logfile),started_utc=datetime.now(timezone.utc).isoformat())


def prepare_recovery(source,target,campaign):
    """Reclassify only the false source alarm; the native plan gets fresh gates."""
    from .store import _screen_source_guard,binding_fingerprint
    source,target=Path(source).resolve(),Path(target).resolve()
    raw_bytes=source.read_bytes()
    raw=json.loads(raw_bytes)
    if not raw.get('plan') or not raw.get('evidence',{}).get('passed'):
        raise ValueError('Recovery requires a recorded native checked incumbent')
    origin=str(source.relative_to(ROOT))
    probe=dict(source_screen=origin)
    _screen_source_guard(probe)
    current=binding_fingerprint()
    if raw.get('binding_source_sha256')!=current or probe['binding_source_sha256']!=current:
        raise ValueError('Recovery is unavailable: an actual binding runtime module changed')
    row=deepcopy(raw)
    row.update(passed=True,status='awaiting_current_full_gate',campaign_id=campaign,
        binding_source_after_sha256=current,source_screen_origin=origin,
        reason='The measurement-only source alarm was reclassified; a fresh full gate is still required.',
        recovery_provenance=dict(original_path=origin,
            original_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            original_before_sha256=raw.get('binding_source_sha256'),
            original_after_sha256=raw.get('binding_source_after_sha256'),
            original_status=raw.get('status'),original_reason=raw.get('reason'),
            effective_runtime_sha256=current,unchanged_runtime_modules=probe['binding_source_files'],
            excluded_nonruntime_addition='live_campaign.py',
            reason='The broad after hash included a new measurement launcher. All PID-bound planning runtime files are byte-identical; the raw source artifact is preserved. Full checker and every gauge frame must pass again.',
            prepared_utc=datetime.now(timezone.utc).isoformat()))
    row.pop('gauge',None)
    row.pop('gate',None)
    write(target,row)
    # Exact copies preserve the original worker PID/start binding for review.
    for suffix in ('.progress.json','.progress.source.json'):
        old=source.with_suffix(suffix)
        target.with_suffix(suffix).write_bytes(old.read_bytes())
    if source.read_bytes()!=raw_bytes:
        raise ValueError('Original recovery evidence changed while preparing its derivative')
    return row


def launch_recovery(source,target,campaign):
    row=prepare_recovery(source,target,campaign)
    command=['nohup',sys.executable,'-m','experiments.mvp.pipeline.lattice.screen','sweep',str(target)]
    logfile=target.with_suffix('.log')
    with logfile.open('a') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    return dict(instance_path=row['path'],target=str(target),pid=child.pid,kind='recovered_gauge',
                original=str(source),command=command,log=str(logfile),
                started_utc=datetime.now(timezone.utc).isoformat())


def row_complete(row):
    return row.get('passed') is False or isinstance(row.get('gate'),dict)


def current_pass(row):
    from .store import _binding_source_matches
    if row.get('passed') is not True or row.get('gate',{}).get('passed') is not True:
        return False
    probe=dict(row,canonical_rules=row.get('canonical_rules',[]))
    probe['planning_origin_binding_source_sha256']=row.get('planning_origin_binding_source_sha256',
        row.get('recovery_provenance',{}).get('original_before_sha256',row.get('binding_source_sha256')))
    if row.get('source_screen_origin'):
        probe['source_screen']=row['source_screen_origin']
    return _binding_source_matches(probe)


def candidates():
    path=HERE/'screening/inventory.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    # These earlier standard plans failed native intermediate-frame physics.
    # They establish cheapest-first screening, but rerunning them is no backup.
    rejected = set()
    for result in INITIAL:
        if result.exists():
            row = json.loads(result.read_text())
            if row.get('gauge',{}).get('bad_steps') or row.get('early_gauge_rejection'):
                rejected.add(row['path'])
    eligible = [r['path'] for r in sorted(rows,key=lambda r:r['boxes'])
            if r['envelope']['in_envelope'] and r['dg'] and r['reefers']>0
            and len(r['pods']) in (2,3) and r['path'] not in DEFAULT_MAPS
            and r['path'] not in rejected]
    return list(dict.fromkeys([p for p in BACKUP_PRIORITY if (ROOT/p).exists()]+eligible))


def open_screen_slots(passing, pending):
    """Two owned slots, also bounded by how many checked candidates are needed."""
    return max(0, 2-len(passing)-len(pending))


def merge_extra_candidates(state, directory):
    """The launcher records exact owned repair workers without replacing raw rows."""
    additions=directory/'extra-candidates.json'
    if not additions.exists():
        return
    known={str(Path(job['target']).resolve()) for job in state['jobs']}
    for job in json.loads(additions.read_text()):
        target=Path(job['target']).resolve()
        if not target.is_relative_to(directory.resolve()):
            raise ValueError('Extra candidate must be a distinct artifact in this campaign')
        if str(target) in known:
            continue
        if job.get('kind') not in {'sequence_repair','recovered_gauge'} or not isinstance(job.get('pid'),int):
            raise ValueError('Extra candidate must record its exact owned repair or gauge worker')
        state['jobs'].append(dict(job,target=str(target)))
        known.add(str(target))


def reserved_targets(reserved_screens,pending):
    return list(dict.fromkeys(str(Path(p).resolve()) for p in
        list(reserved_screens or [])+[job['target'] for job in pending]))


def distinct_passing(jobs):
    """Two checked plans for the same voyage cannot be hero plus backup."""
    selected={}
    for job in jobs:
        row=json.loads(Path(job['target']).read_text())
        instance=row['instance_sha256']
        score=(row['gate'].get('lifts',float('inf')),
               bool(row['gate'].get('sequence_repair')),str(job['target']))
        if instance not in selected or score<selected[instance][0]:
            selected[instance]=(score,job)
    return [value[1] for value in selected.values()]


def archive_attempt(target):
    archive=target.parent/'archive'/str(time.time_ns())
    archive.mkdir(parents=True,exist_ok=True)
    for path in list(target.parent.glob(target.stem+'*')):
        if path.is_file():
            path.rename(archive/path.name)


def preferred(jobs):
    return sorted(jobs,key=lambda job:(
        json.loads(Path(job['target']).read_text())['envelope']['numbers']['ship']!='feeder_800',
        json.loads(Path(job['target']).read_text())['boxes']))


def backup_block_target(directory):
    return directory/'084ffe943b997966.block.json'


def ensure_backup_worker(state,pending,passing,directory):
    """The block repair occupies its reserved slot before rule work can use it."""
    if state.get('backup_block') or len(passing)>=2 or len(pending)>=4:
        return
    source=directory/'084ffe943b997966.json'
    frames=source.with_suffix('.frames.json')
    if not source.exists() or not frames.exists():
        return
    target=backup_block_target(directory)
    for suffix in ('.progress.json','.progress.source.json'):
        target.with_suffix(suffix).write_bytes(source.with_suffix(suffix).read_bytes())
    command=['nohup',sys.executable,'-m','experiments.mvp.pipeline.lattice.sequence_blocks',
             str(source),str(frames),str(target)]
    logpath=target.with_suffix('.log')
    with logpath.open('a') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    job=dict(target=str(target),pid=child.pid,kind='sequence_repair',
        instance_path=json.loads(source.read_text())['path'],command=command,
        source_row=str(source),source_frames=str(frames),log=str(logpath),
        started_utc=datetime.now(timezone.utc).isoformat())
    state['backup_block']=job
    state['jobs'].append(job)
    pending.append(job)
    extras=directory/'extra-candidates.json'
    previous=json.loads(extras.read_text()) if extras.exists() else []
    write(extras,previous+[job])


def publish_primary(state,passing,pending,reserved_screens,campaign,control,directory):
    """Freeze the first fully checked voyage; backup computation can continue."""
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from .instructions import select_instructions
    from .screen import story_candidates
    selected=preferred(passing)[0]
    hero=promote(Path(selected['target']),role='hero')
    if hero is None:
        raise RuntimeError('Primary candidate failed promotion; no batch was started')
    row=json.loads(Path(selected['target']).read_text())
    instance=load_instance(ROOT/row['path'])
    plan=MovePlan.model_validate(row['plan'])
    document=dict(schema_version='stowlm.lattice-hero/1',synthetic=True,
        campaign_id=campaign,selection_status='hero_checked_backup_pending',
        hero=hero,backup=None,story_candidates=story_candidates(instance,plan),
        assumptions=['Current binding runtime fingerprint is required for publication.',
                     'Standard is the provisional runner profile; actual wall time is recorded.',
                     'Feeder800 was preferred and screened; its earlier plans failed intermediate gauges.',
                     'The first fully gated primary is frozen so subsequent stored computations remain useful.',
                     'Backup must have a different instance hash and pass its own complete gate.'],
        computed_utc=datetime.now(timezone.utc).isoformat())
    write(HERE/'hero.json',document)
    choices=select_instructions(instance,plan)
    reserved=list(reserved_screens or [])
    if len(passing)<2:
        reserved.append(backup_block_target(directory))
    manifest=dict(campaign_id=campaign,instance_path=row['path'],base_plan=row['plan'],
                  reserved_screen_paths=reserved_targets(reserved,pending),**choices)
    write(HERE/'manifest.json',manifest)
    command=['nohup',sys.executable,'-m','experiments.mvp.pipeline.lattice.batch',
             str(HERE/'manifest.json'),'--workers','4','--campaign-control',str(control)]
    logpath=directory/'batch.log'
    with logpath.open('a') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    state.update(status='current_batch_running_backup_pending',hero=hero,backup=None,
                 batch_pid=child.pid,batch_command=command,batch_log=str(logpath))
    return row['instance_sha256']


def publish_backup(state,passing):
    primary=state['hero']['instance_sha256']
    others=[job for job in passing if json.loads(Path(job['target']).read_text())['instance_sha256']!=primary]
    if not others:
        return False
    backup=promote(Path(preferred(others)[0]['target']),role='backup')
    if backup is None:
        return False
    document=json.loads((HERE/'hero.json').read_text())
    if document['hero']['instance_sha256']!=primary:
        raise RuntimeError('Primary voyage changed while its batch was running')
    document.update(backup=backup,selection_status='hero_and_backup_checked')
    write(HERE/'hero.json',document)
    state.update(backup=backup,status='current_batch_running_four_workers')
    placeholder=backup_block_target(Path(state['batch_log']).parent)
    if not state.get('backup_block') and not placeholder.exists():
        write(placeholder,dict(passed=False,status='cancelled_unused_backup_reservation',plan=None,
            reason='A distinct fully gated backup is available; no block worker was started.'))
    return True


def run(campaign='current-v2',control=HERE/'campaign-control.json',recovery_sources=None,reserved_screens=None):
    directory=HERE/'screening'/campaign
    directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else dict(campaign_id=campaign,jobs=[])
    state.update(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat())
    if active_campaign(control)!=campaign:
        state['status']='superseded_no_cpu'
        write(state_path,state)
        return
    if recovery_sources and not state['jobs']:
        for source in recovery_sources:
            row=json.loads(Path(source).read_text())
            target=directory/(row['instance_sha256'][:16]+'.json')
            state['jobs'].append(launch_recovery(source,target,campaign))
        state['status']='recovered_incumbents_in_fresh_gates'
        write(state_path,state)
    queue=[p for p in DEFAULT_MAPS+candidates() if p not in {j['instance_path'] for j in state['jobs']}]
    while True:
        if active_campaign(control)!=campaign:
            state['status']='superseded_owned_jobs_may_finish'
            write(state_path,state)
            return
        pending=[]
        passing=[]
        retry=[]
        merge_extra_candidates(state,directory)
        from .store import binding_fingerprint
        current_source=binding_fingerprint()
        for job in state['jobs']:
            target=Path(job['target'])
            row=json.loads(target.read_text()) if target.exists() else None
            if row is None or not row_complete(row):
                pending.append(job)
                continue
            job['status']=row.get('status','checked_plan' if row.get('passed') else 'rejected')
            if current_pass(row):
                passing.append(job)
            elif (row.get('status')=='rerun_required' or row.get('passed') is True
                  or row.get('binding_source_sha256')!=current_source
                  or row.get('binding_source_after_sha256')!=current_source):
                retry.append(job)
        passing=distinct_passing(passing)
        ensure_backup_worker(state,pending,passing,directory)
        if state.get('hero'):
            if publish_backup(state,passing):
                write(state_path,state)
                return
            state['status']='current_batch_running_backup_pending'
            write(state_path,state)
            time.sleep(5)
            continue
        if passing and legacy_quiescent():
            publish_primary(state,passing,pending,reserved_screens,campaign,control,directory)
            completed=publish_backup(state,passing)
            write(state_path,state)
            if completed:
                return
            time.sleep(5)
            continue
        if len(passing)<2:
            for job in retry:
                archive_attempt(Path(job['target']))
                state['jobs'].remove(job)
                queue.insert(0,job['instance_path'])
            while queue and new_searches_allowed(control) and open_screen_slots(passing,pending):
                path=queue.pop(0)
                info=describe(ROOT/path)
                target=directory/(info['instance_sha256'][:16]+'.json')
                job=launch_screen(path,target,campaign,control)
                state['jobs'].append(job)
                pending.append(job)
            state['status']='current_binding_screens_running'
            write(state_path,state)
        if passing:
            state['status']='waiting_for_legacy_gauges_and_bootstrap_quiescence'
            write(state_path,state)
        elif not pending and not queue and not retry:
            state.update(status='no_two_checked_heroes_from_development_inventory',
                         passing=len(passing))
            write(state_path,state)
            return
        time.sleep(5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',default='current-v2')
    parser.add_argument('--campaign-control',type=Path,default=HERE/'campaign-control.json')
    parser.add_argument('--recover-from',type=Path,action='append')
    parser.add_argument('--reserve-screen',type=Path,action='append')
    args=parser.parse_args()
    run(args.campaign,args.campaign_control,args.recover_from,args.reserve_screen)


if __name__=='__main__':
    main()
