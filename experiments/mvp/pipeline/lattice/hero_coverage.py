"""Six-worker, drainable hero coverage. Existing campaign controls are untouched."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from . import batch
from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
DIRECTORY = HERE/'hero-coverage'
QUIET = batch.ROOT/'experiments/fleet/quiet.flag'
MAX_WORKERS = 6


def utc():
    return datetime.now(timezone.utc).isoformat()


class AdmissionDeferred(Exception):
    pass


def blocked(directory, quiet=QUIET):
    if (Path(directory)/'STOP').exists():
        return 'stop'
    if Path(quiet).exists():
        return 'quiet'
    return None


def first_admission(directory, *, quiet=QUIET, reading=batch.capacity_reading,
                    sleep=time.sleep, clock=time.monotonic):
    """A queued worker cannot pass its first admission after quiet/STOP appears."""
    began = clock()
    while True:
        if reason := blocked(directory, quiet):
            raise AdmissionDeferred(reason)
        value = reading()
        if value is not None:
            if reason := blocked(directory, quiet):
                raise AdmissionDeferred(reason)
            return dict(value, waited_s=clock()-began)
        sleep(2)


def profile_matches(manifest):
    from experiments.mvp.pipeline.runner.profiles import get_profile
    profile = get_profile('standard')
    snapshot = {k: asdict(v) if k == 'config' else v for k, v in profile.items()}
    if (profile['budget_s'], profile['finalize_s'], profile['engine_timeout_s']) != (300., 100., 1800.):
        raise ValueError('Coverage requires standard300/100 and independent engine hard wall1800')
    fields = ('budget_s', 'finalize_s', 'seed', 'config', 'name', 'engine_timeout_s')
    if any(snapshot.get(key) != manifest['profile'].get(key) for key in fields) or sha256_file(manifest['profile_selection_path']) != manifest['profile_selection_sha256']:
        raise ValueError('Pinned coverage profile changed')


def validate_job_identity(manifest, spec):
    from stowage.io import load_instance
    from .store import entry_key, canonical_rule_set
    instance = load_instance(manifest['instance_path']).model_dump(mode='json')
    if (sha256_json(instance) != manifest['instance_sha256'] or
            sha256_json(manifest['base_plan']) != manifest['base_plan_sha256'] or
            manifest['base_plan'].get('instance_id') != instance.get('id') or
            entry_key(manifest['instance_sha256'], canonical_rule_set(spec['rules'])) != spec['key']):
        raise ValueError('Coverage job instance, baseline or canonical key changed')
    base_path = manifest.get('base_fixture') or manifest.get('base_fixture_path')
    if base_path:
        base_path = Path(base_path)
        base_path = base_path if base_path.is_absolute() else batch.ROOT/base_path
        if sha256_file(base_path) != manifest['base_fixture_sha256']:
            raise ValueError('Frozen baseline fixture changed')
    identity = manifest.get('source_identity') or {}
    for field, root in (('binding_source_files', HERE.parent/'binding'),
                        ('reader_source_files', batch.ROOT), ('gate_source_files', batch.ROOT)):
        for name, digest in identity.get(field, {}).items():
            path = (root/name).resolve()
            if not path.is_relative_to(root.resolve()) or sha256_file(path) != digest:
                raise ValueError('Coverage source identity changed: '+name)


def capture_source_identity():
    from .hero_coverage_queue import source_identity
    return source_identity()


def normalize_proved_refusal(manifest, spec, start_identity, end_identity):
    """Repair only the local batch's classification of an explicit hatch proof."""
    from .store import canonical_rule_set, entry_key, save_entry
    from stowage.io import load_instance
    target = Path(manifest['run_dir'])/(spec['key']+'.json')
    if not target.exists():
        return None
    raw = target.read_bytes()
    row = json.loads(raw)
    evidence = row.get('binding_evidence') or {}
    rules = canonical_rule_set(spec['rules'])
    if (row.get('status') != 'budget_failed' or evidence.get('status') != 'constraint_unavailable'
            or evidence.get('refusal_kind') != 'proved_full_load' or row.get('plan')
            or row.get('original_binding_plan') or len(rules) != 1
            or rules[0]['constraint']['type'] != 'one_port_per_hatch'):
        return None
    if (start_identity != end_identity or capture_source_identity() != end_identity
            or row.get('binding_source_sha256') != end_identity['binding_source_sha256']
            or row.get('binding_source_after_sha256') != end_identity['binding_source_sha256']
            or row.get('engine_version') != end_identity['engine_version']
            or row.get('engine_manifest_sha256') != end_identity['engine_manifest_sha256']):
        raise ValueError('Hatch refusal computation source or engine identity changed')
    instance = load_instance(manifest['instance_path']).model_dump(mode='json')
    fields = ('budget_s', 'finalize_s', 'seed', 'config', 'name', 'engine_timeout_s')
    if (sha256_json(instance) != manifest['instance_sha256'] or row.get('instance_sha256') != manifest['instance_sha256']
            or row.get('key') != spec['key'] or entry_key(manifest['instance_sha256'], rules) != spec['key']
            or canonical_rule_set(row['canonical_rules']) != rules
            or any((row.get('profile') or {}).get(k) != manifest['profile'].get(k) for k in fields)):
        raise ValueError('Hatch refusal instance, canonical rule or standard profile differs')
    candidate = dict(row, instance=instance)
    from .hero_coverage_publish import _refusal_evidence
    try:
        audit = _refusal_evidence(candidate)
    except ValueError as error:
        batch.write_json(target.parent/'normalization-attempts'/(spec['key']+'.json'),
            dict(status='not_proved', reason=str(error), original_status=row['status'], checked_utc=utc()))
        return None
    proof = audit.get('necessary_proof') or {}
    if proof.get('proven_impossible') is not True:
        return None
    if capture_source_identity() != end_identity:
        raise ValueError('Source changed during fresh necessary hatch refusal proof')
    digest = sha256_file(target)
    archive = target.parent/'normalization-archive'/(spec['key']+'.'+digest+'.json')
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        with archive.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    elif archive.read_bytes() != raw:
        raise ValueError('Hatch refusal raw archive differs')
    normalization = dict(original_status='budget_failed', original_run_path=str(archive),
        original_run_sha256=digest, fresh_necessary_proof=audit, normalized_utc=utc(),
        planner_repeated=False, native_checker_called=False)
    candidate.update(status='proved_refusal', proof=dict(reason=evidence.get('reason'), arithmetic=proof,
        scope='Fresh necessary hatch capacity/allocation proof, not search exhaustion'),
        refusal_normalization=normalization)
    save_entry(candidate, store_dir=manifest['store_dir'])
    batch.write_json(target, candidate)
    return normalization


