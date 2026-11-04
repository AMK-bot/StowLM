"""Freeze public metadata, then explicitly release one immutable sealed run."""
from __future__ import annotations

import argparse
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
import fcntl
import importlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from threading import Event

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from experiments.mvp.pipeline.sealed.common import (
    HERE, ROOT, HarnessError, atomic_json, cpu_environment, exclusive_json,
    object_hash, read_json, resolve, sha256, wait_for_admission)

V3 = ROOT / 'experiments/mvp/corpus/test-v3'
ENVELOPE = ROOT / 'experiments/mvp/pipeline/maps'
GLOBAL_CLAIM = HERE / 'SEALED-CLAIM.json'
COHORT_SIZES = {'tiny15': 15, 'small30': 30, 'envelope24': 24}
ARMS = ['pipeline', 'twostep']
PROTOCOL_HOME = HERE
SOURCE_ROOTS = ('src', 'params', 'experiments/mvp/frontier/engine',
                'experiments/mvp/reader', 'experiments/twostep',
                'experiments/mvp/frontier', 'experiments/mvp/hybrid', 'experiments/mvp/strategy',
                'experiments/mvp/pipeline/contract', 'experiments/mvp/pipeline/binding',
                'experiments/mvp/pipeline/runner', 'experiments/mvp/pipeline/package',
                'experiments/mvp/pipeline/sealed')
IGNORED = {'runs', 'results', 'fixtures', 'measurements', 'runtime-cache', 'cache', 'tmp',
           '__pycache__', '.pytest_cache', '.keys', 'demo-output'}
ENGINE_RESOURCES = ('tiers.toml', 'approved-maps.json', 'native-source-manifest.json',
                    'source-manifest-0.13.0-review-fixes.json',
                    'peel_ladder/copied-manifest.json',
                    'peel_ladder/assignment-frozen-hashes.json')


def is_sealed(path):
    path = resolve(path)
    roots = (V3, ENVELOPE / 'sealed', ROOT / 'experiments/mvp/reader/v3/sealed')
    return any(path.is_relative_to(p.resolve()) for p in roots)


def public_path(path):
    path = resolve(path)
    # These two published manifests are metadata, never input payloads.
    if is_sealed(path) and path != (V3 / 'manifest.json').resolve():
        raise HarnessError(f'Preparation cannot open sealed input: {path}')
    if 'runs' in path.parts:
        raise HarnessError('Preparation cannot use historical run results')
    return path


def source_inventory():
    """Broad source pin, without traversing maps or experiment results."""
    sealed_roots = tuple(p.resolve() for p in
        (V3, ENVELOPE / 'sealed', ROOT / 'experiments/mvp/reader/v3/sealed'))
    paths = set()
    for name in ('pyproject.toml', 'uv.lock'):
        path = public_path(ROOT / name)
        if path.is_file():
            paths.add(path)
    # The measured profile is executable configuration even though its selection
    # receipt lives under fixtures. Pin its exact bytes as well as its semantics.
    profile_selection = public_path(ROOT / 'experiments/mvp/pipeline/runner/fixtures/profile-selection.json')
    if profile_selection.is_file():
        paths.add(profile_selection.resolve())
    for name in ENGINE_RESOURCES:
        path = public_path(ROOT / 'experiments/mvp/frontier/engine' / name)
        if path.is_file():
            paths.add(path)
    for name in SOURCE_ROOTS:
        base = (ROOT / name).resolve()
        if not base.is_dir() or any(base.is_relative_to(root) for root in sealed_roots):
            continue
        # Reader's descendants contain private sealed inputs. Its runtime is
        # flat; never descend into training/evaluation datasets or results.
        flat = name in {'experiments/mvp/reader', 'experiments/mvp/pipeline/sealed',
                        'experiments/mvp/frontier', 'experiments/twostep', 'experiments/mvp/hybrid'}
        # Pipeline JSON files are mutable verification receipts, fixtures and
        # measurements. Actual runtime resources belong in task 2.1's files;
        # this harness pins its protocol separately below.
        allowed = {'.py'} if name.startswith('experiments/mvp/pipeline/') or name in {'experiments/twostep', 'experiments/mvp/hybrid',
                                    'experiments/mvp/strategy', 'experiments/mvp/reader',
                                    'experiments/mvp/frontier'} else {'.py', '.json', '.toml', '.so', '.dylib'}
        if name == 'experiments/mvp/frontier/engine':
            allowed = {'.py', '.toml', '.so', '.dylib'}
        def wanted(filename):
            return (os.path.splitext(filename)[1] in allowed and filename != 'SEALED-CLAIM.json'
                    and not filename.startswith('test_'))
        def descendants():
            for current, directories, filenames in os.walk(base, topdown=True):
                directories[:] = [d for d in directories if d not in IGNORED and
                    not d.startswith(('sealed', 'test-tmp'))]
                yield from (Path(current) / filename for filename in filenames if wanted(filename))
        candidates = (p for p in base.iterdir() if wanted(p.name)) if flat else descendants()
        for path in candidates:
            # base is already resolved and os.walk does not follow directory
            # symlinks. Only a symlink file needs another filesystem traversal.
            if path.is_symlink():
                path = path.resolve()
            if any(path.is_relative_to(root) for root in sealed_roots):
                continue
            if path.is_file():
                paths.add(path)
    return sorted(paths)


def normalize_files(value):
    if isinstance(value, list):
        pairs = [(row['path'], row['sha256']) for row in value]
        if len({p for p, _ in pairs}) != len(pairs):
            raise HarnessError('Repeated manifest file')
    elif isinstance(value, dict):
        pairs = list(value.items())
    else:
        raise HarnessError('Manifest files must be a mapping or list')
    result = {}
    for name, digest in pairs:
        path = public_path(name)
        if (not isinstance(digest, str) or len(digest) != 64 or
                any(c not in '0123456789abcdef' for c in digest)):
            raise HarnessError('Manifest digest must be SHA256')
        if str(path) in result:
            raise HarnessError('Aliased manifest file')
        result[str(path)] = digest
    return result


def verify_pipeline_manifest(path):
    from experiments.mvp.pipeline.sealed.protocol import verify_pipeline_manifest as verify
    return verify(path)


