"""Prepare booking-independent linear acceleration caches; never read maps.

The unmodified public warm_cache hook uses empty synthetic vessel contexts.
Only exact numerical cache keys requested by that hook are copied. Source and
margin-policy validation remain unchanged; any qualification failure is kept.
Run after the generation pool closes, with one admitted CPU worker.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from time import perf_counter

from capacity import wait_for_slot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = HERE / 'runtime'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n')
    temporary.replace(path)


def copy_independent(source, target):
    assert target.is_relative_to(RUNTIME), 'cache/evidence destination outside runtime'
    before = sha(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name+'.copying')
    shutil.copyfile(source, temporary)
    temporary.replace(target)
    assert before == sha(source) == sha(target), f'Copy/source changed: {source}'
    assert target.stat().st_ino != source.stat().st_ino


def main():
    started = perf_counter()
    admission = wait_for_slot()
    assert not any(name == 'stowage' or name.startswith('stowage.') for name in sys.modules)
    for name in list(os.environ):
        if name.startswith('STOWAGE_'):
            del os.environ[name]
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(RUNTIME / 'experiments/cache/vessels')
    os.environ['TMPDIR'] = str(HERE / 'tmp')
    (HERE / 'tmp').mkdir(exist_ok=True)
    sys.path.insert(0, str(RUNTIME / 'src'))

    snapshot = json.loads((HERE / 'runtime-snapshot.json').read_text())
    expected_sources = snapshot['files']
    assert all(sha(RUNTIME / path) == value for path, value in expected_sources.items())
    snapshot_sha = sha(HERE / 'runtime-snapshot.json')

    from stowage.linear import cache, margin_policy
    from stowage.linear.cache import coefficient_family_key, response_calibration_identity
    from stowage.linear.physics import _params_for
    from stowage.linear.reusable import family_band, coefficient_family
    from stowage.linear.warm import _empty_context, warm_cache
    from stowage.vessel import load_vessel, load_fleet_preset

    original_load = cache.load_payload
    copied = {}
    external = {}
    expected_evidence = {}
    requested_keys = set()

    def pull(key, namespace):
        """Copy only an exact requested inert numeric artifact, then validate it."""
        if not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('invalid cache key')
        source_folder = ROOT / 'experiments/cache/linear' / namespace
        manifest = source_folder / f'{key}.json'
        if not manifest.exists():
            return
        value = json.loads(manifest.read_text())
        filename = value.get('file', '')
        if not re.fullmatch(re.escape(key)+r'\.[0-9a-f]{64}\.npz', filename):
            raise ValueError('invalid source numerical cache filename')
        data = source_folder / filename
        if sha(data) != value['sha256']:
            raise ValueError('source numerical cache checksum mismatch')
        target_folder = RUNTIME / 'experiments/cache/linear' / namespace
        for source in (data, manifest):
            target = target_folder / source.name
            copy_independent(source, target)
            copied[target.relative_to(HERE).as_posix()] = sha(target)

    def load_with_exact_copy(key, *, namespace='payload', root=None):
        requested_keys.add((namespace, key))
        result = original_load(key, namespace=namespace, root=root)
        if result is None and root is None:
            pull(key, namespace)
            result = original_load(key, namespace=namespace, root=root)
        return result

    # This extends artifact availability only. The unchanged cache reader still
    # validates every checksum, schema and numeric payload after copying.
    cache.load_payload = load_with_exact_copy

    def prepare_evidence(record):
        """Copy only relative pins actually checked by unchanged inward policy."""
        pins = list(margin_policy._conservative_evidence(record) or [])
        pins += list(record.get('boundary_list', {}).get('evidence', []))
        for pin in pins:
            raw = Path(pin['path'])
            if raw.is_absolute():
                # Absolute evidence is deliberately kept absolute: rewriting it
                # would alter a frozen qualification/policy contract.
                if raw.is_file():
                    external[str(raw)] = sha(raw)
                continue
            relative = raw.as_posix()
            if not relative.startswith(('src/stowage/', 'experiments/linear-model/',
                                        'experiments/stability/', 'experiments/cache/stability/')):
                raise ValueError(f'Unexpected non-physics evidence pin: {relative}')
            source = (ROOT / raw).resolve()
            target = (RUNTIME / raw).resolve()
            if not source.is_relative_to(ROOT) or not target.is_relative_to(RUNTIME):
                raise ValueError('Evidence pin escapes its root')
            expected_evidence[target.relative_to(HERE).as_posix()] = pin['sha256']
            if target.exists():
                # Existing frozen sources/params and stability artifacts are
                # never replaced by an evidence preparation step.
                continue
            copy_independent(source, target)
            copied[target.relative_to(HERE).as_posix()] = sha(target)

    rows = []
    try:
        for ship in ('feeder_500', 'feeder_800'):
            for preset in ('full', 'medium'):
                row = dict(ship=ship, preset=preset, status='started')
                before = perf_counter()
                try:
                    vessel = load_vessel(ship) if preset == 'full' else load_fleet_preset(ship, preset)
                    instance = _empty_context(vessel)
                    assert not instance.containers and not instance.load_list and not instance.onboard.placements
                    _, params = _params_for(instance)
                    band = family_band(instance, params)
                    key = coefficient_family_key(instance, params, band)
                    pull(key, 'model')
                    family = coefficient_family(instance, params, band)
                    identity = response_calibration_identity(family)
                    row['response_identity'] = identity
                    margin_policy.assert_policy_epoch(family, identity)
                    bank = json.loads(Path(margin_policy._BANK_PATH).read_text())
                    record = bank['entries'].get(identity)
                    if record is not None:
                        prepare_evidence(record)
                    row.update(status='ok', report=warm_cache(vessel, workers=1))
                except Exception as error:
                    row.update(status='error', error=f'{type(error).__name__}: {error}')
                row['elapsed_s'] = perf_counter()-before
                rows.append(row)
                print(json.dumps(dict(event='linear_cache_prepared', **row)), flush=True)
    finally:
        cache.load_payload = original_load

    # Include newly built numerical artifacts as well as independent copies.
    numerical = RUNTIME / 'experiments/cache/linear'
    if numerical.exists():
        for path in numerical.rglob('*'):
            if path.is_file() and path.suffix in ('.json', '.npz'):
                copied[path.relative_to(HERE).as_posix()] = sha(path)
    assert all(sha(RUNTIME / path) == value for path, value in expected_sources.items())
    assert sha(HERE / 'runtime-snapshot.json') == snapshot_sha
    completed = len(rows) == 4 and all(row['status'] == 'ok' for row in rows)
    manifest = dict(format='pipeline-linear-acceleration-v1', completed=completed,
        generated_at_utc=datetime.now(timezone.utc).isoformat(), admission=admission,
        helper_sha256=sha(__file__), runtime_snapshot_sha256=snapshot_sha,
        files=copied, evidence_expected_sha256=expected_evidence,
        external_absolute_evidence_sha256=external,
        requested_keys=[dict(namespace=n, key=k) for n,k in sorted(requested_keys)],
        contexts='Empty synthetic vessels: no bookings, onboard boxes, maps or plans.',
        map_files_opened=False, plans_used=False, results=rows, elapsed_s=perf_counter()-started)
    manifest_path = HERE / 'linear-cache-manifest.json'
    write(manifest_path, manifest)
    status_path = HERE / 'runtime-cache-status.json'
    status = json.loads(status_path.read_text())
    status['linear_acceleration'] = dict(completed=completed,
        manifest='linear-cache-manifest.json', manifest_sha256=sha(manifest_path),
        helper='prepare_linear_cache.py', helper_sha256=sha(__file__),
        successes=sum(row['status'] == 'ok' for row in rows), contexts=len(rows))
    write(status_path, status)
    return 0 if completed else 2


if __name__ == '__main__':
    raise SystemExit(main())
