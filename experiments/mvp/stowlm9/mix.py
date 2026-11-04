"""Hash-bound train-only label/attempt census. No producer/checker imports."""
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import time

from rule_readout import read, write

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FILL_DEFINITION = ('audit.realised_teu / audit.capacity_teu: generated cargo TEU divided by '
                   'geometric decision-cell TEU capacity, not nominal ship capacity or checker-legal capacity. '
                   'Audit instance_sha256 must match the frozen train input hash. Bands are [0,25%), '
                   '[25,50%), [50,75%), [75,100%), and >=100%.')


def fill_band(fill):
    if fill is None:
        return 'unknown'
    if fill < 0:
        raise ValueError('negative fill')
    for limit, name in ((.25, '<25%'), (.5, '25–<50%'), (.75, '50–<75%'), (1, '75–<100%')):
        if fill < limit:
            return name
    return '>=100%'


def lineage(root, manifest):
    result = {manifest['manifest_id']: manifest}
    current = manifest
    while current.get('parent_manifest_id'):
        key = current['parent_manifest_id']
        if key in result:
            raise ValueError('manifest lineage cycle')
        parent = read(root/'history'/key/'manifest.json')
        if not parent or parent.get('manifest_id') != key:
            break
        result[key] = parent
        current = parent
    return result


def terminal_attempts(root, manifests, entries):
    seen, rows = set(), []
    locations = [root/'rule'/'rows', root/'rule'/'attempts']
    locations.extend(root/'history'/key/'rule-rows' for key in manifests)
    for location in locations:
        for path in location.rglob('*.json'):
            row = read(path, {})
            key = row.get('key')
            if (key not in entries or row.get('manifest_id') not in manifests or
                    row.get('status') not in ('complete', 'error') or
                    row.get('input_sha256') != entries[key]['input_sha256']):
                continue
            identity = tuple(row.get(k) for k in ('key', 'pid', 'started_at', 'finished_at'))
            if identity not in seen:
                seen.add(identity)
                rows.append(row)
    return rows


def published_rows(root, manifests):
    generation = (root/'data-85').resolve()
    report = read(generation/'build_report.json', {})
    mid = report.get('manifest_id')
    if not report:
        return [], [], str(generation)
    if mid not in manifests or report.get('outcome_epoch') != manifests[mid]['outcome_epoch']:
        return [], ['published generation is outside current manifest lineage/physics epoch'], str(generation)
    snapshots = {r['key']: r['row_sha256'] for r in report.get('rows', [])}
    rows, issues = [], []
    for line in (generation/'train.jsonl').read_text().splitlines():
        gold = json.loads(line).get('meta', {}).get('gold', {})
        key = Path(gold.get('source', '')).parent.name
        if not key or key != Path(key).name:
            issues.append('invalid published map key')
            continue
        candidates = [root/'rule'/'rows'/f'{key}.json']
        candidates.extend((root/'rule'/'attempts'/key).rglob('*.json'))
        candidates.extend(root/'history'/m/'rule-rows'/f'{key}.json' for m in manifests)
        match = None
        for path in candidates:
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                continue
            if sha256(raw).hexdigest() == snapshots.get(key):
                match = json.loads(raw)
                break
        if (not match or match.get('manifest_id') != mid or match.get('outcome_epoch') != manifests[mid]['outcome_epoch'] or match.get('passed') is not True or
                match.get('status') != 'complete' or gold.get('manifest_id') != mid or
                gold.get('outcome_epoch') != manifests[mid]['outcome_epoch']):
            issues.append(f'{key}: published row SHA/binding unavailable')
        else:
            rows.append(match)
    return rows, issues, str(generation)


def grouped(entries, published, attempts, field):
    attempted = {r['key'] for r in attempts}
    passed = {r['key'] for r in attempts if r.get('passed') is True}
    admitted = {r['key'] for r in published}
    groups = defaultdict(Counter)
    for key, entry in entries.items():
        counts = groups[entry[field]]
        counts['eligible'] += 1
        counts['attempted'] += key in attempted
        counts['checked_pass'] += key in passed
        counts['failed_no_pass'] += key in attempted and key not in passed
        counts['published'] += key in admitted
    result = []
    for name, counts in sorted(groups.items()):
        item = dict(group=name, **{k: counts[k] for k in ('eligible', 'attempted', 'checked_pass', 'failed_no_pass', 'published')})
        item['terminal_attempts'] = sum(entries[r['key']][field] == name for r in attempts)
        item['attempted_pass_rate'] = counts['checked_pass']/counts['attempted'] if counts['attempted'] else None
        item['published_share'] = counts['published']/len(admitted) if admitted else None
        item['attempted_share'] = counts['attempted']/len(attempted) if attempted else None
        item['eligible_share'] = counts['eligible']/len(entries) if entries else None
        result.append(item)
    return result


