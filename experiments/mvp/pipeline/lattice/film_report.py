"""Refresh film analysis from checkpointed evidence without controlling producers."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from .film_analysis import HERE, analyze, render_readme_table
from .hero_instruction_candidates import build_candidates, render_candidates_table
from .report import _atomic_text

OBSERVER = HERE/'film-observer'


def replace_section(text, name, body):
    start, end = f'<!-- {name}_START -->', f'<!-- {name}_END -->'
    block = body.strip()
    if not block.startswith(start):
        block = start+'\n'+block+'\n'+end
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        return before+block+after
    return text.rstrip()+'\n\n'+block+'\n'


def refresh():
    """Publish only analysis artifacts and README; all plan inputs are read-only."""
    with (HERE/'.film-report.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        analysis = analyze()
        candidates = build_candidates(analysis)
        for name, value in [('film-analysis.json', analysis), ('hero-instruction-candidates.json', candidates)]:
            _atomic_text(HERE/name, json.dumps(value, indent=2, sort_keys=True)+'\n')
        with (HERE/'.progress.lock').open('a+') as readme_lock:
            fcntl.flock(readme_lock, fcntl.LOCK_EX)
            path = HERE/'README.md'
            text = replace_section(path.read_text(), 'FILM_ANALYSIS', render_readme_table(analysis))
            text = replace_section(text, 'FILM_CANDIDATES', render_candidates_table(candidates))
            _atomic_text(path, text)
    return analysis


def fingerprint():
    """Ignore heartbeat churn; refresh on accepted catalogue changes or completion."""
    result = []
    for path in [HERE/'store/catalogue.json', HERE/'backup-campaign/store/catalogue.json']:
        result.append(hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)
    supervisor = HERE/'backup-campaign/supervisor.json'
    status = json.loads(supervisor.read_text()).get('status') if supervisor.exists() else None
    return result, status


def watch(*, interval=30):
    OBSERVER.mkdir(exist_ok=True)
    with (OBSERVER/'.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = None
        state = dict(pid=os.getpid(), ppid=os.getppid(), session_id=os.getsid(0),
                     purpose='read-only checkpoint observer; no producer control or native gates', status='watching')
        while True:
            try:
                current = fingerprint()
                if current != previous:
                    analysis = refresh()
                    state.update(refreshed_utc=analysis['computed_utc'],
                        counts={sha: hero['counts'] for sha, hero in analysis['heroes'].items()})
                    previous = current
                state.pop('last_error', None)
                state.update(updated_unix_s=time.time(), status='complete' if current[1] == 'complete' else 'watching')
                _atomic_text(OBSERVER/'state.json', json.dumps(state, indent=2)+'\n')
                if state['status'] == 'complete':
                    return
            except Exception as error:
                state.update(last_error=f'{type(error).__name__}: {error}', updated_unix_s=time.time())
                _atomic_text(OBSERVER/'state.json', json.dumps(state, indent=2)+'\n')
            time.sleep(interval)


def daemonize():
    first = os.fork()
    if first:
        os.waitpid(first, 0)
        return False
    os.setsid()
    if os.fork():
        os._exit(0)
    OBSERVER.mkdir(exist_ok=True)
    with open(OBSERVER/'observer.log', 'a') as log, open(os.devnull) as quiet:
        os.dup2(quiet.fileno(), 0)
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
    for _ in range(100):
        if os.getppid() == 1:
            return True
        time.sleep(.05)
    raise RuntimeError('Film observer did not reparent to PID 1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--daemon', action='store_true')
    args = parser.parse_args()
    if args.daemon and not args.watch:
        parser.error('--daemon requires --watch')
    if args.daemon and not daemonize():
        return
    if args.watch:
        watch()
    else:
        analysis = refresh()
        print(json.dumps({sha: hero['counts'] for sha, hero in analysis['heroes'].items()}))


if __name__ == '__main__':
    main()
