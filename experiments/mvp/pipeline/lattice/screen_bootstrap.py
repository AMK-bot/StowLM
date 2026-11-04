"""Detached promotion: wait for honest hero gates, then run disjoint batch queues."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .screen import HERE, ROOT, describe, write, record_path

INITIAL = [HERE/'screening/feeder500-sim-f60-r3.json', HERE/'screening/feeder500-random-f60-r2.json']


def selection_reason(description):
    fill = description['envelope']['numbers']['physical_cell_fill']
    return ('Screened in-envelope development candidate with a full original-instance '
            'checker pass and every crane frame inside limits; synthetic DG cargo, reefers and '
            f"{len(description['pods'])} discharge ports, with {100*(1-fill):.2f}% of section capacity open.")


def promote(path, *, role):
    from stowage.io import load_instance
    from experiments.mvp.pipeline.contract.schema import sha256_json
    from .store import entry_key, save_entry
    row = json.loads(Path(path).read_text())
    if row.get('passed') is True and not row.get('gate'):
        from .screen import sweep
        row = sweep(path)
    if row.get('passed') is not True or row.get('gate', {}).get('passed') is not True:
        return None
    instance = load_instance(ROOT/row['path']).model_dump(mode='json')
    gate = row['gate']
    instance_sha = sha256_json(instance)
    plan_sha = sha256_json(row['plan'])
    if row.get('instance_sha256') != instance_sha or gate.get('instance_sha256') != instance_sha or \
            gate.get('plan_sha256') != plan_sha or row.get('canonical_rules', []) or gate.get('rules') != []:
        return None
    # Historical timeout wall_s=900 was nominal. Never rename it native elapsed.
    native_search_s = row.get('engine_wall_s')
    value = dict(id='base', purpose='baseline', status='checked_plan', mode='stored', label='stored',
        instance=instance, instance_path=row['path'], instance_sha256=instance_sha,
        canonical_rules=[], sentences=[], key=entry_key(instance_sha, []),
        plan=row['plan'], base_plan=None, binding_evidence=row.get('evidence'),
        gate=gate, lifts=gate['lifts'], base_lifts=None, lift_delta=0, what_moved=[],
        profile=row.get('profile',row.get('evidence',{}).get('profile')),
        admission=row.get('admission'), computed_utc=row['computed_utc'],
        engine_version=gate['engine_identity']['engine_version'],
        engine_manifest_sha256=gate['engine_identity']['engine_manifest_sha256'],
        wall_s=row.get('owned_call_total_wall_s'), native_search_wall_s=native_search_s,
        native_search_timing_status='measured' if native_search_s is not None else 'not_separately_measured',
        timeout_allowance_s=row.get('timeout_allowance_s',900 if row.get('budget_status')=='timeout_with_checked_incumbent' else None),
        owned_call_wall_s=row.get('owned_call_wall_s'),
        shutdown_wall_s=row.get('shutdown_wall_s'),
        owned_call_total_wall_s=row.get('owned_call_total_wall_s'),
        timing_scope=row.get('timing_scope','Historical timeout allowance is not measured elapsed time.'),
        sequence_repair_wall_s=row.get('sequence_repair_wall_s'),
        release_gate_wall_s=gate.get('wall_s'),
        source_screen=row.get('source_screen_origin',record_path(path)),
        budget_status=row.get('budget_status','standard_completed'),
        selected_before_profile_completion=row.get('selected_before_profile_completion',False),
        binding_source_sha256=row.get('binding_source_sha256'),
        binding_source_after_sha256=row.get('binding_source_after_sha256'),
        planning_origin_binding_source_sha256=row.get('planning_origin_binding_source_sha256',
            row.get('recovery_provenance',{}).get('original_before_sha256',row.get('binding_source_sha256'))),
        campaign_id=row.get('campaign_id'),
        recovery_provenance=row.get('recovery_provenance'),
        sequence_repair=gate.get('sequence_repair'),
        plan_producer=gate['sequence_repair']['producer'] if gate.get('sequence_repair') else gate['engine_identity']['engine_version'],
        producer_label=gate.get('sequence_repair',{}).get('label','Frontier Engine native output'),
        measurement='measured', confirmation_source='batch')
    saved = save_entry(value)
    write(HERE/'runs'/(value['key']+'.json'),value)
    fixture = HERE/'fixtures'/f'{role}-base.json'
    write(fixture,value)
    description = describe(ROOT/row['path'])
    return dict(**description, screening_result=record_path(path),
        base_plan=record_path(fixture), base_entry=record_path(saved),
        lifts=gate['lifts'], total_lifts_now=gate.get('total_lifts_now'),
        plan_producer=value['plan_producer'], producer_label=value['producer_label'],
        frames_checked=gate['gauges']['n_frames'], computed_utc=value['computed_utc'],
        why=selection_reason(description))


def start_queue(manifest, name):
    path = HERE/'screening'/f'{name}.json'
    write(path,manifest)
    logfile = HERE/'screening'/f'{name}.log'
    command = ['nohup',sys.executable,'-m','experiments.mvp.pipeline.lattice.batch',str(path),'--workers','2']
    with logfile.open('a') as log:
        child = subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    record = dict(pid=child.pid,started_utc=datetime.now(timezone.utc).isoformat(),command=command,log=str(logfile.relative_to(ROOT)))
    write(path.with_suffix('.process.json'),record)
    return record


def bootstrap(paths=INITIAL):
    """No guesses are promoted. Completion of the two screens releases four slots."""
    hero_file = HERE/'hero.json'
    state_file = HERE/'screening/bootstrap-state.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    paths = [Path(p) for p in state.get('screen_paths', paths)]
    hero = json.loads(hero_file.read_text()) if hero_file.exists() else None
    while True:
        completed = []
        for path in paths:
            if path.exists():
                row = json.loads(path.read_text())
                if 'gauge' in row or row.get('passed') is False:
                    completed.append(path)
        successes = [p for p in completed if json.loads(p.read_text()).get('passed')]
        # A checked anytime incumbent may become the frozen hero while the
        # same standard call continues. It still gets every independent gate.
        if hero is None and not successes:
            for path in paths:
                incumbent_path = path.with_suffix('.incumbent.json')
                progress_path = path.with_suffix('.progress.json')
                preview_path = path.with_suffix('.preview.json')
                if incumbent_path.exists() and progress_path.exists() and not preview_path.exists():
                    from .screen import sweep
                    progress = json.loads(progress_path.read_text())
                    event = json.loads(incumbent_path.read_text())
                    preview = dict(progress, plan=event['plan'], evidence=event, passed=True,
                        selected_before_profile_completion=True,
                        wall_s=event['wall_s'], computed_utc=datetime.now(timezone.utc).isoformat(),
                        budget_status='checked_anytime_incumbent_from_running_standard_profile')
                    write(preview_path,preview)
                    preview = sweep(preview_path)
                    if preview['passed']:
                        successes.append(preview_path)
                        break
        if hero is None and successes:
            source = successes[0]
            selected = promote(source,role='hero')
            if selected is None:
                continue
            from stowage.io import load_instance
            from stowage.model import MovePlan
            from .instructions import select_instructions
            raw = json.loads(source.read_text())
            choices = select_instructions(load_instance(ROOT/raw['path']),MovePlan.model_validate(raw['plan']))
            manifest = dict(instance_path=raw['path'],base_plan=raw['plan'],**choices)
            write(HERE/'manifest.json',manifest)
            hero = dict(schema_version='stowlm.lattice-hero/1',synthetic=True,hero=selected,backup=None,
                selection_status='hero_checked_backup_pending',
                assumptions=['Standard is the provisional runner profile, not a calibrated 900-second end-to-end guarantee.',
                             'Feeder800 is preferred, but fresh dev maps were feeder500 only at launch; cheapest eligible maps were screened first.'],
                considered_candidates=[json.loads(line) for line in (HERE/'screening/candidates.jsonl').read_text().splitlines()])
            write(hero_file,hero)
            first = dict(manifest,instructions=manifest['instructions'][:13],off_lattice=[])
            state['queue_a'] = start_queue(first,'batch-a')
            state['hero_source'] = str(source.relative_to(ROOT))
            write(state_file,state)
            print(json.dumps(dict(event='hero_promoted',hero=selected)),flush=True)
        if hero is not None:
            others = [p for p in successes if json.loads(p.read_text())['instance_sha256'] != hero['hero']['instance_sha256']]
            if hero.get('backup') is None and others:
                hero['backup'] = promote(others[0],role='backup')
                hero['selection_status'] = 'hero_and_backup_checked'
                write(hero_file,hero)
        if len(completed) == len(paths):
            if hero is None or hero.get('backup') is None:
                tried = {json.loads(p.read_text())['path'] for p in completed}
                candidates = [json.loads(line) for line in (HERE/'screening/candidates.jsonl').read_text().splitlines()]
                remaining = sorted([c for c in candidates if c['path'] not in tried and c['envelope']['in_envelope']], key=lambda c:c['boxes'])
                if remaining:
                    for candidate in remaining[:2]:
                        target = HERE/'screening'/(candidate['instance_sha256'][:16]+'.json')
                        logpath = target.with_suffix('.log')
                        with logpath.open('a') as log:
                            command = ['nohup',sys.executable,'-m','experiments.mvp.pipeline.lattice.screen','screen',candidate['path'],str(target)]
                            child = subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                        write(target.with_suffix('.launcher.json'),dict(pid=child.pid,command=command))
                        paths.append(target)
                    state['screen_paths']=[str(p) for p in paths]
                    write(state_file,state)
                    continue
                if hero is None:
                    state.update(status='no_hero_from_screened_candidates',completed=[str(p.relative_to(ROOT)) for p in completed])
                    write(state_file,state)
                    print(json.dumps(state),flush=True)
                    return
            if 'queue_b' not in state:
                manifest=json.loads((HERE/'manifest.json').read_text())
                second=dict(manifest,instructions=manifest['instructions'][13:])
                state['queue_b']=start_queue(second,'batch-b')
                state['status']='two_disjoint_batches_running_four_workers'
                write(state_file,state)
                print(json.dumps(state),flush=True)
            return
        time.sleep(5)


def main():
    write(HERE/'screening/bootstrap-process.json',dict(pid=os.getpid(),started_utc=datetime.now(timezone.utc).isoformat()))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths',type=Path,nargs='*')
    args=parser.parse_args()
    bootstrap(args.paths or INITIAL)


if __name__=='__main__':
    main()