def validate_release_support(config, identity):
    from .runner_seam import support_check
    seam = support_check()
    factory = config.get('reader_factory') or config.get('parser_factory', '')
    if not isinstance(factory, str) or ':' not in factory:
        raise HarnessError('Sealed run requires a frozen reader_factory module:callable')
    module_name, attribute = factory.split(':', 1)
    module = importlib.import_module(module_name)
    if not callable(getattr(module, attribute, None)):
        raise HarnessError('Reader factory is unavailable')
    if getattr(getattr(module, attribute), 'development_only', False):
        raise HarnessError('Development recorded reader cannot be released as the adopted reader')
    source = Path(module.__file__).resolve()
    if str(source) not in identity['pipeline_files']:
        raise HarnessError('Reader factory source is not in the task 2.1 manifest')
    for key in ('source', 'assembly_source', 'core_source'):
        if seam[key] not in identity['pipeline_files']:
            raise HarnessError('Authorized runner seam is not in the task 2.1 manifest: '+seam[key])


def declared_sealed_maps():
    """Read only published metadata, never hash/read a sealed map here."""
    tiny_small = read_json(V3 / 'manifest.json')['files']
    maps = []
    for row in tiny_small:
        rung = row.get('rung', row['path'].split('/')[0])
        if rung not in {'tiny', 'small'}:
            raise HarnessError('Unexpected test-v3 cohort')
        maps.append(dict(path=str((V3 / row['path']).resolve()), sha256=row['sha256'],
                         map_id=f'testv3:{row["path"]}', ship_id=_ship_id(row),
                         cohort=rung + ('15' if rung == 'tiny' else '30')))
    manifest_path = ENVELOPE / 'MANIFEST.json'
    if not manifest_path.is_file():
        raise HarnessError('Task 0.6 sealed envelope manifest has not landed')
    for index, row in enumerate(r for r in read_json(manifest_path)['maps'] if r['split'] == 'sealed'):
        path = resolve(row['path'])
        if not path.is_relative_to((ENVELOPE / 'sealed').resolve()):
            raise HarnessError('Envelope map declaration outside sealed cohort')
        maps.append(dict(path=str(path), sha256=row['sha256'], map_id=f'envelope:{row["seed"]}',
                         cohort='envelope24', cohort_index=index, ship_id=_ship_id(row)))
    if {c: sum(m['cohort'] == c for m in maps) for c in COHORT_SIZES} != COHORT_SIZES:
        raise HarnessError('Sealed map declarations must be 15 tiny, 30 small and 24 envelope')
    if len({m['path'] for m in maps}) != 69 or len({m['sha256'] for m in maps}) != 69:
        raise HarnessError('Repeated sealed map declaration')
    return maps


def _ship_id(row):
    """Use public metadata only, never inspect a sealed payload for its ship."""
    import re
    for key in ('ship_id', 'vessel_id', 'ship', 'vessel', 'path'):
        match = re.search(r'feeder_(500|800|1700)(?!\d)', str(row.get(key, '')))
        if match:
            return match.group(0)
    raise HarnessError('Published map declaration must identify its feeder ship')


def development_maps():
    dev = ENVELOPE / 'dev.txt'
    sets = [dev] if dev.is_file() else [ROOT / 'experiments/mvp/frontier/lab/sets' / name
                                      for name in ('quick_tiny.txt', 'quick_small.txt')]
    maps = []
    for set_path in sets:
        for line in public_path(set_path).read_text().splitlines():
            line = line.split('#')[0].strip()
            if not line:
                continue
            path = public_path(line)
            cohort = ('envelope24' if set_path == dev else
                      'tiny15' if 'tiny' in set_path.name else 'small30')
            index = sum(m['cohort'] == cohort for m in maps)
            maps.append(dict(path=str(path), sha256=sha256(path), map_id=f'dev:{path.relative_to(ROOT)}',
                             cohort=cohort, cohort_index=index, ship_id=_ship_id({'path': str(path)})))
    return maps


def validate_config(config, prereg):
    if config.get('mode') not in {'sealed', 'dry-run'}:
        raise HarnessError('mode must be sealed or dry-run')
    if config['mode']=='sealed':
        config.setdefault('required_enabled_cores',18)
        if config['required_enabled_cores'] != 18:
            raise HarnessError('Final sealed release requires18enabled cores and an idle GPU/fleet')
    if 'dry_sample_map_ids' in config and config['mode'] != 'dry-run':
        raise HarnessError('A sealed run cannot select a development sample')
    registered_arms = prereg.get('required_arms', [a for a in prereg.get('arms', {})
                                               if a not in {'instruction', 'twostep_300'}])
    if config.get('arms', ARMS) != ARMS or registered_arms != ARMS:
        raise HarnessError('Required arms are pipeline and twostep, in that order')
    from experiments.mvp.pipeline.sealed.budget import DEFAULT_WORKERS, MAX_WORKERS
    workers = config.get('workers', DEFAULT_WORKERS)
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise HarnessError(f'workers must be an integer from 1 to {MAX_WORKERS}')
    if config.get('profile', 'standard') != 'standard':
        raise HarnessError('The pipeline arm must use standard profile')
    if not prereg.get('metrics') or not prereg.get('instruction_selection'):
        raise HarnessError('Metrics and instruction selection must be preregistered')
    budget = config.get('twostep_budget_s', 30)
    if type(budget) not in (int, float) or not math.isfinite(budget) or not 0 < budget <= 30:
        raise HarnessError('two-step budget must be positive and at most 30 seconds')
    config.update(workers=workers, arms=ARMS, profile='standard', twostep_budget_s=budget)
    config.setdefault('twostep_s', budget / 2)
    config.setdefault('twostep_finalize_s', budget / 2)
    if any(type(config[k]) not in (int, float) or not math.isfinite(config[k]) or config[k] <= 0
           for k in ('twostep_s', 'twostep_finalize_s')) or config['twostep_s'] + config['twostep_finalize_s'] != budget:
        raise HarnessError('Two-step stage budgets must add to the frozen total')


