"""Owned legacy-supervisor handoff; no signals or process discovery required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import run
HERE,ROOT=run.HERE,run.ROOT


def legacy_active(stream):
    handoff=run.read(HERE/'HANDOFF.json')[stream]
    launch=handoff['legacy_status']['launch_id']
    active=[]
    for path in (HERE/stream/'owned').glob('*.json'):
        owned=run.read(path)
        if owned.get('launch_id')!=launch:continue
        target=HERE/stream/'rows'/f"{owned['key']}.json"
        row=run.read(target) if target.exists() else {}
        if row.get('pid')!=owned['pid'] or 'finished_at' not in row:active.append(owned)
    return active


def launch_rule():
    command=['uv','run','--offline','--no-sync','python',str(HERE/'run.py'),'supervise','rule']
    with (HERE/'rule/controller.log').open('a') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    run.write(HERE/'rule/launches'/f'upgrade-{uuid.uuid4().hex}.json',dict(pid=child.pid,command=command,at=time.time()))
    return child


def install():
    old=run.read(HERE/'manifest.json')
    from llm.exit import outcome_epoch
    expected=outcome_epoch.process_snapshot()
    if outcome_epoch.epoch_id(expected)!=old['outcome_epoch']:raise RuntimeError('physics epoch changed during handoff')
    # Verify every old passing candidate against its original manifest before adoption.
    from export import admitted_rows
    admitted,counts,_=admitted_rows(old,'rule',HERE)
    passing={entry['key'] for entry,row in admitted}
    history=HERE/'history'/old['manifest_id'];history.mkdir(parents=True,exist_ok=True)
    run.write(history/'manifest.json',old)
    (history/'run.py').write_bytes((HERE/'run.py').read_bytes())
    snapshots={}
    for path in (HERE/'rule/rows').glob('*.json'):
        row=run.read(path);snapshots[row['key']]=row
        run.write(HERE/'rule/attempts'/row['key']/f"legacy-{row['pid']}.json",row)
        run.write(history/'rule-rows'/path.name,row)
    next_source=(HERE/'run.next.py').read_bytes()
    temporary=HERE/'run.py.upgrade.tmp';temporary.write_bytes(next_source);temporary.replace(HERE/'run.py')
    new=json.loads(json.dumps(old));new.pop('manifest_id')
    new.update(parent_manifest_id=old['manifest_id'],adoption_policy='Explicit same-outcome-epoch adoption of guarded passing candidates with unchanged input/plan/baseline bytes; original producing manifest retained in history; budget/control changes only.')
    new['config'].update(rule_plan_s=300,rule_finisher_s=900,rule_workers=3,frontier_paused=True,checker_timeout_s=900)
    new['sources']=run.sources();new['manifest_id']=run.digest(new)
    # No old verdict is relabelled as produced by the new runtime: preserve its producer proof.
    for key,row in snapshots.items():
        path=HERE/'rule/rows'/f'{key}.json'
        timeout=any(word in json.dumps(row.get('planning',{})).lower() for word in ('timeout','timed out','time limit','deadline','budget_exhausted'))
        if row.get('status')=='error' or (not row.get('passed') and timeout):
            path.unlink();continue
        if row.get('passed') and key not in passing:raise RuntimeError('unadmitted prior winner')
        entry=next(e for e in new['entries'] if e['key']==key)
        row['producer_manifest_id']=old['manifest_id']
        row['adopted_from']=dict(manifest=str(history/'manifest.json'),row=str(history/'rule-rows'/path.name),row_sha256=run.sha(history/'rule-rows'/path.name))
        row.update(manifest_id=new['manifest_id'],binding=run.binding(entry,new,'rule'))
        if row.get('passed') and timeout:row['needs_rule_timeout_retry']=True
        run.write(path,row)
    run.write(HERE/'manifest.json',new)
    run.write(HERE/'control.json',dict(rule=dict(workers=3,paused=False,plan_s=300,finisher_s=900),frontier=dict(workers=0,paused=True),reason='Leader update; frontier resumes only on explicit instruction'))
    for item in run.read(HERE/'HANDOFF.json').values():
        fence=Path(item['fence'])
        if fence.exists():fence.rmdir()
    child=launch_rule()
    run.write(HERE/'UPGRADE.DONE',dict(pid=os.getpid(),rule_launcher_pid=child.pid,old_manifest=old['manifest_id'],new_manifest=new['manifest_id'],preserved_passing=len(passing),at=time.time()))
    return new


def main():
    run.write(HERE/'upgrade-owner.json',dict(pid=os.getpid(),at=time.time()))
    while legacy_active('rule'):
        run.write(HERE/'upgrade-status.json',dict(phase='draining_rule',active=legacy_active('rule'),frontier_legacy_active=legacy_active('frontier'),at=time.time()))
        time.sleep(3)
    new=install()
    while legacy_active('frontier'):
        run.write(HERE/'upgrade-status.json',dict(phase='rule_upgraded_frontier_draining',frontier_legacy_active=legacy_active('frontier'),at=time.time()))
        time.sleep(3)
    pause=run.read(HERE/'frontier/PAUSED.json');pause.update(phase='paused',legacy_workers_drained=True,at=time.time());run.write(HERE/'frontier/PAUSED.json',pause)
    run.write(HERE/'frontier/status.json',dict(paused=True,workers=0,active=[],manifest_id=new['manifest_id'],updated_at=time.time(),reason='Leader pause; no automatic resume'))
    run.write(HERE/'upgrade-status.json',dict(phase='complete',frontier_paused=True,at=time.time()))


if __name__=='__main__':main()
