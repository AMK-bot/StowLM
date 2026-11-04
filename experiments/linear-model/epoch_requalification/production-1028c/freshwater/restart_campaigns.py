"""Archive ended freshwater command attempts, then restart frozen controllers.

No frozen producer changes. Invalid catalogue-containing edge populations are
preserved whole and remeasured; valid partial checkpoints keep original guards.
The root must clear ABORT and pin runtime catalogue/schema inputs before launch.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

RUN=Path(__file__).resolve().parent
ROOT=RUN.parents[4]
sys.path.insert(0,str(RUN.parent))
from compute import abort_check
DEST=RUN/'recovery-preserved'
MARKERS=('validation errors for Catalogue','pending_contract','superseded_by','extra_forbidden')


def pin(path):
    path=Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def dead(pid):
    try:os.kill(int(pid),0)
    except ProcessLookupError:return True
    except PermissionError:raise ValueError('cannot prove recorded PID absent: '+str(pid))
    raise ValueError('recorded process still alive: '+str(pid))


def preserve(path,changes):
    target=DEST/path.relative_to(RUN.parent)
    if target.exists():raise ValueError('refuse overwrite of archived attempt: '+str(target))
    before=pin(path)
    target.parent.mkdir(parents=True,exist_ok=True);path.rename(target)
    after=pin(target)
    if before['sha256']!=after['sha256']:raise ValueError('archive hash mismatch')
    changes.append(dict(original=before,archived=after))


def prepare():
    manifest=DEST/'restart-archive.json'
    if manifest.exists():
        for entry in json.loads(manifest.read_text())['archives']:
            current=pin(ROOT/entry['archived']['path'])
            if current!=entry['archived']:raise ValueError('preserved attempt changed')
        return
    # Controllers themselves must have exited before touching their records.
    for path in [RUN/'ordinary-logs/controller.pid',RUN/'edges-controller-all.pid']:
        if path.exists():dead(path.read_text().strip())
    changes=[];affected=set()
    for path in sorted(RUN.glob('edges-v*/*/*.json')):
        if any(marker in path.read_text() for marker in MARKERS):affected.add(path.parent.name)
    # Descendants pin parent observations. Rebuild every version for an affected
    # ship, rather than reusing a successful command with an archived parent.
    for ship in sorted(affected):
        for folder in sorted(RUN.glob('edges-v*/'+ship)):
            for path in sorted(folder.rglob('*')):
                if path.is_file():preserve(path,changes)
    central=RUN.parent/'compute-logs'
    failed_edges=set()
    for path in sorted(central.glob('freshwater-*.execution.json')):
        name=path.name.removesuffix('.execution.json')
        if not ('-edge-' in name or '-diagnostic-' in name):continue
        record=json.loads(path.read_text())
        if 'exit_code' not in record:raise ValueError('unfinished central record: '+str(path))
        rebuild=any('-edge-'+ship+'-' in name for ship in affected)
        if record['exit_code']==0 and not rebuild:continue
        if 'pid' in record:dead(record['pid'])
        if '-edge-' in name:failed_edges.add(name.removeprefix('freshwater-edge-'))
        preserve(path,changes)
        log=central/(name+'.log')
        if log.exists():preserve(log,changes)
    for name in sorted(failed_edges):
        for suffix in ('.execution.json','.log'):
            path=RUN/'edge-logs'/(name+suffix)
            if path.exists():preserve(path,changes)
    DEST.mkdir(exist_ok=True)
    manifest.write_text(json.dumps(dict(controller=pin(__file__),archives=changes,
        catalogue_affected_ships=sorted(affected),whole_invalid_populations_preserved=True,
        valid_partial_populations_resumed_with_original_checkpoint_guards=True),indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('prepare','edges','diagnostics'),required=True)
    args=parser.parse_args()
    if args.phase=='prepare':prepare();return
    abort_check()
    if not (DEST/'restart-archive.json').exists():raise ValueError('archive failed attempts before restart')
    command=[sys.executable,str(RUN/('edges_campaign.py' if args.phase=='edges' else 'diagnostics_campaign.py'))]
    if args.phase=='diagnostics':command+=['--phase','all']
    print(json.dumps(dict(controller=pin(__file__),frozen_command=command,controller_pid=os.getpid())),flush=True)
    # Original frozen controller performs every central numerical admission.
    result=subprocess.run(command,check=False)
    raise SystemExit(result.returncode)

if __name__=='__main__':main()