def freeze(config, out_dir):
    config = dict(config) if isinstance(config, dict) else read_json(public_path(config))
    prereg_path = public_path(config.get('preregistration', HERE / 'preregistration.json'))
    prereg = read_json(prereg_path)
    validate_config(config, prereg)
    out_dir = resolve(out_dir)
    if not out_dir.is_relative_to(HERE) or out_dir == HERE:
        raise HarnessError('Outputs must stay in a child directory of sealed/')
    if out_dir.exists() and any(out_dir.iterdir()):
        raise HarnessError('Freeze destination must be empty')
    if config['mode'] == 'sealed':
        if config.get('stubbed') or config.get('fixture_surrogate'):
            raise HarnessError('A sealed run cannot use stubs')
        if prereg.get('amendment', {}).get('sha256') != sha256(PROTOCOL_HOME/'AMENDMENT.md'):
            raise HarnessError('Sealed registration must pin the adopted amendment')
        pipeline_manifest, pipeline_files = verify_pipeline_manifest(config.get('pipeline_manifest', ''))
        from experiments.mvp.pipeline.sealed.budget import measured_budget
        config['runtime_budget'] = measured_budget(config)
        from experiments.mvp.pipeline.sealed.calibration import verify_calibration
        config['cap_calibration'] = verify_calibration(config.get('cap_calibration_receipt'))
        maps = declared_sealed_maps()
        if 'maps' in config and config['maps'] != maps:
            raise HarnessError('Cannot substitute sealed maps')
    else:
        pipeline_files = {}
        maps = config.get('maps') or development_maps()
        for row in maps:
            path = public_path(row['path'])
            if sha256(path) != row['sha256']:
                raise HarnessError('Development map hash mismatch')
    if not maps or len({m['map_id'] for m in maps}) != len(maps):
        raise HarnessError('Map ids must be nonempty and unique')
    config['maps'] = maps
    report_maps(config)  # Validate an optional development sample before freeze.
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(out_dir / 'config.json', config)
    atomic_json(out_dir / 'preregistration.json', prereg)
    metadata = {str(prereg_path): sha256(prereg_path), str(HERE / 'PREREG.md'): sha256(HERE / 'PREREG.md')}
    for name in ('AMENDMENT.md', 'AMENDMENT.sha256'):
        path = PROTOCOL_HOME / name
        metadata[str(path)] = sha256(path)
    if (PROTOCOL_HOME/'AMENDMENT.sha256').read_text().split()[0] != sha256(PROTOCOL_HOME/'AMENDMENT.md'):
        raise HarnessError('Amendment hash pin differs')
    if config['mode'] == 'sealed':
        for path in (V3 / 'manifest.json', ENVELOPE / 'MANIFEST.json', resolve(config['pipeline_manifest'])):
            metadata[str(path)] = sha256(path)
        path = public_path(config['runtime_measurement'])
        metadata[str(path)] = sha256(path)
        path = public_path(config['cap_calibration_receipt'])
        metadata[str(path)] = sha256(path)
        for name, digest in config['cap_calibration']['files'].items():
            metadata[name] = digest
    identity = dict(schema_version=1, mode=config['mode'],
        config_sha256=sha256(out_dir / 'config.json'), preregistration_sha256=sha256(out_dir / 'preregistration.json'),
        metadata=metadata,
        code={} if config['mode']=='sealed' else {str(p): sha256(p) for p in source_inventory()},
        pipeline_files=pipeline_files)
    if config['mode'] == 'sealed':
        from experiments.mvp.pipeline.sealed.budget import standard_profile_hash
        identity['standard_profile_sha256'] = standard_profile_hash()
        identity.update(manifest_sha256=sha256(resolve(config['pipeline_manifest'])),
                        tree_digest=pipeline_manifest['tree_sha256'])
    identity['run_id'] = object_hash(identity)
    atomic_json(out_dir / 'freeze.json', identity)
    return identity


seal = freeze


def verify_freeze(out_dir):
    out_dir = resolve(out_dir)
    identity = read_json(out_dir / 'freeze.json')
    claimed = identity['run_id']
    if object_hash({k: v for k, v in identity.items() if k != 'run_id'}) != claimed:
        raise HarnessError('Freeze identity changed')
    for name in ('config', 'preregistration'):
        if sha256(out_dir / f'{name}.json') != identity[f'{name}_sha256']:
            raise HarnessError(f'Frozen {name} changed')
    if identity['mode'] != 'sealed' and {str(p) for p in source_inventory()} != set(identity['code']):
        raise HarnessError('Frozen runtime source inventory changed')
    for field in (('metadata',) if identity['mode']=='sealed' else ('metadata', 'code', 'pipeline_files')):
        for path, expected in identity[field].items():
            if not Path(path).is_file() or sha256(path) != expected:
                raise HarnessError(f'Dirty frozen {field}: {path}')
    if identity['mode'] == 'sealed':
        config = read_json(out_dir/'config.json')
        manifest, files = verify_pipeline_manifest(config['pipeline_manifest'])
        if (sha256(resolve(config['pipeline_manifest'])) != identity.get('manifest_sha256')
                or manifest.get('tree_sha256') != identity.get('tree_digest')
                or files != identity['pipeline_files']):
            raise HarnessError('Authoritative frozen pipeline identity changed')
        from experiments.mvp.pipeline.sealed.budget import standard_profile_hash
        if standard_profile_hash() != identity.get('standard_profile_sha256'):
            raise HarnessError('Frozen standard profile changed')
    return identity


def report_maps(config):
    """Keep the full instruction draw, but execute a named development sample."""
    maps = config['maps']
    if 'dry_sample_map_ids' not in config:
        return maps
    if config.get('mode') != 'dry-run':
        raise HarnessError('A sealed run cannot select a development sample')
    selected = config['dry_sample_map_ids']
    if (not isinstance(selected, list) or not selected or any(not isinstance(name, str) for name in selected)
            or len(set(selected)) != len(selected)):
        raise HarnessError('Development sample map IDs must be a nonempty unique list')
    declared = {item['map_id'] for item in maps}
    if not set(selected) <= declared:
        raise HarnessError('Development sample contains an unknown map ID')
    wanted = set(selected)
    return [item for item in maps if item['map_id'] in wanted]