def run_worker(job_path):
    job_path = Path(job_path)
    job = json.loads(job_path.read_text())
    manifest_path = Path(job['manifest_path'])
    directory = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    result_path = job_path.with_name('result.json')
    result = dict(id=job['spec']['id'], key=job['spec']['key'], attempt=job['attempt'],
                  process_pid=os.getpid(), started_utc=utc())
    original_admit, original_refresh = batch.admit, batch.refresh_progress
    try:
        if sha256_file(manifest_path) != job['manifest_sha256']:
            raise ValueError('Pinned coverage manifest changed')
        profile_matches(manifest)
        validate_job_identity(manifest, job['spec'])
        start_identity = capture_source_identity()
        if start_identity['source_identity_sha256'] != manifest['source_identity']['source_identity_sha256']:
            raise ValueError('Coverage source identity differs from prepared manifest')
        batch.write_json(job_path.with_name('start-identity.json'), start_identity)
        result['coverage_start_identity'] = start_identity
        admission = first_admission(directory, quiet=job['quiet_path'],
            reading=lambda: batch.capacity_reading(manifest['state_path']))
        first = True

        def guarded_admit(state_path=batch.STATE, *, poll_s=2., sleep=time.sleep):
            nonlocal first
            if first:
                first = False
                return dict(admission)
            return original_admit(state_path, poll_s=poll_s, sleep=sleep)

        batch.admit = guarded_admit
        batch.refresh_progress = lambda run_dir: update_report(directory)
        compute_job = {k: manifest[k] for k in ('instance_path', 'base_plan', 'run_dir', 'store_dir', 'fixtures_dir', 'state_path')}
        # No campaign_id: main campaign-control.json must never govern this queue.
        compute_job['spec'] = job['spec']
        computed = batch.compute(compute_job)
        end_identity = capture_source_identity()
        batch.write_json(job_path.with_name('end-identity.json'), end_identity)
        result['coverage_end_identity'] = end_identity
        if computed.get('key') != job['spec']['key']:
            raise ValueError('Computed result key differs from the pinned job key')
        result.update(computed)
        # Admission interception is complete; publication has no planner work.
        batch.admit, batch.refresh_progress = original_admit, original_refresh
        if result.get('status') == 'budget_failed':
            normalized = normalize_proved_refusal(manifest, job['spec'], start_identity, end_identity)
            if normalized:
                result.update(status='proved_refusal', refusal_normalization=normalized)
        if result.get('status') in ('checked_plan', 'proved_refusal'):
            from .hero_coverage_publish import publish_staged
            destination = manifest.get('publish_store_dir') or str(Path(manifest['existing_catalogue_path']).parent)
            result['publication'] = publish_staged(result['key'], staging_store=manifest['store_dir'],
                store_dir=destination, expected_profile=manifest['profile'],
                coverage_start_identity=start_identity, coverage_end_identity=end_identity,
                run_dir=manifest['run_dir'])
            batch.write_json(directory/'publications'/(result['key']+'.json'), result)
    except AdmissionDeferred as error:
        result.update(status='deferred', reason=str(error), no_engine_started=True)
    except Exception as error:
        result.update(status='worker_error', reason=f'{type(error).__name__}: {error}')
    finally:
        batch.admit, batch.refresh_progress = original_admit, original_refresh
        result.update(worker_released=True, release_utc=utc())
        batch.write_json(result_path, result)
        update_report(directory)
    return result


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Conservatively reserve an unverifiable inherited PID.


