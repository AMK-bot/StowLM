"""Three isolated row sessions; an independent parent enforces a 900s wall."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from ..measurement_lifecycle import durable_json

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[3]
KINDS = ('baseline', 'close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch')
WALL_S = 900.
VALIDATED_BY = 'binding_original_instance_full_checker_and_rule_audit'


def valid_event(event, *, deadline_s=WALL_S):
    def bounded(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= deadline_s
    return (event.get('status') == 'checked_plan' and event.get('passed') is True
        and event.get('validated_by') == VALIDATED_BY
        and event.get('instruction_recheck_passed') is True
        and (event.get('checker') or {}).get('passed') is True
        and isinstance(event.get('plan'), dict)
        and bounded(event.get('wall_s'))
        and bounded(event.get('published_wall_s', event.get('received_wall_s')))
        and isinstance(event.get('lifts'), (int, float)) and not isinstance(event['lifts'], bool) and math.isfinite(event['lifts'])
        and isinstance(event.get('rules'), list)
        and all((row.get('instruction_recheck') or {}).get('passed') is True
                and not (row.get('instruction_recheck') or {}).get('violations') for row in event['rules']))


def deadline_row(job, events, *, elapsed_s=WALL_S, deadline_s=WALL_S):
    accepted = [event for event in events if valid_event(event, deadline_s=deadline_s)]
    best = min(accepted, key=lambda event: event['lifts']) if accepted else None
    first = min(event.get('published_wall_s', event['received_wall_s']) for event in accepted) if accepted else None
    evidence = dict(status='deadline_exceeded', passed=bool(best),
        plan_released=bool(best), refusal_kind=None, proved_infeasible=False,
        reason='The supervisor stopped this row at its hard wall deadline; no impossibility is established.',
        wall_s=elapsed_s, hard_wall_s=deadline_s, first_checked_wall_s=first,
        checker=best.get('checker') if best else None, rules=best.get('rules', []) if best else [],
        instruction_recheck_passed=bool(best), broken_rules=[],
        engine_version=best.get('engine_version') if best else '0.13.0',
        engine_manifest_sha256=best.get('engine_manifest_sha256') if best else None,
        native_manifest_sha256=best.get('native_manifest_sha256') if best else None,
        engine_manifest=best.get('publication_identity') if best else None,
        lane='checked_incumbent' if best else None, stages={},
        stage_attribution=best.get('stage_attribution') if best else None,
        budget_s=90., finalize_s=30., tier='T0' if best else None,
        boxes_ashore=[] if best else None,
        level=0 if best else None, lifts=best.get('lifts') if best else None)
    return dict(job, key=job.get('key', job.get('job_id')), status='deadline_exceeded',
        deadline_exceeded=True, deadline_with_incumbent=bool(best), passed=bool(best),
        live_outcome='checked_incumbent_inside_live_budget' if best else 'no_plan_inside_live_budget',
        wall_s=elapsed_s, hard_wall_s=deadline_s, first_checked_wall_s=first,
        lifts=best.get('lifts') if best else None, plan=best.get('plan') if best else None,
        evidence=evidence, measurement_identity=best.get('measurement_identity') if best else None,
        proved_infeasible=False, finished_at=time.time())


def terminate_group(process):
    """Only this Popen's new-session group; never enumerate or signal a pattern."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5.)


def start_deadline(process, seconds):
    """Parent timer kills its recorded group even while reporting is busy."""
    state = dict(fired=False, error=None)
    def expire():
        if process.poll() is not None:
            return
        state['fired'] = True
        state['fired_monotonic'] = time.monotonic()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            state['error'] = repr(error)
    timer = threading.Timer(max(0., seconds), expire)
    timer.daemon = True
    timer.start()
    return timer, state


def admitted(state_path=None):
    try:
        row = json.loads(Path(state_path or ROOT/'experiments/fleet/core-state.json').read_text())
        mask = int(row['mask'], 0) if isinstance(row['mask'], str) else int(row['mask'])
        age = time.time()-float(row['t'])
        return mask >= 0 and mask.bit_count() >= 8 and float(row['load1']) <= 45 and 0 <= age < 120
    except (OSError, ValueError, TypeError, KeyError):
        return False


def row_id(cell, path, kind):
    return hashlib.sha256(json.dumps([cell, path, kind], separators=(',', ':')).encode()).hexdigest()[:24]


def ready_jobs(manifest, finished, running=()):
    """Existing records, including failed checked attempts, are never rerun."""
    done = {(r['cell'], r['map'], r['rule_type']) for r in finished}
    busy = set(running)
    medium = manifest['selected_medium']
    medium_done = all((r['cell'], r['path'], kind) in done for r in medium for kind in KINDS)
    maps = manifest.get('opportunistic', []) if medium_done else medium
    # Finish missing baselines first, preventing a baseline at the tail from
    # serializing the last four instruction rows beyond the three-slot bound.
    for kind in KINDS:
        for record in maps:
            key = (record['cell'], record['path'], kind)
            if key in done or key in busy:
                continue
            if kind != 'baseline' and (record['cell'], record['path'], 'baseline') not in done:
                continue
            yield dict(job_id=row_id(*key), cell=key[0], map=key[1], rule_type=kind,
                       input_sha256=record['sha256'], profile='live-v2', hard_wall_s=WALL_S)