def jobs_for(config, out_dir):
    for index, item in enumerate(report_maps(config)):
        arms = ARMS + (['instruction', 'twostep_300'] if item['cohort'] == 'envelope24' else [])
        # Rotate within each map; never finish all maps of one arm first.
        arms = arms[index % len(arms):] + arms[:index % len(arms)]
        for arm in arms:
            job = dict(item, map_path=item['path'], map_sha256=item['sha256'], arm=arm,
                       profile='standard', mode=config['mode'], seed=0,
                       twostep_budget_s=config['twostep_budget_s'],
                       twostep_s=config['twostep_s'],
                       twostep_finalize_s=300 if arm == 'twostep_300' else config['twostep_finalize_s'],
                       fixture_surrogate=config.get('fixture_surrogate', False),
                       instruction_allocation_sha256=config.get('instruction_allocation_sha256'),
                       open_log=str(out_dir / 'opened-maps.jsonl'), run_dir=str(out_dir))
            if arm != 'pipeline':
                job.pop('calibration_rules', None)
            job['wall_timeout_s'] = config.get('arm_deadlines_s', {}).get(arm, config.get('child_timeout_s', 1200))
            yield job


class ChildProcessFailure(HarnessError):
    def __init__(self, message, metrics):
        self.metrics = metrics
        super().__init__(message)


def _stop_owned_group(child, *, grace_s=.25):
    """Bound cleanup of this Popen's new session, including grandchildren.

    A denied signal or a macOS zombie must never turn a deadline into a wait
    without an end. Unconfirmed cleanup remains explicit for the controller.
    """
    if not getattr(child, '_sealed_owns_session', False):
        raise ValueError('Refusing to signal a process group not created by this driver')
    pgid = child.pid  # start_new_session=True creates this exact owned group.
    errors = []
    def alive():
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError as exc:
            errors.append(f'probe: {type(exc).__name__}: {exc}')
            return True
    def send(signum):
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            errors.append(f'{signal.Signals(signum).name}: {type(exc).__name__}: {exc}')
    def settle():
        end = time.monotonic()+grace_s
        try:
            child.wait(timeout=grace_s)
        except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
            pass
        while time.monotonic() < end:
            if not alive():
                return False
            time.sleep(min(.01, max(0., end-time.monotonic())))
        return alive()
    remains = alive()
    if remains:
        send(signal.SIGTERM)
        remains = settle()
    if remains:
        send(signal.SIGKILL)
        remains = settle()
    # Reap only the exact direct child, with a nonblocking wait.
    try:
        child.wait(timeout=0)
    except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
        pass
    return dict(cleanup_failed=remains, cleanup_errors=list(dict.fromkeys(errors)))


def _start_watchdog(child, seconds):
    """A timer owns only this recorded new-session group, never another PID."""
    if not getattr(child,'_sealed_owns_session',False):
        raise ValueError('Watchdog requires this driver\'s recorded owned session')
    state = dict(fired=False, finished=False, errors=[])
    lock = threading.Lock()
    def expire():
        with lock:
            if state['finished']:
                return
            try:
                if child.poll() is not None:
                    return
            except (ProcessLookupError, PermissionError) as error:
                state['errors'].append(f'poll: {type(error).__name__}: {error}')
            state.update(fired=True, fired_monotonic=time.monotonic())
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                state['errors'].append(f'SIGKILL: {type(error).__name__}: {error}')
    timer = threading.Timer(max(0., seconds), expire)
    timer.daemon = True
    try:
        timer.start()
    except BaseException:
        with lock:
            state['finished'] = True
            timer.cancel()
        if timer.ident is not None:
            try:
                timer.join(timeout=.1)
            except RuntimeError:
                pass  # Interrupted Thread.start may precede its started flag.
        raise
    return timer, state, lock


