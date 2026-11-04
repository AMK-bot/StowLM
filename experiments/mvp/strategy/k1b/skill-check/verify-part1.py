"""Read-only binding/result audit, publishing its verdict only below skill-check."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
RUNNER = str((HERE/'part1.py').relative_to(ROOT))
partial = '--partial' in sys.argv
sha = lambda raw: hashlib.sha256(raw).hexdigest()
errors = []
config_path = HERE/'config-data-baseline.json'
config_audit = dict(present=config_path.exists(), checked_files=0, mtime_qualification=True)
if config_path.exists():
    baseline = json.loads(config_path.read_text())
    for rel, record in baseline['files'].items():
        config_audit['checked_files'] += 1
        if sha((ROOT/rel).read_bytes()) != record['sha256']:
            errors.append(f'config or data changed during run: {rel}')
        if not record['mtime_predates_run']:
            errors.append(f'late config baseline not qualified by pre-run mtime: {rel}')
    for rel, expected in baseline['offset_pins'].items():
        if sha((ROOT/rel).read_bytes()) != expected:
            errors.append(f'offset pin mismatch: {rel}')
else:
    errors.append('supplemental config/data baseline missing')
source_hashes = json.loads((HERE/'source-hashes.json').read_text())
for rel, expected in source_hashes.items():
    if sha((ROOT/rel).read_bytes()) != expected:
        errors.append(f'upstream or harness source changed: {rel}')
manifests = {sha(json.dumps(source_hashes, sort_keys=True).encode()): source_hashes}
for path in (HERE/'source-manifests').glob('*.json'):
    values = json.loads(path.read_text())
    if sha(json.dumps(values, sort_keys=True).encode()) != path.stem:
        errors.append(f'bad archived source manifest: {path.name}')
    manifests[path.stem] = values
rows = [json.loads(p.read_text()) for p in (HERE/'rows').rglob('sample-*.json')]
keys = {(r['arm'], r['instance_id'], r['sample']) for r in rows}
if len(keys) != len(rows):
    errors.append('duplicate candidate keys')
if not partial and len(rows) != 2640:
    errors.append(f'expected2640 rows, got{len(rows)}')
for row in rows:
    binding = row['binding']
    for prefix in ('input', 'source'):
        if sha((ROOT/binding[prefix]).read_bytes()) != binding[f'{prefix}_sha256']:
            errors.append(f'{prefix} changed: {binding[prefix]}')
    record = manifests.get(binding['deterministic_source_sha256'])
    if record is None:
        errors.append(f'unknown source manifest: {binding["deterministic_source_sha256"]}')
    elif {k:v for k,v in record.items() if k != RUNNER} != {k:v for k,v in source_hashes.items() if k != RUNNER}:
        errors.append(f'upstream source manifest changed: {binding["source"]}')
    if row.get('error'):
        errors.append(f'candidate processing error: {binding["source"]}: {row["error"]}')
    if row['complete'] and row['sample'] == 0:
        if sha((ROOT/row['prepared_path']).read_bytes()) != row['chosen_sha256']:
            errors.append(f'prepared plan changed: {binding["source"]}')
    if row['complete']:
        expected = all(row.get(f) is True for f in ('move_legal','load_complete','sight_clear','static_ok','frontier_mask'))
        if row.get('all_clear') is not expected:
            errors.append(f'all_clear inconsistent: {binding["source"]}')
result = dict(verified_utc=datetime.now(timezone.utc).isoformat(), passed=not errors,
              partial=partial, n_rows=len(rows), n_keys=len(keys), errors=errors,
              source_manifests=len(manifests), current_upstream_bound=True,
              supplemental_config_audit=config_audit,
              native_environment={k:__import__('os').environ.get(k) for k in ('PYTHONDONTWRITEBYTECODE','UV_CACHE_DIR')})
target = HERE/('verification-partial.json' if partial else 'verification.json')
target.write_text(json.dumps(result, indent=1, sort_keys=True)+'\n')
if not partial and (HERE/'part1-summary.json').exists():
    summary_path = HERE/'part1-summary.json'
    summary = json.loads(summary_path.read_text())
    summary['final_binding_recheck'] = result
    summary_path.write_text(json.dumps(summary, indent=1, sort_keys=True)+'\n')
    with (HERE/'part1.md').open('a') as stream:
        stream.write(f"\nFinal binding recheck at {result['verified_utc']}: {'PASS' if result['passed'] else 'FAIL; results provisional'}, {len(rows)} rows; {len(errors)} errors. See `verification.json`.\n")
    if result['passed']:
        (HERE/'part1.DONE').write_text(result['verified_utc']+'\n')
print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result['passed'] else 1)
