"""Drain the recorded producer, preserve proofs, install D209, resume three lanes.

The old producer predates cooperative shutdown. Once its own durable status has
no children/exporter, a deliberate invalid worker control exits its dispatch
loop. No signal is sent, and no live map is interrupted. Later supervisors have
an explicit drain/shutdown control.
"""
import fcntl
import json
import os
import subprocess
import sys
import time

import run

HERE = run.HERE


def install():
    old = run.read(HERE/'manifest.json')
    from llm.exit import outcome_epoch
    if outcome_epoch.epoch_id(outcome_epoch.process_snapshot()) != old['outcome_epoch']:
        raise RuntimeError('physical outcome epoch changed')
    from export import admitted_rows
    admitted, _, _ = admitted_rows(old, 'rule', HERE)
    passing = {entry['key'] for entry, row in admitted}
    history = HERE/'history'/old['manifest_id']
    run.write(history/'manifest.json', old)
    for name in ('run.py', 'export.py'):
        (history/name).write_bytes((HERE/name).read_bytes())
    snapshots = []
    for path in (HERE/'rule/rows').glob('*.json'):
        row = run.read(path)
        if row.get('passed') and row['key'] not in passing:
            raise RuntimeError('unadmitted prior winner')
        run.write(history/'rule-rows'/path.name, row)
        run.write(HERE/'rule/attempts'/row['key']/f"d209-{row['pid']}.json", row)
        snapshots.append((path, row))
    for name in ('run', 'export'):
        target = HERE/f'{name}.py'
        temporary = target.with_suffix('.d209.tmp')
        temporary.write_bytes((HERE/f'{name}.d209.py').read_bytes())
        temporary.replace(target)
    new = json.loads(json.dumps(old))
    new.pop('manifest_id')
    new.update(parent_manifest_id=old['manifest_id'],
               adoption_policy='D209 policy-only handoff: unchanged physics/plans/checker/mask; original producer proofs retained in history; all failures retained.',
               snapshot_policy='19:45 initial publication; no stopping cutoff; every 200 further admitted labels, immutable numbered snapshots')
    new['config'].update(rule_stop_at_deadline=False, rule_entry_limit=None,
                         rule_workers=3, frontier_paused=True)
    new['sources'] = run.sources()
    new['manifest_id'] = run.digest(new)
    entries = {entry['key']: entry for entry in new['entries']}
    for path, row in snapshots:
        row.setdefault('producer_manifest_id', old['manifest_id'])
        row['adopted_from'] = dict(manifest=str(history/'manifest.json'),
            row=str(history/'rule-rows'/path.name), row_sha256=run.sha(history/'rule-rows'/path.name))
        row.update(manifest_id=new['manifest_id'], binding=run.binding(entries[row['key']], new, 'rule'))
        run.write(path, row)
    run.write(HERE/'manifest.json', new)
    run.write(HERE/'control.json', dict(rule=dict(workers=3, paused=False, plan_s=300, finisher_s=900),
        frontier=dict(workers=0, paused=True), reason='D209: three rule workers overnight; frontier paused until explicit instruction'))
    command = ['uv', 'run', '--offline', '--no-sync', 'python', str(HERE/'run.py'), 'supervise', 'rule']
    with (HERE/'rule/controller.log').open('a') as log:
        child = subprocess.Popen(command, cwd=run.ROOT, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    run.write(HERE/'rule/launches'/f'd209-{child.pid}.json', dict(pid=child.pid, command=command, at=time.time()))
    run.write(HERE/'D209-HANDOFF.json', dict(phase='complete', pid=os.getpid(), launcher_pid=child.pid,
        preserved_passing=len(passing), preserved_rows=len(snapshots), old_manifest=old['manifest_id'],
        new_manifest=new['manifest_id'], workers=3, frontier_paused=True, at=time.time()))


def main():
    run.write(HERE/'d209-upgrade-owner.json', dict(pid=os.getpid(), at=time.time()))
    while True:
        status = run.read(HERE/'rule/status.json')
        if not status['active'] and not status.get('export_pid') and status['workers'] == 0:
            break
        time.sleep(3)
    control = run.read(HERE/'control.json')
    control['rule']['workers'] = 'D209-drained-supervisor-exit'
    run.write(HERE/'control.json', control)
    # The producer releases this advisory lock when its dispatch loop exits.
    with (HERE/'rule/controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fcntl.flock(lock, fcntl.LOCK_UN)
    install()


if __name__ == '__main__':
    main()
