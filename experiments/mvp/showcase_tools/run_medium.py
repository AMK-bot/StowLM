#!/usr/bin/env python3
"""Resumable, sequential medium SOLVER SHOWCASE experiment (not evaluation).

Use one admitted core: nice -n 10 uv run python experiments/mvp/showcase_tools/run_medium.py
Only full checker AND complete production token-mask passes are packaged as passed.
APPROXIMATION: random-yard messiness is blocking_ratio=.7 (there is no noise argument).
ASSUMPTION: fixed synthetic demand uses leader D83/D84 .25 TEU slack/.55 mass cap.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
from run_hero import THREAD_ENV, hashes, isolate_caches, load_result, memory_stats, plan_worker, utc, write_json

CASES = [('feeder_800', 68102901), ('feeder_1700', 68102902), ('feeder_800', 68102903)]
GENERATORS = ('random_yard', 'random_yard', 'simulated_yard')
PHASES = ('generation', 'plan', 'finalize', 'check', 'mask', 'package')


def source_hashes():
    result = hashes()
    for path in sorted((ROOT / 'params').rglob('*.toml')):
        result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    result[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def worker(args):
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.io import load_instance, save_instance
    assert not rule_freeze_v1_enabled(), 'Rule freeze must stay OFF'
    case = args.output
    case.mkdir(parents=True, exist_ok=True)
    # Cached numerical tables are never underneath the UI-discovered run folder.
    isolate_caches(args.root_output / 'numerical-cache')
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(args.root_output / 'numerical-cache' / 'vessels')
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    # Record only descendants this worker itself starts; remove joined processes.
    import multiprocessing.process as mp_process
    descendant_path = case / (args.phase + '.children.json')
    descendants = {}
    original_start, original_join = mp_process.BaseProcess.start, mp_process.BaseProcess.join
    def tracked_start(process, *positional, **keywords):
        value = original_start(process, *positional, **keywords)
        descendants[process.pid] = process.name
        write_json(descendant_path, {'active_pids': list(descendants), 'time': utc()})
        return value
    def tracked_join(process, *positional, **keywords):
        value = original_join(process, *positional, **keywords)
        if not process.is_alive():
            descendants.pop(process.pid, None)
            write_json(descendant_path, {'active_pids': list(descendants), 'time': utc()})
        return value
    mp_process.BaseProcess.start, mp_process.BaseProcess.join = tracked_start, tracked_join
    write_json(descendant_path, {'active_pids': [], 'time': utc()})
    started = time.monotonic()
    before = source_hashes()
    for relative, digest in before.items():
        target = case / 'stage-snapshots' / args.phase / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (ROOT / relative).read_bytes()
        target.write_bytes(content)
        if hashlib.sha256(content).hexdigest() != digest:
            raise RuntimeError(f'Source changed while snapshotting: {relative}')
    record = {'phase': args.phase, 'started_at': utc(), 'pid': os.getpid(),
              'source_hashes_before': before, 'passed': False}
    path = case / (args.phase + '.stage.json')
    write_json(path, record)
    try:
        if args.phase == 'generation':
            from stowage.generate.random_yard import RandomGenerationReport, generate_random_instance
            from stowage.generate.instances import GenerationReport, generate_simulated_instance
            simulated = args.generator == 'simulated_yard'
            audit = GenerationReport() if simulated else RandomGenerationReport()
            record.update(generator=args.generator, noise=.7 if simulated else None,
                          requested_blocking_ratio=None if simulated else .7)
            try:
                kwargs = dict(seed=args.seed, vessel_name=args.ship, port_name='laem_chabang_like',
                              booking_slack=.25, booking_mass_fraction=.55, max_attempts=200, report=audit)
                # APPROXIMATION: third case uses simulated-yard noise .7 as an independent
                # generation remedy after the first random-yard case exhausted 200 draws.
                if simulated:
                    instance = generate_simulated_instance('medium', noise=.7, **kwargs)
                else:
                    instance = generate_random_instance('medium', blocking_ratio=.7, c2_screen=False, **kwargs)
            finally:
                write_json(case / 'generation.audit.json', asdict(audit))
            save_instance(instance, case / 'instance.json')
            selected = [b for b in instance.containers if b.id in set(instance.load_list)]
            record.update(passed=any(b.dg is not None for b in selected) and any(b.reefer for b in selected), instance_id=instance.id, loads=len(selected),
                          dg=sum(b.dg is not None for b in selected), reefers=sum(b.reefer for b in selected),
                          blocking_ratio=getattr(audit, 'realised_blocking_ratio', None),
                          simulation_statistics=getattr(audit, 'simulation_statistics', None),
                          input_sha256=hashlib.sha256((case / 'instance.json').read_bytes()).hexdigest())
        else:
            instance = load_instance(case / 'instance.json')
            if args.phase == 'plan':
                # Unchanged default config except the leader-authorized C2 switch.
                args.time_limit_s = args.budget
                plan_worker(args, instance)
                raw = load_result(case / 'raw.result.json')
                record.update(passed=raw.status == 'ok' and raw.plan is not None,
                              status=raw.status, message=raw.message, metrics=raw.metrics)
            elif args.phase == 'finalize':
                from stowage.planner import finalize
                raw = load_result(case / 'raw.result.json')
                # Duration-scoped V4 only; D58 generator/constructor retain legacy path.
                try:
                    os.environ['STOWAGE_BALLAST_V4'] = '1'
                    result = finalize(instance, raw, time_limit_s=args.budget)
                finally:
                    os.environ.pop('STOWAGE_BALLAST_V4', None)
                write_json(case / 'finalized.result.json', result.model_dump(mode='json'))
                record.update(passed=result.status == 'ok' and result.plan is not None,
                              status=result.status, message=result.message, metrics=result.metrics,
                              finisher_environment={'STOWAGE_BALLAST_V4': '1'})
            elif args.phase == 'check':
                from stowage.checker import check
                result = load_result(case / 'finalized.result.json')
                if result.plan is None:
                    raise RuntimeError('No plan to check')
                report = check(instance, result.plan)
                write_json(case / 'check-report.json', report.model_dump(mode='json'))
                result._record_full_check(instance, report)
                write_json(case / 'checked.result.json', result.model_dump(mode='json'))
                hard = [v.model_dump(mode='json') for v in report.violations if v.severity == 'hard']
                record.update(passed=report.passed, first_hard_violation=hard[0] if hard else None,
                              hard_violations=len(hard), count=report.count.model_dump(mode='json') if report.count else None)
            elif args.phase == 'mask':
                # Reuse the measured production token decoder, never an action-only approximation.
                import importlib.util
                helper = ROOT / 'experiments/mvp/solve47/slack/run_small_yield_finalizer.py'
                spec = importlib.util.spec_from_file_location('showcase_medium_mask_helper', helper)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                record['mask_helper_sha256'] = hashlib.sha256(helper.read_bytes()).hexdigest()
                result = load_result(case / 'finalized.result.json')
                try:
                    module.token_replay(instance, result.plan, False, record, require_complete=True)
                finally:
                    module.summarize_replay(record)
            elif args.phase == 'package':
                from package_showcase import package_run
                # Package accepts explicit report binding and independently verifies UI import.
                entry = package_run(case / 'instance.json', case / 'checked.result.json',
                    args.root_output / 'ui', report_path=case / 'check-report.json',
                    method='solver.scalable', status='passed', command=' '.join(sys.argv))
                record.update(passed=True, entry=str(entry))
        return 0 if record['passed'] else 2
    except Exception as exc:
        record.update(error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
        traceback.print_exc()
        return 1
    finally:
        deadline_module = sys.modules.get('stowage.solver.ballast_deadline')
        if deadline_module is not None:
            deadline_module.shutdown_worker()
        after = source_hashes()
        record.update(finished_at=utc(), wall_time_s=time.monotonic()-started, resources=memory_stats(),
            source_hashes_after=after, changed_sources=sorted(p for p in before.keys() | after.keys()
                                                            if before.get(p) != after.get(p)))
        write_json(path, record)


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def supervise(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    launched = time.monotonic()
    end = launched + args.overall_seconds
    if args.deadline_utc:
        deadline = datetime.fromisoformat(args.deadline_utc)
        if deadline.tzinfo is None:
            raise ValueError('--deadline-utc requires timezone')
        end = min(end, launched + deadline.timestamp() - time.time())
    environment = os.environ.copy()
    environment.update({name: '1' for name in THREAD_ENV})
    environment.update(RAYON_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false')
    environment.pop('STOWAGE_BALLAST_V4', None)
    invocation = {'started_at': utc(), 'pid': os.getpid(), 'command': sys.argv,
                  'overall_seconds': args.overall_seconds, 'deadline_utc': args.deadline_utc,
                  'gpu': False, 'workers': 1, 'cases': CASES[:args.cases], 'generators': GENERATORS[:args.cases],
                  'booking_slack': .25, 'booking_mass_fraction': .55, 'blocking_ratio': .7,
                  'c2_global_screen': 0, 'rule_freeze_v1': False, 'solver_seed': 0, 'phases': []}
    # Append invocation identity; stage successes are durable across invocations.
    invpath = output / f'invocation-{time.time_ns()}.json'
    write_json(invpath, invocation)

    def phase(index, name, budget):
        ship, seed = CASES[index]
        case = output / 'evidence' / f'{index+1:02d}-{ship}-{seed}'
        case.mkdir(parents=True, exist_ok=True)
        stagepath = case / (name + '.stage.json')
        if read(stagepath).get('passed'):
            return True
        if (args.skip_failed_generation and name == 'generation'
                and (read(stagepath).get('finished_at') or read(stagepath).get('timed_out'))):
            return False
        if time.monotonic() >= end:
            return False
        timeout = min(budget, max(0, end-time.monotonic()))
        if timeout <= 0:
            return False
        command = [sys.executable, str(Path(__file__).resolve()), '--phase', name,
                   '--output', str(case), '--root-output', str(output), '--ship', ship,
                   '--seed', str(seed), '--generator', GENERATORS[index], '--budget', str(max(.01, timeout-3))]
        item = {'name': name, 'case': case.name, 'started_at': utc(), 'timeout_s': timeout}
        started = time.monotonic()
        write_json(case / (name + '.children.json'), {'active_pids': [], 'time': utc()})
        with (case / (name + '.log')).open('a') as log:
            child = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
            item['pid'] = child.pid
            invocation['phases'].append(item)
            write_json(invpath, invocation)
            try:
                while True:
                    remaining = timeout - (time.monotonic()-started)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        item.update(returncode=child.wait(timeout=min(10, remaining)), timed_out=False)
                        break
                    except subprocess.TimeoutExpired:
                        write_json(output / 'progress.json', {'time': utc(), 'case': case.name,
                            'phase': name, 'pid': child.pid, 'elapsed_s': time.monotonic()-started,
                            'remaining_total_s': max(0, end-time.monotonic())})
            except subprocess.TimeoutExpired:
                # Exact direct child PID created and recorded above; never pattern-based termination.
                # Descendant IDs are recorded by our own worker's Process.start hook.
                # Never enumerate unrelated processes or kill by name/pattern.
                tracked = read(case / (name + '.children.json')).get('active_pids', [])
                item['terminated_recorded_descendants'] = tracked
                for pid in tracked:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.kill()
                item.update(returncode=child.wait(), timed_out=True)
            finally:
                item['wall_time_s'] = time.monotonic()-started
                write_json(invpath, invocation)
        if item.get('timed_out'):
            stage = read(stagepath)
            stage.update(passed=False, timed_out=True, wall_time_s=item['wall_time_s'])
            write_json(stagepath, stage)
        return bool(read(stagepath).get('passed'))

    try:
        # Gather all input feature counts early before any long construction search.
        for index in range(args.cases):
            phase(index, 'generation', min(600, args.generation_seconds))
        if not args.generation_only:
            for index in range(args.cases):
                case = output / 'evidence' / f'{index+1:02d}-{CASES[index][0]}-{CASES[index][1]}'
                if not read(case / 'generation.stage.json').get('passed'):
                    continue
                remaining_cases = sum(read(output / 'evidence' / f'{j+1:02d}-{CASES[j][0]}-{CASES[j][1]}' / 'generation.stage.json').get('passed', False)
                                      for j in range(index, args.cases))
                allowance = max(0, (end-time.monotonic()) / max(1, remaining_cases))
                # APPROXIMATION: budget splits reserve finisher/check/replay for each later case.
                solve = min(args.solver_seconds, 1800, max(0, allowance-args.finisher_seconds-args.check_seconds-args.mask_seconds-60))
                if solve < 5:
                    continue
                if not phase(index, 'plan', solve):
                    continue
                # Check/replay still run on a saved failed finisher outcome for diagnosis.
                phase(index, 'finalize', min(600, args.finisher_seconds))
                if not (case / 'finalized.result.json').exists():
                    continue
                checked = phase(index, 'check', args.check_seconds)
                masked = phase(index, 'mask', args.mask_seconds)
                if checked and masked:
                    phase(index, 'package', 120)
        return 0
    finally:
        rows = []
        for index, (ship, seed) in enumerate(CASES[:args.cases]):
            case = output / 'evidence' / f'{index+1:02d}-{ship}-{seed}'
            stages = {p: read(case / (p + '.stage.json')) for p in PHASES}
            prefixes = {}
            for filename in ('raw.result.json', 'longest-prefix.result.json', 'most-loaded-prefix.result.json'):
                artifact = read(case / filename)
                if artifact:
                    prefixes[filename] = {'path': str(case / filename), 'status': artifact.get('status'),
                        'metrics': artifact.get('metrics'), 'message': artifact.get('message'),
                        'lifts': len((artifact.get('plan') or {}).get('moves', []))}
            rows.append({'case': case.name, 'prefixes': prefixes, 'progress': read(case / 'progress.json'), 'ship': ship, 'seed': seed, 'generator': GENERATORS[index], 'stages': stages,
                         'passed': all(stages[p].get('passed', False) for p in ('generation', 'plan', 'finalize', 'check', 'mask')),
                         'packaged': stages['package'].get('passed', False)})
        write_json(output / 'summary.json', {'generated_at': utc(), 'rows': rows,
            'wall_time_s': time.monotonic()-launched, 'purpose': 'SOLVER SHOWCASE ONLY; medium excluded from training/evaluation'})
        invocation.update(finished_at=utc(), wall_time_s=time.monotonic()-launched)
        write_json(invpath, invocation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'experiments/mvp/runs/showcase-medium')
    parser.add_argument('--overall-seconds', type=float, default=7200)
    parser.add_argument('--deadline-utc')
    parser.add_argument('--cases', type=int, choices=(1, 2, 3), default=3)
    parser.add_argument('--generation-only', action='store_true')
    parser.add_argument('--skip-failed-generation', action='store_true',
                        help='Resume later cases without repeating a completed bounded generation failure')
    parser.add_argument('--pilot', action='store_true', help='One case; generation120s/solve60s/finisher60s/check120s/mask120s')
    parser.add_argument('--generation-seconds', type=float, default=600)
    parser.add_argument('--solver-seconds', type=float, default=1500)
    parser.add_argument('--finisher-seconds', type=float, default=480)
    parser.add_argument('--check-seconds', type=float, default=180)
    parser.add_argument('--mask-seconds', type=float, default=180)
    parser.add_argument('--phase', choices=PHASES, help=argparse.SUPPRESS)
    parser.add_argument('--root-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--ship', help=argparse.SUPPRESS)
    parser.add_argument('--generator', choices=('random_yard', 'simulated_yard'), default='random_yard', help=argparse.SUPPRESS)
    parser.add_argument('--seed', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--budget', type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output = args.output.resolve()
    allowed = ROOT / 'experiments/mvp/runs/showcase-medium'
    if not args.output.is_relative_to(allowed):
        parser.error('Output must stay inside experiments/mvp/runs/showcase-medium')
    if args.phase and (args.root_output is None or not args.root_output.resolve().is_relative_to(allowed)):
        parser.error('Worker cache root must stay inside showcase-medium')
    if args.overall_seconds <= 0 or args.overall_seconds > 7200:
        parser.error('Overall budget must be >0 and <=7200s')
    for name in ('generation_seconds', 'solver_seconds', 'finisher_seconds', 'check_seconds', 'mask_seconds'):
        if getattr(args, name) <= 0:
            parser.error(f'{name} must be positive')
    if args.pilot:
        args.cases = 1
        args.generation_seconds = args.check_seconds = args.mask_seconds = 120
        args.solver_seconds = args.finisher_seconds = 60
    for key in THREAD_ENV:
        os.environ[key] = '1'
    return worker(args) if args.phase else supervise(args)


if __name__ == '__main__':
    sys.exit(main())
