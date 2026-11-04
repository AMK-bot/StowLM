"""D132 exact-byte transaction; invoked only at an operator-selected boundary.

The package is prepared and qualified in scratch. Production is never used to
resolve patch conflicts. Unexpected source additions, removals, bytes or modes
block both installation and rollback, protecting concurrent owners' edits.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / 'package'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_info(path: Path):
    if path.is_symlink():
        raise RuntimeError(f'Refusing symlink: {path}')
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError(f'Expected regular file: {path}')
    return {'sha256': sha(path.read_bytes()), 'mode': stat.S_IMODE(path.stat().st_mode)}


def safe(root: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts:
        raise RuntimeError(f'Unsafe manifest path: {relative}')
    target = root / part
    for candidate in (target, *target.parents):
        if candidate.is_symlink():
            raise RuntimeError(f'Refusing path through symlink: {candidate}')
    return target


def source_info(root: Path) -> dict:
    result = {}
    for path in sorted((root / 'src/stowage').rglob('*')):
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_symlink():
            raise RuntimeError(f'Refusing source symlink: {path}')
        if path.is_file():
            result[str(path.relative_to(root))] = file_info(path)
    return result


def mismatch(actual, expected) -> list:
    return [key for key in sorted(set(actual) | set(expected))
            if actual.get(key) != expected.get(key)]


def verify_epoch(root, manifest, epoch):
    expected = manifest['src_stowage_' + epoch]
    errors = mismatch(source_info(root), expected)
    for relative, target in manifest['targets'].items():
        if file_info(safe(root, relative)) != target[epoch]:
            errors.append(relative)
    for relative, wanted in manifest.get('qualified_inputs', {}).items():
        if file_info(safe(root, relative)) != wanted:
            errors.append(relative)
    if errors:
        raise RuntimeError(f'{epoch} source/target/input mismatch: {sorted(set(errors))}')


def verify_package():
    manifest_path = PACKAGE / 'manifest.json'
    expected = (PACKAGE / 'manifest.sha256').read_text().strip()
    raw = manifest_path.read_bytes()
    if sha(raw) != expected:
        raise RuntimeError('Package manifest checksum mismatch')
    manifest = json.loads(raw)
    if not manifest.get('qualification_complete'):
        raise RuntimeError('Combined package has not been marked qualified')
    for relative, wanted in manifest['artifacts'].items():
        path = safe(PACKAGE, relative)
        if sha(path.read_bytes()) != wanted:
            raise RuntimeError(f'Package artifact checksum mismatch: {relative}')
    for relative, wanted in manifest.get('tooling_artifacts', {}).items():
        if sha(safe(HERE.parent, relative).read_bytes()) != wanted:
            raise RuntimeError(f'Installer/rollback tooling checksum mismatch: {relative}')
    return manifest, expected


def atomic(path: Path, data: bytes, mode: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.d132-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value):
    atomic(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode(), 0o600)


def fast_tests(root, manifest, state):
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0',
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
               VECLIB_MAXIMUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1',
               PYTHONPATH=str(root / 'src') + os.pathsep + str(root))
    env.pop('D130_HEAT_ENGINE_SOURCE', None)
    for index, arguments in enumerate(manifest['fast_test_commands']):
        command = [sys.executable, *(argument.format(package=PACKAGE) for argument in arguments)]
        with (state / f'fast-{index}.stdout').open('wb') as out, \
                (state / f'fast-{index}.stderr').open('wb') as err:
            result = subprocess.run(command, cwd=root, env=env, stdout=out, stderr=err,
                                    check=False)
        if result.returncode:
            raise RuntimeError(f'Fast test command {index} failed ({result.returncode}); '
                               f'files remain installed, explicit ROLLBACK is available; logs: {state}')


@contextmanager
def transaction_lock(state, lock_path, check_only):
    if check_only:
        # A preflight makes no metadata/source writes and is not a reservation.
        yield
        return
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def execute(action, root, check_only=False, run_tests=True):
    manifest, manifest_sha = verify_package()
    root = Path(root).absolute()
    if '..' in root.parts:
        raise RuntimeError('Root must not contain parent traversal')
    safe(root, '')  # Check the supplied root and ancestors before resolve hides links.
    root = root.resolve(strict=True)
    state_relative = 'experiments/mvp/batch-d132/assembly/install-state'
    state = safe(root, state_relative)
    lock_path = safe(root, state_relative + '/transaction.lock')
    journal = safe(root, state_relative + '/state.json')
    backup = safe(root, state_relative + '/backup')
    # Reject preexisting redirected backup ancestors before even opening a lock.
    for relative in manifest['targets']:
        safe(backup, relative)
    with transaction_lock(state, lock_path, check_only):
        if action == 'install':
            try:
                verify_epoch(root, manifest, 'after')
                already = True
            except RuntimeError:
                verify_epoch(root, manifest, 'before')
                already = False
            if check_only:
                return {'action': action, 'state': 'already_installed' if already else 'ready',
                        'writes': False, 'source_writes': False, 'metadata_writes': False,
                        'manifest_sha256': manifest_sha}
            if not already:
                if journal.exists():
                    previous = json.loads(journal.read_text())
                    if previous.get('phase') not in ('rolled_back',):
                        raise RuntimeError('Existing transaction journal requires inspection/rollback')
                # Verify every expected source/absence before making any source write.
                for relative, target in manifest['targets'].items():
                    if target['before'] is not None:
                        atomic(safe(backup, relative), safe(root, relative).read_bytes(),
                               target['before']['mode'])
                record = {'manifest_sha256': manifest_sha, 'root': str(root),
                          'started_utc': datetime.now(timezone.utc).isoformat(),
                          'phase': 'applying', 'written': []}
                write_json(journal, record)
                verify_epoch(root, manifest, 'before')
                for relative, target in manifest['targets'].items():
                    destination = safe(root, relative)
                    if file_info(destination) != target['before']:
                        raise RuntimeError(f'Concurrent target change: {relative}')
                    atomic(destination, safe(PACKAGE / 'after', relative).read_bytes(),
                           target['after']['mode'])
                    record['written'].append(relative)
                    write_json(journal, record)
                verify_epoch(root, manifest, 'after')
                record['phase'] = 'installed'
                write_json(journal, record)
            else:
                if not journal.exists():
                    raise RuntimeError('After bytes already present but no owned transaction; refusing to claim installation')
                prior = json.loads(journal.read_text())
                if prior.get('manifest_sha256') != manifest_sha or prior.get('root') != str(root):
                    raise RuntimeError('Existing installation is not owned by this package/root')
            if run_tests:
                fast_tests(root, manifest, state)
                verify_epoch(root, manifest, 'after')
            return {'action': action, 'state': 'already_installed' if already else 'installed',
                    'manifest_sha256': manifest_sha, 'fast_tests_run': run_tests}
        if not journal.exists():
            raise RuntimeError('No owned installation journal; refusing rollback')
        record = json.loads(journal.read_text())
        if record.get('manifest_sha256') != manifest_sha or record.get('root') != str(root):
            raise RuntimeError('Transaction ownership/manifest mismatch')
        if record.get('phase') == 'rolled_back':
            verify_epoch(root, manifest, 'before')
            return {'action': action, 'state': 'already_rolled_back', 'writes': False}
        # A partial interrupted install is safe to unwind only if every file is
        # exactly one recorded epoch and no unrelated stowage file changed.
        current = source_info(root)
        expected = dict(manifest['src_stowage_before'])
        for relative, target in manifest['targets'].items():
            actual = file_info(safe(root, relative))
            if actual not in (target['before'], target['after']):
                raise RuntimeError(f'Foreign target edit blocks rollback: {relative}')
            if relative.startswith('src/stowage/'):
                if actual is None:
                    expected.pop(relative, None)
                else:
                    expected[relative] = actual
            if target['before'] is not None:
                if file_info(safe(backup, relative)) != target['before']:
                    raise RuntimeError(f'Backup checksum mismatch: {relative}')
        if mismatch(current, expected):
            raise RuntimeError(f'Unrelated source drift blocks rollback: {mismatch(current, expected)}')
        for relative, wanted in manifest.get('qualified_inputs', {}).items():
            if file_info(safe(root, relative)) != wanted:
                raise RuntimeError(f'Qualified input drift blocks rollback: {relative}')
        if check_only:
            return {'action': action, 'state': 'ready', 'writes': False,
                    'source_writes': False, 'metadata_writes': False}
        for relative, target in reversed(list(manifest['targets'].items())):
            destination = safe(root, relative)
            if file_info(destination) not in (target['before'], target['after']):
                raise RuntimeError(f'Concurrent rollback target edit: {relative}')
            if target['before'] is None:
                destination.unlink(missing_ok=True)
            else:
                atomic(destination, safe(backup, relative).read_bytes(), target['before']['mode'])
        verify_epoch(root, manifest, 'before')
        record['phase'] = 'rolled_back'
        record['rolled_back_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(journal, record)
        return {'action': action, 'state': 'rolled_back', 'manifest_sha256': manifest_sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'rollback'))
    parser.add_argument('--root', type=Path, default=HERE.parents[3])
    parser.add_argument('--check-only', action='store_true')
    # Test harness only; shell entry points never suppress the fast suites.
    parser.add_argument('--skip-fast-tests', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    print(json.dumps(execute(args.action, args.root, args.check_only, not args.skip_fast_tests), sort_keys=True))


if __name__ == '__main__':
    main()
