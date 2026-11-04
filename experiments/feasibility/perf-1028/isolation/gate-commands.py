"""Record owned gate commands and closure evidence without altering a worker."""
from __future__ import annotations

import ctypes
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REL = HERE.relative_to(ROOT)
SESSIONS = {
    "gate-saved-full-1704": 96354,
    "gate-saved-full-1705": 84029,
    "gate-saved-measurement-on": 25272,
    "gate-all48-tiny-small-on": 80743,
    "gate-saved-medium-on": 16277,
    "gate-saved-full-1706": 62952,
    "gate-saved-full-1707": 97830,
}
EXIT_CODES = {name: (0 if name in {'gate-saved-measurement-on', 'gate-all48-tiny-small-on'} else 1)
              for name in SESSIONS}


def owned_argv(pid, name):
    """Read only an explicitly recorded owned PID; do not retain unrelated env."""
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    call = lib.sysctl
    call.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                     ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    call.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, owned PID
    size = ctypes.c_size_t(1024 * 1024)
    buf = ctypes.create_string_buffer(size.value)
    if call(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buf.raw[:size.value]
    argc = struct.unpack_from('i', raw)[0]
    nonempty = [part.decode(errors='replace') for part in raw[4:].split(b'\0') if part]
    argv = nonempty[1:1 + argc]
    if str(REL / 'gate.py') not in argv or str(REL / (name + '.json')) not in argv:
        return None  # The original process exited and its PID may have been reused.
    selected_env = {}
    for value in nonempty[1 + argc:]:
        key, _, content = value.partition('=')
        if key in {'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'UV_CACHE_DIR'}:
            selected_env[key] = content
    return dict(argv=argv, env=selected_env, provenance='Exact live owned-PID KERN_PROCARGS2 observation')


def reconstruct(name, data):
    argv = [data['runtime']['python_executable'], str(REL / 'gate.py'), '--factory', str(REL / 'candidate.py')]
    if name == 'gate-all48-tiny-small-on':
        argv += ['--manifest', str(REL / 'gate-all48-manifest.json'), '--rungs', 'tiny', 'small', '--c2', '1']
    elif name == 'gate-saved-measurement-on':
        argv += ['--c2', '1', '--only-measurement-prefixes']
    else:
        seed = name.rsplit('-', 1)[1]
        argv += ['--instances', 'measure-full', '--c2', '1', '--seeds', seed]
    argv += ['--deadline', data['worker']['deadline'], '--abort-file', str(REL / 'GATE_MISMATCH.json'),
             '--output', str(REL / (name + '.json'))]
    return dict(argv=argv, env={
        'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1',
        'UV_CACHE_DIR': str(ROOT / '.uv-cache'),
    }, provenance='Reconstructed equivalent argv from immutable worker metadata; original argument ordering was not retained after process exit')


def main():
    path = HERE / 'gate-commands.json'
    prior = json.loads(path.read_text()) if path.exists() else {'jobs': {}}
    jobs = {}
    for name, session in SESSIONS.items():
        output = HERE / (name + '.json')
        if not output.exists():
            continue
        data = json.loads(output.read_text())
        pid = data['worker']['pid']
        command = owned_argv(pid, name)
        owned_process_alive = command is not None
        if command is None:
            command = prior.get('jobs', {}).get(name, {}).get('command') or reconstruct(name, data)
        failure = data.get('failure')
        jobs[name] = dict(
            session=session, pid=pid, command=command, worker=data['worker'],
            launcher='uv run python; task-level nice -n 10 attempt denied by OS, no elevated priority requested',
            observed_session_exit_code=EXIT_CODES[name], owned_process_alive=owned_process_alive,
            output=str(output.relative_to(ROOT)), stdout_stderr=str(output.with_suffix('.log').relative_to(ROOT)),
            wall_s=data.get('wall_s'), usage=data.get('usage'),
            source_audit_finished='sources_after' in data,
            changed_sources=data.get('changed_sources'),
            runtime_unchanged=(data['runtime_after'] == data['runtime']) if 'runtime_after' in data else None,
            failure=failure,
            stop_reason=('aggregate RSS soft guard' if name in {'gate-saved-full-1704', 'gate-saved-full-1705'} else
                         'source freeze invalidated; parent ordered cooperative stop' if name in {
                             'gate-saved-full-1706', 'gate-saved-full-1707', 'gate-saved-medium-on'} else
                         failure['error'] if failure else None),
            cases={key: dict(status=value['status'], actions=len(value['actions']), states=len(value['states']))
                   for key, value in data['runs'].items()},
            resume='--resume skips completed/dead/measurement cases; any stopped or running rollout restarts deterministically from the root using its seed. No native workspace or mid-rollout LP state is restored.',
        )
    result = dict(schema=1, jobs=jobs,
                  env_policy='All numeric thread limits explicitly one; Python3.12 via uv; no GPU.',
                  argv_note='Exact live argv retained where available; exited-worker reconstruction explicitly labelled.')
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({name: {key: row[key] for key in ('pid', 'source_audit_finished', 'wall_s', 'stop_reason')}
                      for name, row in jobs.items()}, indent=2))


if __name__ == '__main__':
    main()