def signal_owned(process, chosen):
    """Only the Popen returned here is eligible; never discovered processes."""
    try:
        os.killpg(process.pid, chosen)
        return True
    except (PermissionError, ProcessLookupError):
        return False


def signal_preflight(directory):
    records = []
    for chosen in (signal.SIGTERM, signal.SIGKILL):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'], start_new_session=True)
        passed = signal_owned(child, chosen)
        code = child.wait(timeout=10)
        records.append(dict(pid=child.pid, signal=chosen.name, owned=True, passed=passed and code == -chosen, exit=code))
        batch.write_json(Path(directory)/'signal-preflight.json', records)
        if not records[-1]['passed']:
            raise RuntimeError('Owned-child signal preflight denied; use an unsandboxed launch')
    return records


def spawn_worker(job_path, log_path):
    with Path(log_path).open('ab', buffering=0) as stream:
        return subprocess.Popen([sys.executable, '-m', 'experiments.mvp.pipeline.lattice.hero_coverage',
            '--worker', str(job_path)], cwd=batch.ROOT, stdout=stream, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)


class Scheduler:
    """One tick is injectable for quiet, resume and drain regression tests."""
    def __init__(self, directory, *, spawn=spawn_worker, reading=batch.capacity_reading,
                 alive=pid_alive, quiet=QUIET, clock=time.monotonic):
        self.directory = Path(directory)
        self.manifest = json.loads((self.directory/'manifest.json').read_text())
        self.spawn, self.reading, self.alive, self.quiet, self.clock = spawn, reading, alive, Path(quiet), clock
        self.handles = {}
        path = self.directory/'state.json'
        self.state = json.loads(path.read_text()) if path.exists() else dict(active=[], completed={}, attempts=[])
        self.state.update(pid=os.getpid(), ppid=os.getppid(), session_id=os.getsid(0),
                          started_utc=utc(), stop_command=f'touch {self.directory / "STOP"}')

    def tick(self):
        changed = not (self.directory/'progress.json').exists()
        previous_status = self.state.get('status')
        previous_flags = self.state.get('quiet'), self.state.get('stop')
        retained = []
        for item in self.state['active']:
            handle = self.handles.get(item['attempt'])
            exitcode = handle.poll() if handle else None
            receipt = Path(item['job_path']).with_name('result.json')
            # A receipt is written only after the worker's native work and publication.
            finished = exitcode is not None if handle else receipt.exists() or not self.alive(item['pid'])
            if not finished:
                retained.append(item)
                continue
            result = json.loads(receipt.read_text()) if receipt.exists() else dict(
                id=item['id'], key=item['key'], status='worker_error', reason='Owned worker exited without final receipt',
                worker_released=True, release_utc=utc(), exitcode=exitcode)
            self.state['attempts'].append(result)
            changed = True
            if result['status'] not in ('deferred', 'rerun_required'):
                self.state['completed'][item['key']] = result
            self.handles.pop(item['attempt'], None)
        self.state['active'] = retained
        reason = blocked(self.directory, self.quiet)
        capacity = self.reading() if not reason else None
        active_keys = {i['key'] for i in retained}
        pending = [spec for spec in self.manifest['instructions']
                   if spec['key'] not in self.state['completed'] and spec['key'] not in active_keys]
        while not reason and capacity is not None and pending and len(self.state['active']) < MAX_WORKERS:
            if reason := blocked(self.directory, self.quiet):
                break
            spec = pending.pop(0)
            attempt = uuid.uuid4().hex
            folder = self.directory/'attempts'/spec['key']/attempt
            job_path = folder/'job.json'
            batch.write_json(job_path, dict(manifest_path=str(self.directory/'manifest.json'),
                manifest_sha256=sha256_file(self.directory/'manifest.json'),
                spec=spec, attempt=attempt, quiet_path=str(self.quiet)))
            process = self.spawn(job_path, folder/'worker.log')
            self.handles[attempt] = process
            self.state['active'].append(dict(id=spec['id'], key=spec['key'], pid=process.pid,
                attempt=attempt, job_path=str(job_path), admitted=capacity, launched_utc=utc()))
            # Persist every exact owned PID before considering another submission.
            batch.write_json(self.directory/'state.json', self.state)
            changed = True
        active = bool(self.state['active'])
        self.state.update(updated_utc=utc(), queued=[s['id'] for s in pending],
            quiet=self.quiet.exists(), stop=(self.directory/'STOP').exists(),
            status='draining' if reason == 'stop' and active else 'stopped' if reason == 'stop' else
                   'quiet' if reason == 'quiet' else 'complete' if not pending and not active else
                   'running' if active else 'waiting_admission')
        batch.write_json(self.directory/'state.json', self.state)
        changed = changed or previous_status != self.state['status'] or previous_flags != (self.state['quiet'], self.state['stop'])
        if changed:
            update_report(self.directory)
        return self.state['status']