def _owned_process(command, out_dir, *, timeout_s, audit=False, observe=None):
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('Child wall timeout must be finite and positive')
    prefix = 'audit' if audit else 'child'
    stem = 'audit.' if audit else ''
    started = time.monotonic()
    metrics = {prefix+'_timeout_s': timeout_s, prefix+'_timed_out': False}
    if not audit:
        metrics['timeout_s'] = timeout_s
    error = None
    with (out_dir/(stem+'stdout.log')).open('wb') as stdout, (out_dir/(stem+'stderr.log')).open('wb') as stderr:
        try:
            child = subprocess.Popen(command, cwd=ROOT, env=cpu_environment(), stdout=stdout,
                                     stderr=stderr, start_new_session=True)
        except Exception as exc:
            metrics.update({prefix+'_elapsed_s': time.monotonic()-started,
                            prefix+'_cleanup_failed': False, prefix+'_cleanup_errors': []})
            atomic_json(out_dir/(stem+'process.json'), metrics)
            raise ChildProcessFailure(f'{prefix} could not start: {type(exc).__name__}: {exc}', metrics) from exc
        child._sealed_owns_session = True
        metrics.update({prefix+'_pid': child.pid, prefix+'_pgid': child.pid})
        watchdog = None
        timer_state = dict(fired=False,finished=False,errors=[])
        timer_lock = threading.Lock()
        try:
            watchdog, timer_state, timer_lock = _start_watchdog(
                child, timeout_s-(time.monotonic()-started))
            atomic_json(out_dir/(stem+'pid.json'), dict(pid=child.pid, pgid=child.pid,
                start_new_session=True, started=time.time(), timeout_s=timeout_s))
            if observe is None:
                child.wait(timeout=max(.000001, timeout_s-(time.monotonic()-started)))
            else:
                while True:
                    elapsed = time.monotonic()-started
                    if elapsed >= timeout_s:
                        raise subprocess.TimeoutExpired(command, timeout_s)
                    observe(elapsed, started, child.pid)
                    remaining = timeout_s-(time.monotonic()-started)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout_s)
                    try:
                        child.wait(timeout=min(.05, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
        except BaseException as exc:
            error = exc
            metrics[prefix+'_timed_out'] = isinstance(exc, subprocess.TimeoutExpired)
        finally:
            finished_elapsed = time.monotonic()-started
            # Serialize cancellation with the callback so a completed child
            # can never leave a delayed timer signalling a later reused PID.
            with timer_lock:
                timer_state['finished'] = True
                if watchdog is not None:
                    watchdog.cancel()
            if watchdog is not None:
                watchdog.join(timeout=.1)
            expired = timer_state['fired'] or finished_elapsed >= timeout_s
            if error is None and expired:
                error = subprocess.TimeoutExpired(command, timeout_s)
            metrics[prefix+'_timed_out'] = isinstance(error, subprocess.TimeoutExpired)
            metrics.update({prefix+'_watchdog_fired': timer_state['fired'],
                            prefix+'_watchdog_errors': list(timer_state['errors'])})
            cleanup = _stop_owned_group(child)
            metrics.update({prefix+'_'+key: value for key, value in cleanup.items()})
            metrics.update({prefix+'_elapsed_s': time.monotonic()-started,
                            prefix+'_returncode': child.returncode})
            atomic_json(out_dir/(stem+'process.json'), metrics)
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        reason = (f'{prefix} hard wall deadline exceeded ({timeout_s:g}s)' if metrics[prefix+'_timed_out']
                  else f'{prefix} process failed: {type(error).__name__}: {error}')
        if metrics[prefix+'_cleanup_failed']:
            reason += '; cleanup_failed: owned process group could not be confirmed stopped'
        raise ChildProcessFailure(reason, metrics) from error
    if child.returncode or metrics[prefix+'_cleanup_failed']:
        raise ChildProcessFailure(f'{prefix} process failed: exit={child.returncode}; '
            f'cleanup_failed={metrics[prefix+"_cleanup_failed"]}', metrics)
    return metrics


def _execution_metrics(out_dir):
    metrics = {}
    for name in ('process.json', 'audit.process.json'):
        try:
            metrics.update(read_json(out_dir/name))
        except (OSError, ValueError):
            pass
    return metrics


def _observe_incumbents(job, out_dir, elapsed_s, deadline_s, observed, *, parent_started=None,
                        child_pid=None, cursor=None):
    """Only parent-observed, complete checker publications inside the wall."""
    if not 0 <= elapsed_s <= deadline_s:
        return
    def received():
        return time.monotonic()-parent_started if parent_started is not None else elapsed_s
    try:
        if cursor is None:
            lines = (out_dir/'pipeline/events.jsonl').read_text().splitlines()
        else:
            with (out_dir/'pipeline/events.jsonl').open('rb') as stream:
                if os.fstat(stream.fileno()).st_size < cursor.get('offset',0):
                    cursor.update(offset=0,pending=b'')
                stream.seek(cursor.get('offset',0))
                chunk = cursor.get('pending',b'')+stream.read()
                cursor['offset'] = stream.tell()
            complete = chunk.split(b'\n')
            cursor['pending'] = complete.pop()
            lines = []
            for line in complete:
                try:
                    lines.append(line.decode('utf-8'))
                except UnicodeError:
                    continue
    except (OSError, UnicodeError):
        lines = []
    for line in lines:
        try:
            event = json.loads(line)
            data = event.get('data', {})
            plan = data.get('plan')
            lifts = data.get('lifts')
            if (event.get('stage') != 'engine' or event.get('kind') != 'incumbent'
                    or data.get('checked') is not True or not isinstance(plan, dict)
                    or data.get('plan_sha256') != object_hash(plan)
                    or type(lifts) not in (int, float) or not math.isfinite(lifts) or lifts < 0):
                continue
            if job['arm'] == 'instruction' and (data.get('confirmation_source') != 'sealed_expected_match'
                    or (data.get('confirmation') or {}).get('source') != 'sealed_expected_match'):
                continue
            key = data['plan_sha256']
            published = received()
            if not 0 <= published <= deadline_s:
                continue
            observed.setdefault(key, dict(plan=plan, lifts=lifts,
                published_wall_s=published, publication_sha256=object_hash(event),
                validation_source='runner_checked_incumbent'))
        except (ValueError, TypeError, AttributeError):
            # A concurrent final JSON line can be incomplete until the next poll.
            continue
    try:
        if cursor is not None:
            stat = (out_dir/'row.json').stat()
            signature = (stat.st_size,stat.st_mtime_ns)
            if cursor.get('row_signature') == signature:
                return
        row = read_json(out_dir/'row.json')
        if cursor is not None:
            cursor['row_signature'] = signature
        plan = row.get('plan')
        lifts = row.get('weighted_lifts')
        if (all(row.get(k) == job.get(j) for k,j in (
                ('map_id','map_id'), ('arm','arm'), ('map_sha256','sha256')))
                and row.get('status') == 'checked_plan' and row.get('passed') is True
                and all(row.get(key) is True for key in (
                    'full_checker_pass','complete','mask_ok','replay_legal','all_loaded'))
                and row.get('level') in (0,'L0')
                and (job['arm'] != 'instruction' or row.get('instruction_recheck') is True)
                and row.get('planning_pid') == child_pid and isinstance(plan,dict)
                and row.get('plan_sha256') == object_hash(plan)
                and type(lifts) in (int,float) and math.isfinite(lifts) and lifts >= 0):
            published = received()
            if 0 <= published <= deadline_s:
                observed.setdefault(row['plan_sha256'],dict(plan=plan,lifts=lifts,
                    published_wall_s=published,publication_sha256=object_hash(row),
                    validation_source='worker_original_instance_full_audit'))
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def _deadline_result(job, out_dir, metrics, observed):
    accepted = [item for item in observed.values()
                if 0 <= item['published_wall_s'] <= metrics['child_timeout_s']]
    best = min(accepted, key=lambda item: item['lifts']) if accepted else None
    message = ('Hard wall deadline reached; retaining the published checked incumbent for fresh audit.'
               if best else 'no plan within the time budget')
    row = _failure_row(job, message, status='deadline_exceeded', dispatched=True)
    row.pop('error', None)
    row.update(metrics, harness_error=False, deadline_exceeded=True,
        deadline_with_incumbent=best is not None, message=message, reason=message,
        proved_infeasible=False, refusal_kind=None, plan=best['plan'] if best else None,
        planning_pid=metrics['child_pid'], wall_s=metrics['child_elapsed_s'],
        hard_wall_s=metrics['child_timeout_s'], expected_rule=job.get('expected_rule'),
        first_checked_wall_s=min((item['published_wall_s'] for item in accepted), default=None),
        deadline_publication=best, instruction_type=job.get('instruction_type'))
    # The original worker's bytes, if any, remain unchanged. This separate
    # committed recovery target is hashed before the one ordinary audit phase.
    atomic_json(out_dir/'deadline-row.json', row)
    return row


def run_child(job, out_dir, timeout_s=1200, *, prepare=False):
    request = out_dir / 'job.json'
    atomic_json(request, job)
    command = [sys.executable, '-m', 'experiments.mvp.pipeline.sealed.driver', '_prepare' if prepare else '_worker',
               '--request', str(request), '--out-dir', str(out_dir)]
    observed = {}
    cursor = {}
    observer = None if prepare else lambda elapsed, started, pid: _observe_incumbents(
        job, out_dir, elapsed, timeout_s, observed, parent_started=started, child_pid=pid,cursor=cursor)
    try:
        metrics = _owned_process(command, out_dir, timeout_s=timeout_s, observe=observer)
    except ChildProcessFailure as error:
        if (not prepare and error.metrics.get('child_timed_out') is True
                and not error.metrics.get('child_cleanup_failed')):
            return _deadline_result(job, out_dir, error.metrics, observed)
        raise
    if not (out_dir / 'row.json').is_file():
        raise ChildProcessFailure(f'Worker did not write a row: {out_dir}', metrics)
    row = read_json(out_dir / 'row.json')
    return row if prepare else dict(row, **metrics)


def _audit_child(job, out_dir, timeout_s=600):
    target = out_dir/('deadline-row.json' if job.get('deadline_recovery') else 'row.json')
    request = dict(job, target=str(target), target_sha256=sha256(target))
    if job['mode'] == 'sealed':
        from experiments.mvp.pipeline.sealed.authorization import issue
        identity = read_json(resolve(job['run_dir'])/'freeze.json')
        request = issue(request, phase='audit', run_id=identity['run_id'], job_id=out_dir.name+'-audit')
    atomic_json(out_dir / 'audit-job.json', request)
    command = [sys.executable, '-m', 'experiments.mvp.pipeline.sealed.worker',
               '--request', str(out_dir / 'audit-job.json'),
               '--output', str(out_dir / 'audit-row.json'), '--audit']
    metrics = _owned_process(command, out_dir, timeout_s=timeout_s, audit=True)
    if not (out_dir / 'audit-row.json').is_file():
        raise HarnessError(f'Independent audit process failed: {out_dir}')
    row = read_json(out_dir / 'audit-row.json')
    if row.get('audit_verified') is not True or row.get('audit_fresh_process') is not True:
        raise HarnessError(f'Independent audit did not verify result: {out_dir}')
    raw = read_json(target)
    if (row.get('audited_target_sha256') != sha256(target) or
            row.get('audit_pid') == raw.get('planning_pid') or row.get('audit_pid') != metrics['audit_pid']):
        raise HarnessError(f'Independent audit attestation differs from committed bytes/process: {out_dir}')
    return dict(row, **_execution_metrics(out_dir))


def _unverified(row, error, *, phase, job=None):
    retained = dict(row)
    retained.update(getattr(error, 'metrics', {}))
    if retained.get('wall_s') is None and retained.get('child_elapsed_s') is not None:
        retained['wall_s'] = retained['child_elapsed_s']
    invalid = {}
    for key in ('lifts_current_port', 'lifts_projected_later_port', 'lifts_total',
                'weighted_lifts', 'wall_s', 'reading_wall_s'):
        value = retained.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            invalid[key] = value if type(value) in (int, float) and math.isfinite(value) else repr(value)
            retained[key] = None
    if invalid:
        retained['unverified_metrics'] = invalid
    if job is not None:
        retained.update(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
                        map_sha256=job['sha256'], ship_id=job.get('ship_id'))
    return dict(retained, status='error', passed=False, audit_verified=False,
                audit_fresh_process=False, harness_error=True, reason=f'{phase}: {error}',
                **({'audit_error': str(error)} if phase == 'audit' else {'error': str(error)}))


def audit_child(job, out_dir, timeout_s=None):
    try:
        return _audit_child(job, out_dir) if timeout_s is None else _audit_child(job, out_dir, timeout_s)
    except Exception as error:
        try:
            raw = read_json(out_dir/('deadline-row.json' if job.get('deadline_recovery') else 'row.json'))
        except (OSError, ValueError):
            raw = _failure_row(job, str(error), dispatched=True)
        return dict(_unverified(raw, error, phase='audit', job=job), **_execution_metrics(out_dir))


def _failure_row(job, reason, *, status='error', dispatched=False):
    return dict(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
        ship_id=job.get('ship_id'), harness_error=True,
        map_sha256=job['sha256'], status=status, reason=reason, error=reason,
        dispatched=dispatched, passed=False, full_checker_pass=False,
        audit_verified=False, audit_fresh_process=False, plan=None, level=None,
        wall_s=None, instruction_recheck=None, instruction_type=job.get('instruction_type'),
        lifts_current_port=None, lifts_projected_later_port=None, lifts_total=None,
        weighted_lifts=None, fixture_surrogate=bool(job.get('fixture_surrogate')))


def _fail_run(out_dir, config, identity, rows, reason):
    atomic_json(out_dir/'FAILED.json', dict(t=time.time(), reason=reason, rows=len(rows)))
    available = {(row['map_id'], row['arm']): row for row in rows}
    population = []
    for index, job in enumerate(jobs_for(config, out_dir)):
        row = available.get((job['map_id'], job['arm']))
        target = out_dir/'jobs'/f'{index:04d}-{job["arm"]}'
        if row is None:
            for name in ('result-row.json', 'row.json'):
                try:
                    candidate = read_json(target/name)
                    if all(candidate.get(k) == job.get(j) for k,j in (
                            ('map_id', 'map_id'), ('cohort', 'cohort'), ('arm', 'arm'), ('map_sha256', 'sha256'))):
                        row = (candidate if name == 'result-row.json' else
                               _unverified(candidate, reason, phase='controller', job=job))
                        break
                except (OSError, ValueError, AttributeError):
                    pass
        dispatched = (target/'DISPATCHED.json').exists()
        row = row if row is not None else _failure_row(job, reason,
            status='error' if dispatched else 'not_run', dispatched=dispatched)
        population.append(dict(row, **_execution_metrics(target)))
    atomic_json(out_dir/'rows.json', population)
    from experiments.mvp.pipeline.sealed.report import write_report
    return write_report(population, out_dir, metadata=dict(mode=config['mode'],
        run_id=identity['run_id'], expected_maps=report_maps(config), workers=config['workers'],
        sensitivity_arms=['twostep_300'], amendment_sha256=sha256(PROTOCOL_HOME/'AMENDMENT.md'),
        stubbed=config.get('stubbed', []), complete=False, provisional=True, failure_reason=reason))


def _prepare_maps(config, out_dir, identity, worker, admission_wait):
    envelope_maps = [m for m in config['maps'] if m['cohort'] == 'envelope24']
    if envelope_maps:
        snapshot = (admission_wait(config.get('admission_path')) if admission_wait else
                    wait_for_admission(config.get('admission_path'), workers=config['workers'],
                                       min_cores=config.get('required_enabled_cores')))
        verify_freeze(out_dir)
        prepass = out_dir / 'instruction-prepass'
        prepass.mkdir()
        exclusive_json(prepass / 'DISPATCHED.json', dict(t=time.time(), admission=snapshot))
        if config['mode'] == 'sealed':
            from experiments.mvp.pipeline.sealed.authorization import issue
            envelope_maps = [issue(dict(m, arm='instruction', mode='sealed', run_dir=str(out_dir),
                open_log=str(out_dir/'opened-maps.jsonl')), phase='prepass',
                run_id=identity['run_id'], job_id='prepass-'+str(i)) for i,m in enumerate(envelope_maps)]
        settings = dict(reader_factory=config.get('reader_factory') or config.get('parser_factory'),
                        parser_options=config.get('parser_options', {}),
                        fixture_surrogate=config.get('fixture_surrogate', False), mode=config['mode'])
        try:
            if worker is None:
                prepared = run_child(dict(maps=envelope_maps, open_log=str(out_dir / 'opened-maps.jsonl'), **settings),
                                     prepass, timeout_s=config.get('prepass_timeout_s', 1800), prepare=True)
            else:
                from experiments.mvp.pipeline.sealed.worker import prepare_instructions
                prepared = prepare_instructions(envelope_maps, prepass, str(out_dir / 'opened-maps.jsonl'), **settings)
        except Exception as error:
            # A spent read is never repeated. Baseline/sensitivity trials remain
            # executable even if the serial reader/prepass child crashes.
            verify_freeze(out_dir)
            reason = f'Instruction prepass failed; no retry: {type(error).__name__}: {error}'
            atomic_json(prepass/'FAILED.json', dict(reason=reason, t=time.time()))
            return dict(config, maps=[dict(m, instruction_prepass_error=reason)
                if m['cohort'] == 'envelope24' else m for m in config['maps']])
        if ({m['map_id'] for m in prepared} != {m['map_id'] for m in envelope_maps}
                or len(prepared) != len(envelope_maps)):
            raise HarnessError('Instruction prepass changed declared map population')
        originals = {m['map_id']: m for m in envelope_maps}
        for row in prepared:
            original = originals[row['map_id']]
            if any(row.get(key) != original.get(key) for key in ('path', 'sha256', 'cohort')):
                raise HarnessError('Instruction prepass changed map identity')
            if 'expected_rule' not in row or type(row.get('cohort_index')) is not int:
                raise HarnessError('Instruction prepass must commit rule and rank')
        allocation = {m['map_id']: {k:v for k,v in m.items() if k not in
            {'authorization', 'arm', 'mode', 'run_dir', 'open_log'}} for m in prepared}
        exclusive_json(out_dir / 'instruction-allocation.json', prepared)
        config = dict(config, instruction_allocation_sha256=sha256(out_dir/'instruction-allocation.json'),
                      maps=[allocation.get(m['map_id'], m) for m in config['maps']])

    return config


def run(out_dir, *, release_sealed=False, worker=None, parser=None, admission_wait=None):
    out_dir = resolve(out_dir)
    identity = verify_freeze(out_dir)
    config = read_json(out_dir / 'config.json')
    if config['mode'] == 'sealed' and (not release_sealed or worker is not None or parser is not None):
        raise HarnessError('Sealed run requires explicit release and production subprocess workers')
    if config['mode'] == 'sealed':
        validate_release_support(config, identity)
    if (worker is not None or parser is not None) and not config.get('stubbed'):
        raise HarnessError('Injected dry worker must be explicitly labelled stubbed')
    with (out_dir / 'controller.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HarnessError('Another controller owns this run') from exc
        if (out_dir / 'STARTED.json').exists() or (out_dir / 'COMPLETE.json').exists():
            raise HarnessError('One-run policy refuses completed or incomplete reruns; no resume')
        if config['mode'] == 'sealed':
            exclusive_json(GLOBAL_CLAIM, dict(run_id=identity['run_id'], out_dir=str(out_dir), t=time.time()))
        exclusive_json(out_dir / 'STARTED.json', dict(t=time.time(), run_id=identity['run_id']))

        rows = []
        prepass_error = None
        try:
            config = _prepare_maps(config, out_dir, identity, worker, admission_wait)
        except BaseException as exc:
            prepass_error = 'prepass failed: '+str(exc)
            raise
        finally:
            if prepass_error is not None:
                _fail_run(out_dir, config, identity, rows, prepass_error)

        halt = Event()

        def admit():
            return (admission_wait(config.get('admission_path')) if admission_wait else
                    wait_for_admission(config.get('admission_path'), workers=config['workers'],
                        min_cores=config.get('required_enabled_cores'),stop=halt.is_set))

        def integrity():
            try:
                verify_freeze(out_dir)
            except Exception:
                halt.set()
                raise

        def execute(index_job):
            index, job = index_job
            began = time.monotonic()
            queued_s = 0.
            target = out_dir / 'jobs' / f'{index:04d}-{job["arm"]}'
            raw = None
            phase = 'dispatch'
            try:
                target.mkdir(parents=True)
                if job['arm'] == 'instruction' and job.get('instruction_prepass_error'):
                    row = _failure_row(job, job['instruction_prepass_error'])
                    atomic_json(target/'result-row.json', row)
                    return row
                if halt.is_set():
                    return _failure_row(job, 'Stopped after earlier harness failure', status='not_run')
                snapshot = admit()
                queued_s += (snapshot or {}).get('waited_s',0.)
                integrity()
                if halt.is_set():
                    return _failure_row(job, 'Stopped after earlier harness failure', status='not_run')
                exclusive_json(target / 'DISPATCHED.json', dict(t=time.time(), admission=snapshot, job=job))
                if config['mode'] == 'sealed':
                    from experiments.mvp.pipeline.sealed.authorization import issue
                    job = issue(job, phase='plan', run_id=identity['run_id'], job_id=target.name)
                phase = 'worker'
                raw = worker(job, target, parser=parser) if worker else run_child(
                    job, target, job['wall_timeout_s'])
                if any(raw.get(k) != job.get(j) for k,j in (
                        ('map_id', 'map_id'), ('cohort', 'cohort'), ('arm', 'arm'), ('map_sha256', 'sha256'))):
                    raise HarnessError('Worker result identity differs from registered job')
                if not (target/'row.json').exists():
                    atomic_json(target/'row.json', raw)
                integrity()
                row = dict(raw)
                if worker is None:
                    phase = 'audit'
                    snapshot = admit()
                    queued_s += (snapshot or {}).get('waited_s',0.)
                    if snapshot is None:
                        raise HarnessError('Audit cancelled after earlier harness failure')
                    integrity()
                    exclusive_json(target / 'AUDIT-DISPATCHED.json', dict(t=time.time(), admission=snapshot))
                    audit_request = (dict(job, deadline_recovery=True)
                                     if raw.get('deadline_exceeded') is True else job)
                    row = (audit_child(audit_request, target, timeout_s=config['audit_timeout_s'])
                           if 'audit_timeout_s' in config else audit_child(audit_request, target))
                    integrity()
                row['dispatched'] = True
                if row.get('status') == 'error':
                    row['harness_error'] = True
            except Exception as error:
                # Raw worker bytes stay untouched, even when malformed or unaudited.
                if raw is None:
                    try:
                        raw = read_json(target/'row.json')
                    except (OSError, ValueError):
                        pass
                if not isinstance(raw, dict) or any(raw.get(k) != job.get(j) for k,j in (
                        ('map_id', 'map_id'), ('cohort', 'cohort'), ('arm', 'arm'), ('map_sha256', 'sha256'))):
                    raw = _failure_row(job, str(error))
                row = _unverified(raw, error, phase=phase, job=job)
                row['dispatched'] = (target/'DISPATCHED.json').exists()
            row.update(_execution_metrics(target))
            row.update(driver_elapsed_s=time.monotonic()-began,driver_admission_wait_s=queued_s)
            if row.get('child_cleanup_failed') or row.get('audit_cleanup_failed'):
                halt.set()  # No more CPU trials if owned descendants may still be alive.
            if row.get('wall_s') is None and row.get('child_elapsed_s') is not None:
                row['wall_s'] = row['child_elapsed_s']
            atomic_json(target/'result-row.json', row)
            return row

        from experiments.mvp.pipeline.sealed.report import write_report
        all_jobs = list(jobs_for(config, out_dir))
        rows = []
        failure = None
        try:
            results = {}
            with ThreadPoolExecutor(max_workers=config['workers']) as pool:
                futures = {pool.submit(execute, pair): pair for pair in enumerate(all_jobs)}
                try:
                    for future in as_completed(futures):
                        index, job = futures[future]
                        try:
                            results[index] = future.result()
                        except CancelledError:
                            results[index] = _failure_row(job,
                                'Cancelled after earlier harness failure', status='not_run')
                        except Exception as error:
                            halt.set()
                            target = out_dir/'jobs'/f'{index:04d}-{job["arm"]}'
                            results[index] = _failure_row(job, str(error),
                                dispatched=(target/'DISPATCHED.json').exists())
                        rows = [results[i] for i in sorted(results)]
                        atomic_json(out_dir/'rows.json', rows)
                        if halt.is_set():
                            for pending in futures:
                                pending.cancel()
                except BaseException as error:
                    halt.set()
                    for pending in futures:
                        pending.cancel()
                    if isinstance(error, KeyboardInterrupt):
                        atomic_json(out_dir/'INTERRUPTED.json', dict(t=time.time(), reason='Controller interrupted'))
                    raise
        except BaseException as exc:
            failure = str(exc)
            raise
        finally:
            if failure is not None or any(row.get('status') in {'error', 'not_run'} for row in rows):
                report = _fail_run(out_dir, config, identity, rows,
                    failure or 'Errors retained; remaining trials completed without retry')
            else:
                metadata = dict(mode=config['mode'], run_id=identity['run_id'], workers=config['workers'],
                    expected_maps=report_maps(config), stubbed=config.get('stubbed', []),
                    sensitivity_arms=['twostep_300'], amendment_sha256=sha256(PROTOCOL_HOME/'AMENDMENT.md'),
                    budget=dict(profile='standard', twostep_budget_s=config['twostep_budget_s']),
                    complete=True, elapsed_s=time.time() - read_json(out_dir / 'STARTED.json')['t'])
                report = write_report(rows, out_dir, metadata=metadata)
                exclusive_json(out_dir / 'COMPLETE.json', dict(t=time.time(), rows=len(rows), run_id=identity['run_id']))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('freeze', 'dry-run'):
        command = sub.add_parser(name)
        command.add_argument('--config', required=True)
        command.add_argument('--out-dir', required=True)
    command = sub.add_parser('run')
    command.add_argument('--out-dir', required=True)
    command.add_argument('--release-sealed', action='store_true')
    for name in ('_worker', '_prepare'):
        command = sub.add_parser(name)
        command.add_argument('--request', required=True)
        command.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    try:
        if args.command == '_prepare':
            from experiments.mvp.pipeline.sealed.worker import prepare_instructions
            request = read_json(args.request)
            rows = prepare_instructions(request['maps'], Path(args.out_dir), request['open_log'],
                **{k: request[k] for k in ('reader_factory', 'parser_options', 'fixture_surrogate', 'mode') if k in request})
            atomic_json(Path(args.out_dir) / 'row.json', rows)
        elif args.command == '_worker':
            from experiments.mvp.pipeline.sealed.native_runtime import configure
            configure()
            from experiments.mvp.pipeline.sealed.worker import run_job
            request = read_json(args.request)
            row = run_job(request, Path(args.out_dir))
            atomic_json(Path(args.out_dir) / 'row.json', row)
        elif args.command == 'freeze':
            freeze(args.config, args.out_dir)
        elif args.command == 'dry-run':
            config = read_json(public_path(args.config))
            if config.get('mode') != 'dry-run':
                raise HarnessError('dry-run command requires dry-run mode')
            freeze(config, args.out_dir)
            run(args.out_dir)
        else:
            run(args.out_dir, release_sealed=args.release_sealed)
    except (HarnessError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'sealed harness: {exc}\n')


if __name__ == '__main__':
    main()