def render(root=HERE, *, repo=REPO, now=None):
    root, repo = Path(root), Path(repo)
    manifest = read(root/'manifest.json')
    if not manifest:
        return None
    manifests = lineage(root, manifest)
    entries = {}
    audit_issues = []
    for original in manifest['entries']:
        entry = dict(original)
        input_path = Path(entry['input'])
        # Only the frozen train queue may contribute metadata or counts.
        if input_path.is_absolute() or input_path.parts[:4] != ('experiments', 'mvp', 'corpus', 'train') or '..' in input_path.parts:
            raise ValueError(f'non-train input: {input_path}')
        path = repo/input_path.with_name(input_path.stem+'.audit.json')
        audit = read(path, {})
        capacity, cargo = audit.get('capacity_teu'), audit.get('realised_teu')
        fill = None
        if (audit.get('instance_sha256') == entry['input_sha256'] and
                isinstance(capacity, (int, float)) and capacity > 0 and isinstance(cargo, (int, float)) and cargo >= 0):
            fill = cargo/capacity
        else:
            audit_issues.append(entry['key'])
        entry.update(fill=fill, fill_band=fill_band(fill))
        entries[entry['key']] = entry
    attempts = terminal_attempts(root, manifests, entries)
    published, issues, generation = published_rows(root, manifests)
    for row in published:
        if row['key'] not in entries or row.get('input_sha256') != entries[row['key']]['input_sha256']:
            issues.append(f"{row['key']}: published input differs from current frozen train queue")
    published = [r for r in published if r['key'] in entries and r.get('input_sha256') == entries[r['key']]['input_sha256']]
    attempted = {r['key'] for r in attempts}
    passed = {r['key'] for r in attempts if r.get('passed') is True}
    failures = [r for r in attempts if r.get('passed') is not True]
    failed_maps = sorted(attempted-passed)
    result = dict(schema='stowlm9-rule-mix-v1', at=time.time() if now is None else now,
                  manifest_id=manifest['manifest_id'], generation=generation,
                  definition=FILL_DEFINITION, admission_binding_issues=issues, audit_binding_issues=audit_issues,
                  counts=dict(eligible=len(entries), attempted_maps=len(attempted), checked_passing_maps=len(passed),
                              failed_without_passing_label=len(failed_maps), published_labels=len(published),
                              terminal_attempts=len(attempts), failed_attempts=len(failures)),
                  by_ship=grouped(entries, published, attempts, 'ship'),
                  by_size=grouped(entries, published, attempts, 'size'),
                  by_fill=grouped(entries, published, attempts, 'fill_band'),
                  by_yard=grouped(entries, published, attempts, 'yard'),
                  failed_maps=[dict(key=key, ship=entries[key]['ship'], size=entries[key]['size'],
                                    fill=entries[key]['fill'], fill_band=entries[key]['fill_band'],
                                    attempts=[dict(pid=r.get('pid'), started_at=r.get('started_at'), finished_at=r.get('finished_at'),
                                                   status=r.get('status'), planning=r.get('planning'), error=r.get('error'))
                                              for r in failures if r['key'] == key]) for key in failed_maps])
    write(root/'rule'/'MIX.json', result)
    lines = ['# Rule label mix', '', f"Updated {datetime.fromtimestamp(result['at']).astimezone().isoformat(timespec='seconds')}.", '',
             f"Published: {len(published)} labels. Unique terminally attempted: {len(attempted)} maps, "
             f"{len(passed)} with a checked passing plan, {len(failed_maps)} without one. "
             f"Physical terminal attempts: {len(attempts)}, including {len(failures)} failures/retries.", '',
             'Failures are retained in rows/attempt archives and counted here; only passing labels enter SFT. '
             'Attempted means at least one terminal attempt, excluding currently in-flight first attempts. '
             'Passing means any checked passing attempt on that map. Published labels may lag checked winners. '
             'Eligible is the entire fixed train queue; its size proportions differ from the prescribed first-1700 prefix. '
             'Differences between published and attempted shares expose admission/export selection; differences from eligible shares also reflect queue order. '
             'Small groups do not establish a reliable causal difficulty effect.', '', FILL_DEFINITION, '']
    for label, field in [('Ship', 'by_ship'), ('Map size', 'by_size'), ('Fill', 'by_fill'), ('Yard', 'by_yard')]:
        lines.extend([f'## {label}', '', '| Group | Eligible | Attempted maps | Checked pass | Failed no pass | Published | Published / attempted / eligible share |',
                      '|---|---:|---:|---:|---:|---:|---|'])
        for row in result[field]:
            shares = ' / '.join(f"{row[k]:.1%}" if row[k] is not None else '—' for k in ('published_share', 'attempted_share', 'eligible_share'))
            lines.append(f"| {row['group']} | {row['eligible']} | {row['attempted']} | {row['checked_pass']} | {row['failed_no_pass']} | {row['published']} | {shares} |")
        lines.append('')
    lines.extend([f"Audit binding issues: {len(audit_issues)}. Published binding issues: {len(issues)}.", '',
                  'Exact failed-map keys, fill, and attempt failure diagnostics are retained in `MIX.json`.'])
    write(root/'rule'/'MIX.md', '\n'.join(lines)+'\n')
    return result


if __name__ == '__main__':
    result = render()
    print(json.dumps(result['counts'], sort_keys=True))
