"""Cheap live rule-label readout with a separate published-export summary.

Live admission uses existing checker/mask evidence and producer bindings. The
exporter remains the publication authority; no checker or producer imports here.
"""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import mean, median
import time

HERE = Path(__file__).resolve().parent


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    tmp.write_text(value if isinstance(value, str) else json.dumps(value, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def average(values):
    return mean(values) if values else None


def _attempts(root, manifest):
    rows, seen = [], set()
    allowed_manifests = {manifest['manifest_id']}
    ancestor = manifest
    while ancestor.get('parent_manifest_id') and ancestor['parent_manifest_id'] not in allowed_manifests:
        parent = ancestor['parent_manifest_id']
        allowed_manifests.add(parent)
        ancestor = read(root/'history'/parent/'manifest.json', {})
    for directory in ('rows', 'attempts'):
        for path in (root/'rule'/directory).rglob('*.json'):
            raw = path.read_bytes()
            row = json.loads(raw)
            # Manifest/provenance rebinding does not create a new attempt.
            # Process reuse cannot collide because both timestamps are included.
            identity = tuple(row.get(k) for k in ('key', 'pid', 'started_at', 'finished_at'))
            if identity not in seen and row.get('manifest_id') in allowed_manifests and row.get('status') in ('complete', 'error'):
                seen.add(identity)
                rows.append(row)
    return rows


def _admitted(root, manifest):
    # Resolve once to avoid observing two generations during atomic publication.
    published = (root/'data-85').resolve()
    report = read(published/'build_report.json', {})
    if report.get('manifest_id') != manifest['manifest_id'] or report.get('outcome_epoch') != manifest['outcome_epoch']:
        return [], []
    snapshots = {r['key']: r['row_sha256'] for r in report.get('rows', [])}
    admitted, issues = [], []
    train = published/'train.jsonl'
    if not train.exists():
        return [], []
    for line in train.read_text().splitlines():
        gold = json.loads(line).get('meta', {}).get('gold', {})
        key = Path(gold.get('source', '')).parent.name
        # Retry can replace the live row after publication. The frozen exact
        # execution remains authoritative in that map's archived attempts.
        candidates = [root/'rule'/'rows'/f'{key}.json']
        if key and Path(key).name == key:
            candidates.extend((root/'rule'/'attempts'/key).rglob('*.json'))
        row = None
        for rowpath in candidates:
            try:
                raw = rowpath.read_bytes()
            except FileNotFoundError:
                continue
            if sha256(raw).hexdigest() == snapshots.get(key):
                row = json.loads(raw)
                break
        if row is None:
            issues.append(f'{key}: published producer row absent or SHA differs in live/archive')
            continue
        if (row.get('manifest_id') != manifest['manifest_id'] or row.get('passed') is not True or
                row.get('status') != 'complete' or gold.get('manifest_id') != manifest['manifest_id'] or
                gold.get('outcome_epoch') != manifest['outcome_epoch']):
            issues.append(f'{key}: published producer row binding differs')
            continue
        admitted.append(row)
    return sorted(admitted, key=lambda r: (r['finished_at'], r['key'])), issues


def _live_rows(root, manifest):
    """Count one current terminal row per map, independently of export cadence."""
    entries = {entry['key']: entry for entry in manifest.get('entries', [])}
    terminal, admitted, issues = [], [], []
    for path in sorted((root/'rule'/'rows').glob('*.json')):
        row = read(path, {})
        key = row.get('key')
        if row.get('manifest_id') != manifest['manifest_id'] or row.get('status') not in ('complete', 'error'):
            continue
        entry = entries.get(key)
        if entry is None or path.stem != key:
            issues.append(f'{key}: current row absent from manifest or filename differs')
            continue
        expected = dict(manifest_id=manifest['manifest_id'], stream='rule',
                        input_sha256=entry['input_sha256'],
                        baseline_record_sha256=entry.get('baseline_record_sha256'))
        if row.get('binding') != expected or row.get('outcome_epoch') != manifest['outcome_epoch']:
            issues.append(f'{key}: current producer row binding or outcome epoch differs')
            continue
        terminal.append(row)
        if row.get('status') != 'complete' or row.get('passed') is not True:
            continue
        record = row.get('record', {})
        if (record.get('outcome_epoch') != manifest['outcome_epoch'] or
                record.get('input_sha256') != entry['input_sha256'] or
                record.get('report', {}).get('passed') is not True or
                record.get('mask', {}).get('passed') is not True):
            issues.append(f'{key}: current passing row lacks bound checker/mask evidence')
            continue
        admitted.append(row)
    return sorted(admitted, key=lambda r: (r['finished_at'], r['key'])), terminal, issues


def _summary(rows, attempts):
    paired = [r for r in rows if finite(r.get('baseline_moves'))]
    rawpaired = [r for r in rows if finite(r.get('baseline_raw_moves'))]
    walls = [r['wall_s'] for r in rows if finite(r.get('wall_s'))]
    return dict(labels=len(rows), attempts=len(attempts), pass_rate=len(rows)/len(attempts) if attempts else None,
                source_counts=dict(Counter(r['source'] for r in rows)),
                paired_canonical=dict(maps=len(paired), winner_mean_moves=average([r['moves'] for r in paired]),
                                      best_d172_mean_moves=average([r['baseline_moves'] for r in paired])),
                paired_raw=dict(maps=len(rawpaired), winner_mean_moves=average([r['moves'] for r in rawpaired]),
                                best_d172_mean_moves=average([r['baseline_raw_moves'] for r in rawpaired])),
                wall_seconds=dict(mean=average(walls), median=median(walls) if walls else None),
                maps=[dict(key=r['key'], moves=r['moves'], source=r['source'], wall_s=r.get('wall_s'),
                           baseline_moves=r.get('baseline_moves'), baseline_raw_moves=r.get('baseline_raw_moves')) for r in rows])


def render(root=HERE, *, now=None):
    """Refresh cheap reports; freeze milestone and warning artifacts once created."""
    root = Path(root)
    now = time.time() if now is None else now
    manifest = read(root/'manifest.json')
    if not manifest:
        return None
    attempts = _attempts(root, manifest)
    published, published_issues = _admitted(root, manifest)
    admitted, terminal, issues = _live_rows(root, manifest)
    owner = read(root/'rule'/'owner.json', {})
    started = owner.get('started_at', now)
    elapsed = max(0, now-started)
    current = [r for r in attempts if r.get('launch_id') == owner.get('launch_id') and r.get('started_at', 0) >= started]
    checked = [r for r in current if r.get('passed') is True and r.get('status') == 'complete']
    deadline = datetime.fromisoformat(manifest['rule_deadline']).timestamp()
    remaining = max(0, deadline-now)
    # An upper bound assumes every future completed attempt yields a label. A
    # warning is credible even with zero successes when this bound is below400.
    attempt_rate = len(current)/elapsed if elapsed else 0
    optimistic = len(admitted) + remaining*attempt_rate
    observed = len(admitted) + (remaining*len(checked)/elapsed if elapsed else 0)
    credible = len(current) >= 5 and elapsed >= 120
    result = dict(schema='stowlm9-rule-readout-v2', at=now, manifest_id=manifest['manifest_id'],
                  admitted={**_summary(admitted, terminal), 'denominator': 'Current unique terminal map rows (complete or error)',
                            'terminal_maps': len(terminal)},
                  admission_binding_issues=issues,
                  published_snapshot={**_summary(published, []),
                                      'denominator': 'Published snapshot only; no live pass rate'},
                  published_binding_issues=published_issues,
                  physical_attempts=dict(terminal_attempts=len(attempts),
                                         checked_successes=sum(r.get('passed') is True and r.get('status') == 'complete' for r in attempts),
                                         pass_rate=sum(r.get('passed') is True and r.get('status') == 'complete' for r in attempts)/len(attempts) if attempts else None),
                  forecast=dict(workers=3, launch_id=owner.get('launch_id'), elapsed_s=elapsed,
                                terminal_attempts=len(current), checked_successes=len(checked),
                                observed_labels_by_deadline=observed, all_future_attempts_pass_bound=optimistic,
                                credible=credible, deadline=manifest['rule_deadline'],
                                method='Current-launch actual elapsed throughput; includes failures; assumes unchanged 3-worker load. Live admitted rows form the base; future attempt throughput is counted once.'))
    write(root/'rule'/'READOUT.json', result)
    first_path = root/'rule'/'FIRST20.json'
    if len(published) >= 20 and not first_path.exists():
        first = published[:20]
        cutoff = first[-1]['finished_at']
        cohort = [r for r in attempts if r.get('finished_at', now+1) <= cutoff]
        milestone = dict(at=now, manifest_id=manifest['manifest_id'], **_summary(first, cohort))
        write(first_path, milestone)
        write(root/'rule'/'FIRST20.md', '# First 20 admitted rule labels\n\n'
              f"Pass rate: 20/{len(cohort)} terminal attempts ({milestone['pass_rate']:.1%}). "
              'Admission is from the published data-85 generation.\n\n'
              f"Canonical paired moves: {milestone['paired_canonical']}.\n\n"
              f"Raw paired moves: {milestone['paired_raw']}.\n\n"
              f"Wall seconds per admitted map: {milestone['wall_seconds']}.\n\n"
              f"Forecast: {result['forecast']}.\n")
    warning = root/'rule'/'UNDER400.json'
    if credible and optimistic < 400 and not warning.exists():
        write(warning, dict(at=now, reason='Even the all-future-attempts-pass throughput bound is below 400 by cutoff.', **result['forecast']))
    return result
