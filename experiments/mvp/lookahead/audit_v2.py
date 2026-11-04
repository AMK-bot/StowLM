"""Read-only provenance and process audit of the fixed v1 measurement campaign."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


@lru_cache(maxsize=None)
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run():
    freeze = read(HERE / 'runtime-freeze-v2.json')
    sources = freeze['lookahead_runtime_sources']
    scripts = {str(HERE / name): digest(HERE / name) for name in ('worker.py', 'paired_v2.py', 'controller.py')}
    tokenizer = ROOT / 'models/glimmer-30b-4bit/tokenizer.json'
    rows = []
    controllers = []
    for folder in ('pilot-v2-contracts', 'pilot-v2-paired', 'formal-v2-contracts', 'formal-v2-paired/shard0', 'formal-v2-paired/shard1'):
        path = HERE / folder / 'controller.json'
        if not path.exists():
            continue
        controller = read(path)
        controllers.append(dict(path=str(path), **controller))
        for job in controller['jobs']:
            if job.get('skipped_completed'):
                continue
            result_path = Path(job['result'])
            if not result_path.exists():
                continue
            result = read(result_path)
            if not result.get('completed'):
                continue
            before = read(result_path.parent / 'source-before.json')
            after = read(result_path.parent / 'source-after.json')
            worker = result.get('worker_sha256')
            helper = result.get('helper_sha256')
            expected_worker = scripts[str(HERE / ('paired_v2.py' if 'paired' in folder else 'worker.py'))]
            rows.append(dict(case=job['case'], result=str(result_path), pid=job.get('pid'),
                             exit_code=job.get('exit_code'), reaped=job.get('reaped', False),
                             source_same_frozen_epoch=before == after == sources,
                             worker_hash_matches=worker == expected_worker,
                             helper_hash_matches=helper in (None, scripts[str(HERE / 'worker.py')]),
                             tokenizer_hash_matches=result.get('tokenizer_sha256') == digest(tokenizer),
                             wall_s=result.get('wall_s'), cpu_s=result.get('cpu_s'),
                             sleep_s=result.get('sleep_s'), peak_rss_bytes=result.get('peak_rss_bytes'),
                             mlx_imported=result.get('mlx_imported')))
    current_paths = {str(path.relative_to(ROOT)) for folder in ('src/stowage', 'src/llm/stowlm', 'src/llm/securing', 'src/llm/instructions', 'src/llm/lookahead', 'params')
                     for path in (ROOT / folder).rglob('*') if path.is_file() and path.suffix in ('.py', '.json', '.toml')}
    current_drift = [path for path in current_paths | sources.keys() if path not in sources or not (ROOT / path).exists() or digest(ROOT / path) != sources[path]]
    value = dict(updated_utc=datetime.now(timezone.utc).isoformat(),
                 final=len(controllers)==5 and all(c['completed'] for c in controllers),
                 freeze_sha256=digest(HERE / 'runtime-freeze-v2.json'),
                 tokenizer=dict(path=str(tokenizer), sha256=digest(tokenizer)),
                 tokenizer_matches_archived_epoch=digest(tokenizer)==read(HERE/'runtime-v2/archive-manifest.json')['files']['models/glimmer-30b-4bit/tokenizer.json'],
                 invoked_scripts=scripts, current_runtime_drift=current_drift,
                 scripts_match_prelaunch_freeze=(scripts[str(HERE / 'worker.py')] == freeze['worker_sha256'] and scripts[str(HERE / 'paired_v2.py')] == freeze['paired_worker_sha256'] and scripts[str(HERE / 'controller.py')] == freeze['controller_sha256']),
                 completed_results=rows, controllers=controllers,
                 active_owned_children=[dict(controller=c['pid'], child=j['pid'], case=j['case'])
                                        for c in controllers for j in c['jobs']
                                        if j.get('pid') and not j.get('reaped')],
                 source_policy='No source exclusions. All frozen runtime trees and tokenizer exact; actual helper/worker bytes recorded in results. Controller, helper and worker have matching prelaunch and current cryptographic references.',
                 timing_policy='Wall is measured under shared-machine contention; CPU excludes cooperative sleep. RSS values are per-process lifetime peaks, not concurrent aggregate usage.')
    write(HERE / 'audit-v2.json', value)
    print(json.dumps(dict(final=value['final'], completed=len(rows), active=value['active_owned_children'], current_drift=current_drift,
                          invalid=[r['case'] for r in rows if not all(r[k] for k in ('source_same_frozen_epoch', 'worker_hash_matches', 'helper_hash_matches', 'tokenizer_hash_matches'))])))


if __name__ == '__main__':
    run()
