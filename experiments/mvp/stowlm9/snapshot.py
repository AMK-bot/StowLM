"""D209 durable rule snapshot events; workers continue while this child exports.

API: reserve_event(root, manifest, admitted_count, now=None) returns a durable
reservation or None. Launch this file with --manifest/--root/--event ID; a failed
child leaves that reservation pending for retry. Only export_event publishes
success receipts. Snapshot 0000 is the 19:45 snapshot; later snapshots are due
at each additional 200 admitted labels, measured from 0000's exported count.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
import uuid

HERE = Path(__file__).resolve().parent
SCHEMA = 'stowlm9-rule-snapshots-v1'


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}-{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    os.replace(temp, path)


@contextmanager
def locked(root, name):
    directory = Path(root) / 'rule' / 'snapshots'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield directory


def _receipts(directory):
    receipts = [read(path) for path in sorted(directory.glob('[0-9][0-9][0-9][0-9].json'))]
    for sequence, receipt in enumerate(receipts):
        if receipt.get('schema') != SCHEMA or receipt.get('number') != sequence:
            raise ValueError('snapshot receipt sequence is incomplete or invalid')
    return receipts


def reserve_event(root, manifest, admitted_count, now=None):
    """Reserve/recover one due export; counts are admitted labels, never attempts.

    The supervisor must not launch two children for the same reservation. A
    separate export-event lock also makes accidental duplicate launches safe.
    Immutable receipts are the authority after a crash between commit steps.
    """
    now = time.time() if now is None else now
    if isinstance(admitted_count, bool) or not isinstance(admitted_count, int) or admitted_count < 0:
        raise ValueError('admitted_count must be a nonnegative integer')
    with locked(root, '.state.lock') as directory:
        receipts = _receipts(directory)
        state_path = directory / 'state.json'
        state = read(state_path) if state_path.exists() else dict(schema=SCHEMA)
        pending = state.get('pending')
        if pending and pending['number'] < len(receipts):
            pending = None  # Receipt committed before a supervisor/host restart.
        if pending:
            if pending['manifest_id'] != manifest['manifest_id']:
                raise ValueError('pending snapshot belongs to an earlier manifest; migrate explicitly')
            return pending
        deadline = datetime.fromisoformat(manifest['rule_deadline']).timestamp()
        number = len(receipts)
        threshold = receipts[0]['train_labels'] + number * 200 if number else None
        if (not number and now < deadline) or (number and admitted_count < threshold):
            return None
        event = dict(id=f'{number:04d}-{uuid.uuid4().hex}', number=number,
                     kind='1945' if not number else 'additional-200-labels',
                     manifest_id=manifest['manifest_id'], reserved_at=now,
                     admitted_at_reservation=admitted_count, threshold=threshold,
                     scheduled_at=deadline if not number else None)
        atomic(state_path, dict(schema=SCHEMA, pending=event))
        return event


def _validate_publication(root, manifest, report):
    """Verify the immutable generation itself, not a mutable latest symlink."""
    root = Path(root).resolve()
    generation = Path(report['generation']).resolve()
    if not generation.is_relative_to(root / '.exports') or not generation.is_dir():
        raise ValueError('snapshot generation is outside immutable exports')
    actual = read(generation / 'build_report.json')
    if (actual != report or report.get('manifest_id') != manifest['manifest_id'] or
            report.get('stream') != 'rule' or report.get('outcome_epoch') != manifest['outcome_epoch']):
        raise ValueError('snapshot report binding differs')
    pinned = (generation / 'admitted_manifest.json').read_bytes()
    if read(generation / 'admitted_manifest.json') != manifest or sha(pinned) != report['manifest_sha256']:
        raise ValueError('snapshot manifest bytes differ')
    val = (generation / 'val.jsonl').read_bytes()
    train = (generation / 'train.jsonl').read_bytes()
    val_rows = [json.loads(line) for line in val.splitlines() if line.strip()]
    train_rows = [json.loads(line) for line in train.splitlines() if line.strip()]
    if (len(val_rows) != 80 or any(row.get('split') != 'val' for row in val_rows) or
            any(row.get('split') != 'train' for row in train_rows) or
            len(train_rows) != report['counts']['train'] or
            sha(val) != report['frozen_val']['sha256'] or
            (manifest.get('val_sha256') and sha(val) != manifest['val_sha256']) or
            report['frozen_val'].get('train_identity_overlap') != 0):
        raise ValueError('snapshot train/val separation or byte binding differs')
    return generation, dict(train_sha256=sha(train), val_sha256=sha(val),
                            build_report_sha256=sha((generation / 'build_report.json').read_bytes()))


def _finish(root, manifest, event, report, now):
    generation, hashes = _validate_publication(root, manifest, report)
    number = event['number']
    target = Path(root) / f'data-85-{number:04d}'
    if target.is_symlink():
        if target.resolve() != generation:
            raise ValueError('numbered snapshot already names another generation')
    elif target.exists():
        raise ValueError('numbered snapshot must be an immutable symlink')
    else:
        target.symlink_to(generation.relative_to(Path(root).resolve()), target_is_directory=True)
    receipt = dict(schema=SCHEMA, **event, published_at=now, train_labels=report['counts']['train'],
                   val_rows=80, generation=str(generation), output=str(target),
                   manifest_sha256=report['manifest_sha256'], outcome_epoch=manifest['outcome_epoch'],
                   frozen_val_separate=True, **hashes)
    directory = Path(root) / 'rule' / 'snapshots'
    atomic(directory / f'{number:04d}.json', receipt)
    if number == 0:
        atomic(Path(root) / 'rule' / 'EXPORT.DONE', receipt)
    atomic(directory / 'state.json', dict(schema=SCHEMA, pending=None, last_number=number))
    return receipt


def export_event(manifest_path, event_id, *, root=HERE, exporter=None):
    """Run or recover one event; leave a failed attempt pending, never mark done."""
    root = Path(root).resolve()
    with locked(root, '.event.lock') as directory:
        manifest = read(manifest_path)
        receipts = _receipts(directory)
        for receipt in receipts:
            if receipt['id'] == event_id:
                # Revalidate retained files before acknowledging an idempotent retry.
                generation = Path(receipt['generation'])
                report = read(generation / 'build_report.json')
                _, hashes = _validate_publication(root, manifest, report)
                if any(receipt[key] != value for key, value in hashes.items()):
                    raise ValueError('retained snapshot bytes changed')
                if receipt['number'] == 0:
                    atomic(root / 'rule' / 'EXPORT.DONE', receipt)
                return receipt
        event = read(directory / 'state.json').get('pending')
        if not event or event['id'] != event_id or event['manifest_id'] != manifest['manifest_id']:
            raise ValueError('snapshot reservation missing or manifest changed')
        if exporter is None:
            spec = importlib.util.spec_from_file_location('_stowlm9_snapshot_export', HERE / 'export.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            exporter = module.export_stream
        prepared = directory / f"{event['number']:04d}.prepared.json"
        if prepared.exists():
            saved = read(prepared)
            if saved['event_id'] != event_id:
                raise ValueError('prepared generation belongs to another event')
            report = read(Path(saved['generation']) / 'build_report.json')
        else:
            report = exporter(manifest_path, 'rule', root=root)
            _validate_publication(root, manifest, report)
            atomic(prepared, dict(event_id=event_id, generation=report['generation']))
        if read(manifest_path) != manifest:
            raise ValueError('manifest changed during snapshot export')
        with locked(root, '.state.lock'):
            return _finish(root, manifest, event, report, time.time())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=HERE)
    parser.add_argument('--event', required=True)
    args = parser.parse_args()
    print(json.dumps(export_event(args.manifest, args.event, root=args.root), indent=2))


if __name__ == '__main__':
    main()
