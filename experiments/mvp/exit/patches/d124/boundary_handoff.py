"""Prepare a D124 restart after gen0 releases its process and GPU.

This script does not apply patches, write cancellation files, launch a process,
or manufacture a mask-speed boundary. Commit requires the activated, reviewed
D124 code_epoch authorization hook and its exact old/new hash manifest.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack,contextmanager
from datetime import datetime,timezone
import fcntl
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                     allow_nan=False).encode()).hexdigest()


@contextmanager
def released_lock(path):
    path=Path(path)
    if not path.exists():
        raise RuntimeError(f'cannot prove released ownership: missing lock {path}')
    with path.open('r+') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f'old process still owns {path}') from error
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def prepare(out,*,current_pins,approved_changes,gens=8):
    """Reconstruct committed next-generation state; never resume partial gen0."""
    out=Path(out).resolve()
    if (out/'terminal.json').exists():raise RuntimeError('terminal fallback forbids handoff')
    if (out/'gen-001'/'instances.json').exists():raise RuntimeError('generation1 already started')
    summary=read(out/'gen-000'/'summary.json')
    manifest=read(out/'gen-000'/'instances.json')
    sizing=read(out/'sizing-after-gen0.json')
    if summary.get('generation')!=0:raise ValueError('need a committed generation0 summary')
    population=summary.get('next_population')
    if not isinstance(population,list) or not population:
        raise ValueError('completed generation has no next population')
    for row in population:
        if not isinstance(row,dict) or not isinstance(row.get('adapter'),str):
            raise ValueError('malformed next population')
        if not (Path(row['adapter'])/'adapters.safetensors').is_file():
            raise ValueError('next population adapter is not published')
    loaded=manifest['source_pins']
    changed=sorted(path for path in loaded.keys()|current_pins.keys()
                   if loaded.get(path)!=current_pins.get(path))
    if not changed or set(changed)!=set(approved_changes):
        raise ValueError('source drift differs from the explicitly approved D124 files')
    if any(path not in current_pins for path in changed):
        raise ValueError('D124 handoff does not authorize removed source files')
    maps,k=sizing['maps'],sizing['k']
    if type(maps) is not int or maps<3 or k not in (6,7,8):
        raise ValueError('invalid measured next-generation sizing')
    if type(gens) is not int or gens<2:raise ValueError('handoff needs another generation')
    args=dict(manifest['config'])
    args.update(parent=manifest['population'][0]['adapter'],out=str(out),gens=gens,
                maps=None,tiny=maps//3,small=maps-maps//3,k=k)
    payload=dict(schema='exit-d124-handoff-draft-v1',phase='between_generations',
        next_generation=1,population=population,args=args,loaded_pins=loaded,
        current_pins=current_pins,approved_changes=changed,authorization='D124',
        restore_ready=False,requires='reviewed D124 code_epoch authorization + fresh current-code smoke',
        evidence=dict(summary_sha256=digest(summary),manifest_sha256=digest(manifest),
                      sizing_sha256=digest(sizing)),utc=datetime.now(timezone.utc).isoformat())
    return {**payload,'state_sha256':digest(payload)}


def commit(out,boundary,*,root,current_pins,approved_changes,gens=8,epoch=None):
    """Commit a real restore-compatible boundary under released owner locks."""
    if epoch is None:
        from llm.exit import code_epoch as epoch
    out,boundary,root=Path(out),Path(boundary),Path(root)
    with ExitStack() as locks:
        locks.enter_context(released_lock(out/'.runner.lock'))
        locks.enter_context(released_lock(root/'experiments/mvp/exit/.gpu.lock'))
        draft=prepare(out,current_pins=current_pins,approved_changes=approved_changes,gens=gens)
        plan=epoch.boundary_plan(draft['loaded_pins'],draft['current_pins'])
        if plan['kind']!='checker_watchdog':
            raise RuntimeError('reviewed D124 hash authorization is not active; no boundary written')
        if boundary.exists():raise RuntimeError('refusing to overwrite an existing handoff boundary')
        record=epoch.save_boundary(boundary,number=1,population=draft['population'],args=draft['args'],
            loaded_pins=draft['loaded_pins'],current_pins=draft['current_pins'])
        epoch.restore_boundary(boundary,current_pins)
        return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--approved-change',action='append',required=True)
    parser.add_argument('--gens',type=int,default=8)
    parser.add_argument('--boundary',required=True,type=Path)
    args=parser.parse_args()
    from llm.exit import code_epoch
    # The CLI holds both released locks while reading and committing its state.
    # This is intentionally run by the host owner only after old process exit.
    commit(args.out,args.boundary,root=args.root,current_pins=code_epoch.source_snapshot(args.root),
           approved_changes=args.approved_change,gens=args.gens,epoch=code_epoch)
    return 0


if __name__=='__main__':raise SystemExit(main())
