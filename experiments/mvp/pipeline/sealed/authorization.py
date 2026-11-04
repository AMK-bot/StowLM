"""Driver-issued, single-use read/search capabilities for the one sealed run."""
from __future__ import annotations

import fcntl
from .common import HarnessError, exclusive_json, object_hash, read_json, resolve

TERMINAL = ('COMPLETE.json', 'FAILED.json', 'INTERRUPTED.json')


class AuthorizationError(ValueError):
    pass


def issue(job, *, phase, run_id, job_id):
    if phase not in {'plan', 'prepass', 'audit'}:
        raise ValueError('Unknown authorization phase')
    request = {k: v for k, v in job.items() if k != 'authorization'}
    binding = dict(run_id=run_id, map_id=job['map_id'], arm=job['arm'],
                   phase=phase, job_id=job_id)
    key = object_hash({k: binding[k] for k in ('run_id', 'map_id', 'arm', 'phase')})
    binding['authorization_id'] = key
    run = resolve(job['run_dir'])
    exclusive_json(run/'authorizations'/f'{key}.json',
                   dict(binding, payload_sha256=object_hash(request)))
    return dict(request, authorization=binding)


def consume(job, *, phase):
    run = resolve(job['run_dir'])
    if any((run/name).exists() for name in TERMINAL):
        raise ValueError('Terminal run cannot authorize another worker')
    # A request saved before a controller crash is not a new release mechanism.
    with (run/'controller.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise ValueError('No active controller authorizes this worker')
    binding = job.get('authorization') or {}
    if binding.get('phase') != phase:
        raise ValueError('Worker authorization phase differs')
    key = object_hash({k: binding.get(k) for k in ('run_id', 'map_id', 'arm', 'phase')})
    if (binding.get('authorization_id') != key or binding.get('map_id') != job['map_id']
            or binding.get('arm') != job['arm']):
        raise ValueError('Worker authorization identity differs')
    started = read_json(run/'STARTED.json')
    if binding.get('run_id') != started.get('run_id'):
        raise ValueError('Worker authorization run differs')
    expected = dict(binding, payload_sha256=object_hash(
        {k: v for k, v in job.items() if k != 'authorization'}))
    if read_json(run/'authorizations'/f'{key}.json') != expected:
        raise ValueError('Worker authorization payload differs')
    try:
        exclusive_json(run/'consumed'/f'{key}.json', expected)
    except HarnessError as error:
        raise ValueError('Worker authorization already consumed; refusing retry') from error