def update_report(directory=DIRECTORY):
    from .report import collect, render_table, _atomic_text
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    with (directory/'.report.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = []
        for spec in manifest['instructions']:
            row = None
            for suffix in ('.json', '.running.json'):
                path = Path(manifest['run_dir'])/(spec['key']+suffix)
                if path.exists():
                    row = json.loads(path.read_text())
                    if suffix == '.running.json':
                        row = dict(row, status='running')
                    break
            rows.append(collect(row or dict(spec, status='pending')))
        state_path = directory/'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        publications = []
        catalogue_path = manifest.get('existing_catalogue_path')
        catalogue = json.loads(Path(catalogue_path).read_text()) if catalogue_path and Path(catalogue_path).exists() else {}
        for path in (directory/'publications').glob('*.json'):
            item = json.loads(path.read_text())
            publication = item.get('publication') or {}
            if (publication.get('status') in ('published', 'recovered_orphan')
                    and publication.get('added_to_catalogue') is True
                    and catalogue.get(item['key']) == publication.get('entry_sha256')):
                publications.append(item)
        data = dict(as_of_utc=utc(), rows=rows, instance_sha256=manifest['instance_sha256'],
                    supervisor_pid=state.get('pid'), supervisor_status=state.get('status', 'prepared'),
                    active=state.get('active', []), pending=state.get('queued', [s['id'] for s in manifest['instructions']]),
                    quiet=Path(manifest.get('quiet_flag', QUIET)).exists(), stop=(directory/'STOP').exists(),
                    staged_checked=sum(r['status'] == 'checked_plan' for r in rows),
                    staged_refusals=sum(r['status'] == 'proved_refusal' for r in rows),
                    published=len(publications),
                    stored=sum(r['status'] == 'checked_plan' for r in publications),
                    proved_refusals=sum(r['status'] == 'proved_refusal' for r in publications),
                    errors=[r for r in state.get('completed', {}).values()
                            if r.get('status') in ('worker_error', 'error') or (r.get('publication') or {}).get('status') not in
                            (None, 'published', 'recovered_orphan', 'already_present')])
        batch.write_json(directory/'progress.json', data)
        _atomic_text(directory/'RULES.md', render_table(data))
        return data


def run(directory=DIRECTORY):
    directory = Path(directory).resolve()
    with (directory/'.supervisor.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        batch.write_json(directory/'pid.json', dict(pid=os.getpid(), ppid=os.getppid(),
            session_id=os.getsid(0), started_utc=utc(), stop_command=f'touch {directory / "STOP"}'))
        signal_preflight(directory)
        scheduler = Scheduler(directory)
        while scheduler.tick() not in ('complete', 'stopped'):
            time.sleep(2)


