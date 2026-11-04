"""Read-only native acceptance audit; never controls the existing batch."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import multiprocessing
from pathlib import Path
import time

from .batch import capacity_reading, write_json
from .store import entry_key

HERE = Path(__file__).resolve().parent
AUDIT = HERE/'audits/code6-regate'


def snapshot(source, label):
    with (source/'.catalogue.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        raw = (source/'catalogue.json').read_bytes()
        catalogue = json.loads(raw)
        cases = []
        for key, digest in catalogue.items():
            data = (source/(key+'.json')).read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest
            row = json.loads(data)
            assert entry_key(row['instance_sha256'], row['canonical_rules']) == key
            if row.get('status') != 'checked_plan':
                continue
            case = AUDIT/'inputs'/digest
            case.mkdir(parents=True, exist_ok=True)
            target = case/(key+'.json')
            if target.exists():
                assert target.read_bytes() == data
            else:
                target.write_bytes(data)
                write_json(case/'catalogue.json', {key: digest})
            cases.append(dict(key=key, digest=digest, store_dir=str(case),
                              origin=label, original_catalogue_sha256=hashlib.sha256(raw).hexdigest(),
                              instance_sha256=row['instance_sha256'],
                              previously_accepted=bool(row.get('gate', {}).get('passed'))))
        return cases


def native(case):
    from . import store
    from experiments.mvp.pipeline.contract.schema import sha256_file
    location = Path(case['store_dir'])
    result = store.regate(store_dir=location,
                          expected_catalogue_sha256=sha256_file(location/'catalogue.json'))
    passed = case['key'] in result['passed']
    row = json.loads((location/(case['key']+'.json')).read_text())
    candidate = store.lookup(row['instance_sha256'], row['canonical_rules'], store_dir=location) if passed else None
    gate = candidate.get('gate') if candidate else None
    if gate:
        write_json(AUDIT/'checks'/(case['digest']+'.gate.json'), gate)
    output = dict(case, accepted_now=passed, acceptance_changed=passed != case['previously_accepted'],
                  regate=result, frames=(gate or {}).get('gauges', {}).get('n_frames'),
                  rechecked_utc=(candidate or {}).get('rechecked_utc'),
                  checked_utc=datetime.now(timezone.utc).isoformat(), native_checker_and_gauges=True)
    write_json(AUDIT/'checks'/(case['digest']+'.json'), output)
    return output


def batch_workers():
    count = 0
    for path in (HERE/'runs').glob('*.running.json'):
        try:
            count += not json.loads(path.read_text()).get('worker_released', False)
        except (OSError, ValueError):
            count += 1
    return count


def main():
    AUDIT.mkdir(parents=True, exist_ok=True)
    release = json.loads((HERE/'LATEST_RELEASE.json').read_text())
    sources = [(HERE/'store', 'mutable'), (Path(release['release_dir'])/'store', 'frozen')]
    seen, results = {}, {}
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn'),
                             max_tasks_per_child=1) as pool:
        while True:
            cases = [case for source, label in sources for case in snapshot(source, label)]
            for case in cases:
                seen.setdefault(case['digest'], case)
            queue = [case for digest, case in seen.items() if digest not in results]
            pending = {}
            while queue or pending:
                available = max(0, 4-batch_workers())
                while queue and len(pending) < available and capacity_reading():
                    case = queue.pop(0)
                    pending[pool.submit(native, case)] = case
                if pending:
                    done, _ = wait(pending, timeout=2., return_when=FIRST_COMPLETED)
                    for future in done:
                        case = pending.pop(future)
                        try:
                            results[case['digest']] = future.result()
                        except Exception as error:
                            results[case['digest']] = dict(case, accepted_now=False,
                                acceptance_changed=True, error=f'{type(error).__name__}: {error}')
                        print(json.dumps({k: results[case['digest']].get(k) for k in
                                          ('key', 'origin', 'accepted_now', 'acceptance_changed', 'frames', 'error')}), flush=True)
                else:
                    time.sleep(2.)
            current = [case for source, label in sources for case in snapshot(source, label)]
            if all(case['digest'] in results for case in current):
                break
    write_json(AUDIT/'summary.json', dict(checked_utc=datetime.now(timezone.utc).isoformat(),
        source_memberships=current, unique_records_checked=len(results),
        passed=all(row['accepted_now'] for row in results.values()),
        changed=[row for row in results.values() if row['acceptance_changed']],
        results=list(results.values()), batch_untouched=True, hero_unchanged=True))


if __name__ == '__main__':
    main()
