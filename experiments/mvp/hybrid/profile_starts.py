"""Bounded D205 diagnostic; never a hybrid measurement, finisher, or model decode.

Frozen ALNS receives an iteration clock so every start gets equal proposals and
the same cooling schedule, rather than unequal work under a wall deadline.
Only diagnostic process-local bindings are instrumented and restored.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import cProfile
import hashlib
import json
import os
from pathlib import Path
import pstats
import sys
from time import perf_counter, process_time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FRONTIER = ROOT/'experiments/mvp/frontier'
sys.path[:0] = [str(ROOT), str(ROOT/'src'), str(FRONTIER)]


class DiagnosticDeadline(RuntimeError):
    pass


def publish(path, data):
    path = Path(path).resolve()
    if not path.is_relative_to(HERE):
        raise ValueError('profiling artifacts must remain under hybrid/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timed(metrics, name, function, deadline):
    def call(*args, **kwargs):
        if perf_counter() >= deadline:
            raise DiagnosticDeadline('diagnostic wall cap reached')
        wall, cpu = perf_counter(), process_time()
        try:
            result = function(*args, **kwargs)
            metrics.setdefault(name, dict(calls=0, wall_s=0., cpu_s=0., none=0))['none'] += result is None
            return result
        finally:
            row = metrics.setdefault(name, dict(calls=0, wall_s=0., cpu_s=0., none=0))
            row['calls'] += 1
            row['wall_s'] += perf_counter()-wall
            row['cpu_s'] += process_time()-cpu
    return call


def profile_one(instance, actions, reference, *, name, iterations, output, deadline, profile_enabled=True):
    import alns
    from sight import sight_clear
    from stowage.feasibility._engine import IncrementalEngine
    from stowage.checker import rules
    from stowage.conventions import parse_slot

    metrics, counter = {}, dict(iterations=0)
    placement_signatures = {}
    load_start = list(os.getloadavg())
    problem_started = perf_counter()
    problem = alns.Problem(instance)
    setup_s = perf_counter()-problem_started
    threshold = problem.engine.T.e6_threshold
    heavy_deck = [a.box for a in actions if parse_slot(a.slot).on_deck and threshold is not None
                  and problem.boxes[a.box].gross_mass_kg >= threshold]
    score = problem.score
    initial = dict()
    def measured_score(acts):
        first = not metrics.get('score', {}).get('calls')
        wall, cpu = perf_counter(), process_time()
        result = timed(metrics, 'score', score, deadline)(acts)
        if first:
            initial.update(wall_s=perf_counter()-wall, cpu_s=process_time()-cpu,
                           objective=result.objective if result else None)
        return result
    problem.score = measured_score
    profiler = cProfile.Profile()
    with ExitStack() as stack:
        stack.enter_context(patch.object(alns, 'monotonic', lambda: float(counter['iterations'])))
        stack.enter_context(patch.object(alns, '_mask_ok', timed(metrics, 'mask', alns._mask_ok, deadline)))
        stack.enter_context(patch.object(IncrementalEngine, '_e6_last', timed(metrics, 'e6_last', IncrementalEngine._e6_last, deadline)))
        stack.enter_context(patch.object(alns.Sequencer, 'order', timed(metrics, 'sequence', alns.Sequencer.order, deadline)))
        original_placement = rules.placement_violations
        measured_placement = timed(metrics, 'placement_violations', original_placement, deadline)
        def placement(instance, ship, box, slot, **kwargs):
            signature = hashlib.sha256(json.dumps(
                [ship.model_dump(mode='json'), box.model_dump(mode='json'), slot, kwargs],
                sort_keys=True).encode()).hexdigest()
            placement_signatures[signature] = placement_signatures.get(signature, 0)+1
            return measured_placement(instance, ship, box, slot, **kwargs)
        stack.enter_context(patch.object(rules, 'placement_violations', placement))
        for operator in alns.Moves.OPERATORS:
            original = getattr(alns.Moves, operator)
            measured = timed(metrics, f'operator:{operator}', original, deadline)
            def proposal(self, assignment, _measured=measured):
                counter['iterations'] += 1
                return _measured(self, assignment)
            stack.enter_context(patch.object(alns.Moves, operator, proposal))
        gate = timed(metrics, 'sight', lambda s: sight_clear(instance, s.plan, reference)[0], deadline)
        wall, cpu = perf_counter(), process_time()
        if profile_enabled:
            profiler.enable()
        try:
            best, stats = alns.search(instance, actions, time_limit_s=float(iterations), seed=0,
                                      problem=problem, keep=3, gate=gate)
        finally:
            if profile_enabled:
                profiler.disable()
            search_wall, search_cpu = perf_counter()-wall, process_time()-cpu
    profile_path = output/f'{name}.pstats'
    if profile_enabled:
        profiler.dump_stats(str(profile_path))
    if stats.iterations != iterations or sum(stats.operator_calls.values()) != iterations:
        raise AssertionError('iteration clock did not produce the declared equal work')
    stat = pstats.Stats(profiler) if profile_enabled else None
    top = []
    for (filename, line, function), (primitive, calls, self_s, cumulative_s, callers) in sorted(
            stat.stats.items() if stat else [], key=lambda item: item[1][3], reverse=True)[:35]:
        top.append(dict(file=filename, line=line, function=function, calls=calls,
                        self_s=self_s, cumulative_s=cumulative_s))
    row = dict(name=name, diagnostic_only=True, iterations=stats.iterations, accepted=stats.accepted,
               infeasible=stats.infeasible, operator_calls=stats.operator_calls, metrics=metrics,
               initial_score=initial, best_objective=best[0].objective, search_wall_s=search_wall,
               search_cpu_s=search_cpu, cpu_wall_ratio=search_cpu/search_wall,
               problem_setup_wall_s=setup_s, load_start=load_start, load_end=list(os.getloadavg()),
               heavy_deck_boxes=heavy_deck, e6_mass_threshold=threshold,
               placement_unique_arguments=len(placement_signatures),
               placement_repeated_arguments=sum(n-1 for n in placement_signatures.values()),
               profile=str(profile_path) if profile_enabled else None, profiler_enabled=profile_enabled, top=top)
    publish(output/f'{name}.json', row)
    print(json.dumps({k: row[k] for k in ('name', 'iterations', 'accepted', 'search_wall_s', 'search_cpu_s', 'cpu_wall_ratio')}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=12, choices=range(1, 49))
    parser.add_argument('--max-wall-s', type=float, default=240)
    parser.add_argument('--out', type=Path, default=HERE/'profiling/initial')
    parser.add_argument('--order', default='blocked,llm,random')
    parser.add_argument('--no-cprofile', action='store_true')
    args = parser.parse_args()
    if not 0 < args.max_wall_s <= 1200:
        parser.error('wall cap must be positive and at most 1200 seconds')
    order = args.order.split(',')
    if len(order) != 3 or set(order) != {'blocked', 'llm', 'random'}:
        parser.error('order must contain blocked,llm,random exactly once')
    output = args.out.resolve()
    if not output.is_relative_to(HERE):
        parser.error('output must stay under hybrid/')
    output.mkdir(parents=True, exist_ok=True)
    (output/'tmp').mkdir(exist_ok=True)
    os.environ['TMPDIR'] = str(output/'tmp')
    # Reuse the existing process-local write guard; importing installs it.
    from experiments.mvp.hybrid import worker as guarded_worker
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from experiments.mvp.frontier.blocked import blocked_plan, compacted
    import alns

    started = perf_counter()
    deadline = started+args.max_wall_s
    iid = 'sim.small.41100001'
    cohort = HERE/'runs/val-quarantined-d204'
    rows = {arm: json.loads((cohort/arm/f'{iid}.json').read_text()) for arm in ('none', 'llm', 'random')}
    input_path = ROOT/rows['none']['input']
    if '/corpus/val/small/' not in str(input_path):
        raise ValueError('diagnostic refuses non-validation input')
    instance = load_instance(input_path)
    space = get_action_space('boxslot')
    plans, sources = {}, {}
    construction_started = perf_counter()
    result = blocked_plan(instance, seed=0, config=FeasibilityConfig(extra={'global_screen': 0}), time_limit_s=15)
    if result.plan is None:
        raise RuntimeError(f'cannot recover blocked diagnostic start: {result.status}')
    plans['blocked'] = compacted(instance, result.plan)
    construction_s = perf_counter()-construction_started
    for arm in ('llm', 'random'):
        admitted = next(item for item in rows[arm]['candidates']['admitted']
                        if item['label'] == rows[arm]['runs'][0]['start'])
        path = ROOT/admitted['path']
        raw = json.loads(path.read_text())
        plans[arm] = compacted(instance, MovePlan.model_validate(raw['plan']))
        sources[arm] = dict(path=str(path.relative_to(ROOT)), sha256=digest(path),
                            expected_objective=rows[arm]['runs'][0]['start_objective'])
    reference = MovePlan.model_validate(rows['none']['plan']).ballast.tank_masses_kg
    entries = []
    for name in order:
        if perf_counter() >= deadline:
            raise DiagnosticDeadline('wall cap reached before next start')
        actions = tuple(space.from_plan(instance, plans[name]))
        row = profile_one(instance, actions, reference, name=name, iterations=args.iterations,
                          output=output, deadline=deadline, profile_enabled=not args.no_cprofile)
        entries.append(row)
    result = dict(diagnostic_only=True, instance_id=iid, input=str(input_path.relative_to(ROOT)),
                  input_sha256=digest(input_path), sources=sources, construction_wall_s=construction_s,
                  iterations_per_start=args.iterations, load_end=list(os.getloadavg()),
                  order=order, profiler_enabled=not args.no_cprofile, declared_wall_cap_s=args.max_wall_s,
                  wall_s=perf_counter()-started, entries=entries,
                  source_sha256={str(path.relative_to(ROOT)): digest(path) for path in
                                 (Path(__file__).resolve(), FRONTIER/'alns.py', FRONTIER/'sight.py')})
    publish(output/'summary.json', result)


if __name__ == '__main__':
    main()
