"""Freeze the ten compatible rows, then detach the hard-wall supervisor."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .supervisor import HERE, ROOT
from ..measurement_lifecycle import durable_json


def main():
    from ..review3.activation import install
    install()
    from ..review3.measurement import current_rows
    from ..live_campaign import cohort
    from ..adapter import verify_manifest
    manifest = cohort()
    selected = {row['path'] for row in manifest['selected_medium']}
    current, _ = current_rows(HERE/'measurements/rows')
    rows = [row for row in current if row['map'] in selected and row['cell'] == 'medium'
            and row['profile'] == 'live-v2']
    if len(rows) != 10:
        raise ValueError(f'Expected the ten checkpointed valid rows; found {len(rows)}')
    identity = verify_manifest()
    if not identity['passed']:
        raise ValueError('Pinned engine/native manifest differs')
    output = HERE/'measurements'/('deadline-live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir()
    (output/'jobs').mkdir()
    sources = []
    for row in rows:
        source = HERE/'measurements/rows'/(row['key']+'.json')
        sources.append(dict(path=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
        comparison = HERE/'measurements/live-fixed-20261102T0220/comparisons'/(row['key']+'.json')
        if comparison.exists():
            durable_json(output/'comparisons'/comparison.name, json.loads(comparison.read_text()))
    durable_json(output/'reused.json', dict(rows=rows, sources=sources, medium_count=10,
        original_bytes_unchanged=True, frozen_at=time.time(), legacy_rows_not_retroactively_wall_bounded=True))
    manifest.update(workers=3, hard_wall_s=900, engine_identity=identity, supervisor_pid=None,
        created_at=time.time(), remaining_medium_rows=30, preserved_medium_rows=10,
        budget_semantics='900-second independent supervisor wall; engine extra90/finish30 are work controls',
        previous_supervisor_stopped_by_user=98225)
    durable_json(output/'campaign.json', manifest)
    command = ['nohup', sys.executable, '-m', 'experiments.mvp.pipeline.binding.deadline.supervisor',
               '--out', str(output)]
    with (output/'nohup.log').open('ab', buffering=0) as log:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    durable_json(output/'launcher.json', dict(pid=process.pid, command=command,
        detached_session=True, started_at=time.time(), parent_helper_pid=os.getpid()))
    (output/'launcher.pid').write_text(str(process.pid)+'\n')
    durable_json(HERE/'measurements/deadline-current.json', dict(pid=process.pid, output=str(output)))
    print(json.dumps(dict(pid=process.pid, preserved=10, remaining_medium=30,
                         eta_medium='about2.5hours plus admission waits', output=str(output))), flush=True)


if __name__ == '__main__':
    main()
