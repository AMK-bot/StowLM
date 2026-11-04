"""F2 two-worker reproducibility arms under measured external machine load.

Never creates CPU burners. A loaded arm is pending unless at least twelve
OTHER processes meet the recorded CPU-delta criterion throughout its samples.
Only this harness's recorded child PIDs are owned. Run after BENCH-RUNNING ends.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic, sleep

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
FRONTIER = ENGINE.parent
ROOT = FRONTIER.parents[2]
MARKER = FRONTIER / 'lab/c1/leader/BENCH-RUNNING'
MAPS = (
    'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-high/seed-192200000.json',
    'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_800/f60-low/seed-192200101.json',
)
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
           'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n')
    temporary.replace(path)


def cpu_seconds(value):
    """Parse macOS/Linux cumulative ps CPU time, including days/fractions."""
    days = 0
    if '-' in value:
        day, value = value.split('-', 1)
        days = int(day)
    fields = [float(part) for part in value.split(':')]
    if len(fields) not in (2, 3):
        raise ValueError('unsupported ps CPU time')
    total = 0.
    for part in fields:
        total = 60.*total+part
    return 86400.*days+total


def parse_ps(output):
    rows = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 8)
        if len(parts) < 4:
            continue
        pid, ppid, elapsed = parts[:3]
        started = ' '.join(parts[3:8]) if len(parts) == 9 else None
        command = parts[8] if len(parts) == 9 else ' '.join(parts[3:])
        try:
            rows[int(pid)] = dict(pid=int(pid), ppid=int(ppid),
                                  cpu_s=cpu_seconds(elapsed), command=command, started=started)
        except ValueError:
            continue
    return rows


def snapshot():
    """Unavailable process access stays unknown; a load average is only a proxy."""
    try:
        output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,time=,lstart=,comm='], text=True)
        rows = parse_ps(output)
        if not rows:
            raise ValueError('ps produced no parseable process identities')
        return monotonic(), rows
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        try:
            proxy = list(os.getloadavg())
        except (OSError, AttributeError):
            proxy = None
        return monotonic(), None, dict(status='unavailable',
            reason=f'{type(error).__name__}: {error}',
            load_average_proxy=proxy,
            qualification='load average is NOT evidence of twelve other CPU-busy processes')


def descendants(rows, roots):
    owned = set(roots)
    while True:
        more = {pid for pid, row in rows.items() if row['ppid'] in owned}
        if more <= owned:
            return owned
        owned.update(more)


def load_sample(before, after, *, owned, fraction=.20):
    elapsed = after[0]-before[0]
    if elapsed <= 0.:
        raise ValueError('CPU sample interval must be positive')
    if before[1] is None or after[1] is None:
        return dict(interval_s=elapsed, criterion_cpu_fraction=fraction,
            available=False, external_busy_count=None, external_busy=[],
            excluded_owned_pids=sorted(owned),
            unavailable=[sample[2] for sample in (before, after) if sample[1] is None],
            qualification='process CPU deltas unavailable; load proxy cannot qualify F2')
    excluded = descendants(after[1], set(owned)) | descendants(before[1], set(owned))
    measured = []
    for pid, row in after[1].items():
        old = before[1].get(pid)
        # Reused IDs/new arrivals without two observations are unknown. A
        # changing command or decreasing clock also cannot certify busy time.
        if (pid in excluded or old is None or row['command'] != old['command']
                or row.get('started') != old.get('started')):
            continue
        delta = row['cpu_s']-old['cpu_s']
        if delta >= fraction*elapsed:
            measured.append(dict(pid=pid, ppid=row['ppid'], command=row['command'],
                                 cpu_delta_s=delta, cpu_fraction=delta/elapsed))
    return dict(interval_s=elapsed, criterion_cpu_fraction=fraction, available=True,
                external_busy_count=len(measured), external_busy=measured,
                excluded_owned_pids=sorted(excluded))


def file_hashes(paths, base):
    return {str(path.relative_to(base)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def freeze(out):
    """Copy only approved source, never rows/maps/results, into one shared arm artifact."""
    artifact = Path(out)/'artifact'
    if artifact.exists():
        raise ValueError('artifact already exists; use a fresh output directory')
    frontier = artifact/'experiments/mvp/frontier'
    frontier.mkdir(parents=True)
    for path in FRONTIER.glob('*.py'):
        shutil.copy2(path, frontier/path.name)
    for path in ENGINE.rglob('*.py'):
        if 'results' in path.relative_to(ENGINE).parts or '__pycache__' in path.parts:
            continue
        target = frontier/'engine'/path.relative_to(ENGINE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    # Preserve the existing fail-only diagnostic source guard without copying
    # any lab records or data. Its exact bytes are bound with the artifact.
    guard = FRONTIER/'lab/c1/fx-scout/method.py'
    if guard.exists():
        target = frontier/'lab/c1/fx-scout/method.py'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(guard, target)
    # Native providers are read-only dependencies; bind their actual bytes and
    # verify them again around each arm. Correct relative source guards resolve
    # through these links, while the engine/frontier source itself is copied.
    (artifact/'src').symlink_to(ROOT/'src', target_is_directory=True)
    (artifact/'params').symlink_to(ROOT/'params', target_is_directory=True)
    sources = file_hashes(frontier.rglob('*.py'), artifact)
    native = file_hashes((ROOT/'src/stowage').rglob('*.py'), ROOT)
    parameters = file_hashes((ROOT/'params').rglob('*.toml'), ROOT)
    manifest = dict(engine_frontier_sources=sources, native_sources=native,
                    parameters=parameters, maps={path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                                                for path in MAPS})
    manifest['artifact_id'] = canonical_hash(manifest)
    write(artifact/'MANIFEST.json', manifest)
    return artifact, manifest


def verify_artifact(artifact, manifest):
    if file_hashes((artifact/'experiments/mvp/frontier').rglob('*.py'), artifact) != manifest['engine_frontier_sources']:
        raise ValueError('frozen engine/frontier source changed between arms')
    for section in ('native_sources', 'parameters', 'maps'):
        for relative, expected in manifest[section].items():
            if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != expected:
                raise ValueError(f'bound native/input dependency changed: {relative}')


def worker(args):
    artifact = Path(args.artifact).resolve()
    repo = Path(args.repo_root).resolve()
    frontier = artifact/'experiments/mvp/frontier'
    if (repo/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING').exists():
        raise ValueError('leader benchmark active; worker admission refused')
    sys.path[:0] = [str(frontier), str(repo/'src'), str(repo)]
    for name in THREADS:
        os.environ[name] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(Path(args.worker_out).parent/'vessel-cache')
    from stowage.actions import get_action_space
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.moves import apply_plan
    from engine.planner import plan_and_check
    instance = load_instance(repo/args.map)
    expected = (88, 162)[MAPS.index(args.map)]
    if len(instance.load_list) != expected:
        raise ValueError(f'F2 map expected {expected} boxes, got {len(instance.load_list)}')
    started = monotonic()
    result, record = plan_and_check(instance, budget_s=args.budget_s,
                                    finalize_s=args.finalize_s, seed=args.seed)
    manifest = json.loads((artifact/'MANIFEST.json').read_text())
    row = dict(input=args.map, input_sha256=manifest['maps'][args.map],
               instance_id=instance.id, boxes=len(instance.load_list), size='medium',
               complete=True, passed=False, lifts=None, owned_pid=os.getpid(),
               artifact_id=manifest['artifact_id'], engine=record,
               ordered_actions=None, ordered_actions_sha256=None,
               full_stow=None, full_stow_sha256=None)
    if result is not None and result.plan is not None:
        plan = result.plan
        report = check(instance, plan)
        replay = apply_plan(instance, plan)
        space = get_action_space('boxslot')
        actions = [space.encode(action) for action in space.from_plan(instance, plan)]
        stow = sorted((p.container_id, p.slot) for p in replay.final_state.ship.placements)
        row.update(passed=bool(report.passed), replay_legal=bool(replay.legal),
                   lifts=float(report.count.weighted_nonproductive) if report.passed else None,
                   ordered_actions=actions, ordered_actions_sha256=canonical_hash(actions),
                   full_stow=stow, full_stow_sha256=canonical_hash(stow),
                   full_plan_sha256=canonical_hash(plan.model_dump(mode='json')))
        write(Path(args.worker_out).with_name('plan.json'), plan.model_dump(mode='json'))
        write(Path(args.worker_out).with_name('full-report.json'), report.model_dump(mode='json'))
    row['wall_s'] = monotonic()-started
    write(args.worker_out, row)
    return 0


def arm(name, artifact, manifest, out, args, *, unverified_load=False):
    if MARKER.exists():
        raise ValueError('leader benchmark active; two-worker arms are forbidden')
    verify_artifact(artifact, manifest)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.update({key: '1' for key in THREADS})
    jobs, logs = [], []
    before = snapshot()
    try:
        for index, path in enumerate(MAPS):
            target = out/name/f'map-{index}'/'row.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            command = ['nice', '-n', '5', sys.executable, str(artifact/'experiments/mvp/frontier/engine/bench/reproduce.py'),
                       '--worker', '--artifact', str(artifact), '--repo-root', str(ROOT),
                       '--map', path, '--worker-out', str(target),
                       '--budget-s', str(args.budget_s), '--finalize-s', str(args.finalize_s),
                       '--seed', str(args.seed)]
            log = target.with_name('worker.log').open('w'); logs.append(log)
            child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            jobs.append((child, target))
            write(out/name/'owned-pids.json', dict(harness_pid=os.getpid(), worker_pids=[p.pid for p, _ in jobs]))
        samples = []
        while any(child.poll() is None for child, _ in jobs):
            sleep(args.poll_s)  # CLI constrains this to at most sixty seconds.
            after = snapshot()
            samples.append(load_sample(before, after, owned={os.getpid(), *(p.pid for p, _ in jobs)},
                                       fraction=args.busy_cpu_fraction))
            before = after
            write(out/name/'load.json', dict(samples=samples))
        rows = [json.loads(path.read_text()) for child, path in jobs
                if child.returncode == 0 and path.exists()]
        verify_artifact(artifact, manifest)
        observed = [sample['external_busy_count'] for sample in samples
                    if sample['external_busy_count'] is not None]
        all_available = bool(samples) and len(observed) == len(samples)
        qualified = all_available and min(observed) >= args.min_external_busy and not unverified_load
        record = dict(arm=name, artifact_id=manifest['artifact_id'],
            worker_pids=[child.pid for child, _ in jobs],
            returncodes=[child.returncode for child, _ in jobs], rows=rows,
            samples=samples, load_available=all_available, unverified_load=unverified_load,
            minimum_external_busy=min(observed) if observed else None,
            maximum_external_busy=max(observed) if observed else None,
            loaded_threshold_met=qualified, required_external_busy=args.min_external_busy,
            status='complete' if len(rows) == 2 and (name != 'loaded' or qualified) else 'pending')
        write(out/name/'arm.json', record)
        return record
    finally:
        # Stop only recorded children if this harness is interrupted. Never use
        # process patterns, and never touch an observed external busy process.
        for child, _ in jobs:
            if child.poll() is None:
                child.terminate()
        for log in logs:
            log.close()


def compare_arms(first, loaded):
    a = {row['input']: row for row in first.get('rows', [])}
    b = {row['input']: row for row in loaded.get('rows', [])}
    pairs = []
    for path in MAPS:
        x, y = a.get(path), b.get(path)
        known = x is not None and y is not None and x.get('passed') and y.get('passed')
        pairs.append(dict(input=path, both_pass=bool(known),
            same_ordered_actions=(x['ordered_actions_sha256'] == y['ordered_actions_sha256']) if known else None,
            same_full_stow=(x['full_stow_sha256'] == y['full_stow_sha256']) if known else None,
            first_lifts=x.get('lifts') if x else None, loaded_lifts=y.get('lifts') if y else None))
    load_met = bool(loaded.get('loaded_threshold_met')) and not loaded.get('unverified_load', False)
    qualified = load_met and first.get('artifact_id') == loaded.get('artifact_id')
    comparable = qualified and all(p['both_pass'] for p in pairs)
    reproduced = comparable and all(p['same_ordered_actions'] and p['same_full_stow'] for p in pairs)
    return dict(status='reproduced' if reproduced else 'diverged' if comparable else 'pending',
                same_frozen_artifact=first.get('artifact_id') == loaded.get('artifact_id'),
                load_requirement_met=bool(load_met),
                exact_repeat_observed=(all(p['same_ordered_actions'] and p['same_full_stow'] for p in pairs)
                                       if all(p['both_pass'] for p in pairs) else None),
                pairs=pairs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ENGINE/'results/repro-0.12.0')
    parser.add_argument('--budget-s', type=float, default=900.)
    parser.add_argument('--finalize-s', type=float, default=600.)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--poll-s', type=float, default=5.)
    parser.add_argument('--min-external-busy', type=int, default=12)
    parser.add_argument('--busy-cpu-fraction', type=float, default=.20)
    parser.add_argument('--wait-for-load-s', type=float, default=0., help='bounded wait for naturally existing external load')
    parser.add_argument('--allow-unverified-load', action='store_true',
                        help='permit an extra exact-repeat arm without process-load evidence; F2 remains pending')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--artifact'); parser.add_argument('--repo-root')
    parser.add_argument('--map'); parser.add_argument('--worker-out')
    args = parser.parse_args(argv)
    if args.worker:
        return worker(args)
    if MARKER.exists():
        parser.error('BENCH-RUNNING exists; no two-worker reproducibility arm may start')
    if args.budget_s <= 0. or args.finalize_s <= 0. or args.wait_for_load_s < 0.:
        parser.error('positive solver budgets and nonnegative load wait required')
    if not 0. < args.poll_s <= 60. or not 0. < args.busy_cpu_fraction <= 1. or args.min_external_busy < 12:
        parser.error('poll interval must be (0,60], CPU fraction (0,1], external busy threshold >=12')
    out = args.out.resolve()
    if not out.is_relative_to(ENGINE.resolve()) or out.exists():
        parser.error('use a fresh output directory inside engine/')
    artifact, manifest = freeze(out)
    first = arm('two-worker', artifact, manifest, out, args)
    deadline = monotonic()+max(0., args.wait_for_load_s)
    before = snapshot()
    while True:
        sleep(args.poll_s)
        after = snapshot()
        admission = load_sample(before, after, owned={os.getpid()}, fraction=args.busy_cpu_fraction)
        write(out/'loaded-admission.json', admission)
        if (admission['external_busy_count'] is not None
                and admission['external_busy_count'] >= args.min_external_busy):
            loaded = arm('loaded', artifact, manifest, out, args)
            break
        if monotonic() >= deadline or (admission['external_busy_count'] is None and args.allow_unverified_load):
            if args.allow_unverified_load:
                loaded = arm('loaded', artifact, manifest, out, args, unverified_load=True)
                break
            loaded = dict(status='pending', artifact_id=manifest['artifact_id'], rows=[],
                loaded_threshold_met=False,
                reason=('process-load measurement unavailable; F2 evidence remains pending'
                        if admission['external_busy_count'] is None else
                        'fewer than twelve other CPU-busy processes observed; no burners started'))
            write(out/'loaded/arm.json', loaded)
            break
        before = after
    comparison = compare_arms(first, loaded)
    write(out/'comparison.json', comparison)
    print(json.dumps(comparison, sort_keys=True))
    return 0 if comparison['status'] == 'reproduced' else 1 if comparison['status'] == 'diverged' else 2


if __name__ == '__main__':
    raise SystemExit(main())