def load_finished(output):
    rows = json.loads((output/'reused.json').read_text())['rows']
    return rows + [json.loads(path.read_text()) for path in sorted((output/'jobs').glob('*.json'))]


def collect_events(active, *, elapsed_s):
    for path in sorted((active['directory']/'incumbents').glob('*.json')):
        if path.name in active['seen']:
            continue
        event = json.loads(path.read_text())
        active['seen'].add(path.name)
        event['received_wall_s'] = time.monotonic()-active['began']
        expected = active['job'].get('canonical_rule')
        actual = [row.get('canonical_envelope') for row in event.get('rules', [])]
        if actual != ([] if expected is None else [expected]):
            continue
        if event.get('engine_manifest_sha256') != active['manifest_sha256']:
            continue
        active['events'].append(event)


def launch(job, output, baseline, engine_manifest_sha256):
    directory = output/'attempts'/job['job_id']
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'incumbents').mkdir(exist_ok=True)
    job['key'] = 'wall900-'+job['job_id']
    began = time.monotonic()
    job.update(started_at=time.time(), started_monotonic=began)
    durable_json(directory/'request.json', dict(job, baseline=baseline))
    log = (directory/'worker.log').open('ab', buffering=0)
    process = subprocess.Popen([sys.executable, '-m', 'experiments.mvp.pipeline.binding.deadline.worker',
        '--request', str(directory/'request.json')], cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    log.close()
    timer, timer_state = start_deadline(process, WALL_S-(time.monotonic()-began))
    durable_json(directory/'owned-process.json', dict(pid=process.pid, pgid=process.pid,
        started_at=job['started_at'], supervisor_pid=os.getpid(), new_session=True))
    return dict(job=job, process=process, directory=directory, began=began,
                events=[], seen=set(), manifest_sha256=engine_manifest_sha256,
                timer=timer, timer_state=timer_state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    output = args.out.resolve()
    if not output.is_relative_to(HERE):
        parser.error('output must stay under binding')
    manifest = json.loads((output/'campaign.json').read_text())
    manifest['supervisor_pid'] = os.getpid()
    durable_json(output/'campaign.json', manifest)
    for item in json.loads((output/'reused.json').read_text())['sources']:
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('frozen reused row changed')
    (output/'jobs').mkdir(exist_ok=True)
    from .report import refresh
    active = {}
    finished = load_finished(output)
    refreshed = 0.
    while True:
        now = time.monotonic()
        for job_id, row in list(active.items()):
            elapsed = time.monotonic()-row['began']
            metadata = row['directory']/'selection.json'
            if metadata.exists() and not row.get('selection_loaded'):
                row['job'].update(json.loads(metadata.read_text()))
                row['selection_loaded'] = True
            collect_events(row, elapsed_s=elapsed)
            result_path = row['directory']/'result.json'
            candidate = json.loads(result_path.read_text()) if result_path.exists() else None
            elapsed = time.monotonic()-row['began']
            # A finished file still requires group cleanup: Python or a native
            # child can hang during shutdown after checkpoint publication.
            if candidate is not None and elapsed < WALL_S and not row['timer_state']['fired']:
                result = candidate
                row['timer'].cancel()
                result.update(wall_s=elapsed,
                    first_checked_wall_s=min((e.get('published_wall_s', e['received_wall_s'])
                        for e in row['events'] if valid_event(e)), default=None))
            elif elapsed >= WALL_S:
                terminate_group(row['process'])
                collect_events(row, elapsed_s=time.monotonic()-row['began'])
                result = deadline_row(row['job'], row['events'], elapsed_s=elapsed)
                result.update(killed_process_group=row['process'].pid)
            elif row['process'].poll() is not None:
                result = dict(row['job'], status='worker_failed', passed=False,
                    evidence=dict(status='no_checked_plan', passed=False, refusal_kind=None,
                                  reason='worker exited without a finalized result'),
                    wall_s=elapsed, first_checked_wall_s=None, finished_at=time.time())
            else:
                continue
            row['timer'].cancel()
            if elapsed < WALL_S:
                terminate_group(row['process'])  # also retires orphaned members after leader exit
            durable_json(output/'jobs'/(job_id+'.json'), result)
            finished.append(result)
            del active[job_id]
            refreshed = 0.
        if len(active) < 3 and admitted():
            busy = [(r['job']['cell'], r['job']['map'], r['job']['rule_type']) for r in active.values()]
            for job in ready_jobs(manifest, finished, busy):
                if len(active) == 3 or not admitted():
                    break
                base = next((r for r in finished if r['map'] == job['map'] and r['rule_type'] == 'baseline'), None)
                running = launch(job, output, base, manifest['engine_identity']['manifest_sha256'])
                active[job['job_id']] = running
        if now-refreshed > 15:
            durable_json(output/'active.json', {'rows':[dict(r['job'], pid=r['process'].pid,
                elapsed_s=time.monotonic()-r['began']) for r in active.values()],
                'supervisor_pid':os.getpid(), 'admission_waiting':not admitted()})
            refresh(output)
            refreshed = now
        if not active and not list(ready_jobs(manifest, finished)):
            durable_json(output/'complete.json', dict(completed_at=time.time(), rows=len(finished)))
            refresh(output)
            return
        time.sleep(.25)


if __name__ == '__main__':
    main()