def launch(directory=DIRECTORY):
    directory = Path(directory).resolve()
    if not (directory/'manifest.json').exists():
        raise ValueError('Prepare and verify the coverage manifest before launch')
    with (directory/'.launch.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in (directory/'launch.json', directory/'pid.json'):
            if path.exists() and pid_alive(json.loads(path.read_text())['pid']):
                raise RuntimeError('An existing coverage supervisor PID is still alive')
        with (directory/'supervisor.log').open('ab', buffering=0) as stream:
            process = subprocess.Popen([sys.executable, '-m', 'experiments.mvp.pipeline.lattice.hero_coverage',
                '--run', '--directory', str(directory)], cwd=batch.ROOT, stdout=stream,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        receipt = dict(pid=process.pid, launched_utc=utc(), start_new_session=True,
                       stop_command=f'touch {directory / "STOP"}')
        batch.write_json(directory/'launch.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--launch', action='store_true')
    mode.add_argument('--worker', type=Path)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    args = parser.parse_args()
    if args.prepare:
        if (args.directory/'manifest.json').exists():
            raise FileExistsError('Coverage manifest already exists; resume it without --prepare')
        from .hero_coverage_queue import prepare
        manifest = prepare(args.directory)
        batch.write_json(args.directory/'manifest.json', manifest)
        update_report(args.directory)
        print(json.dumps(dict(instructions=len(manifest['instructions']), manifest=str(args.directory/'manifest.json'))))
    elif args.run:
        run(args.directory)
    elif args.launch:
        print(json.dumps(launch(args.directory)))
    else:
        run_worker(args.worker)


if __name__ == '__main__':
    main()
