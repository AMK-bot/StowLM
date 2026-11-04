"""Direct TRAIN runner: same full checker, custom paths outside score allowlist.

Frozen 0.9 and engine entry points run as-is (one master worker). Every returned
finished plan is independently replayed and checked. Method candidates may use
the complete Stage B action domain, including discharge and aboard restows.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from time import perf_counter
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.dont_write_bytecode = True
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE / 'results/vessel-cache')
for path in (ROOT, ROOT / 'src', ROOT / 'experiments/mvp/frontier/lab', HERE, HERE / 'engine_copy'):
    sys.path.insert(0, str(path))


def jsonable(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(jsonable(value), sort_keys=True, indent=2) + '\n')
    temporary.replace(path)


def provenance(name):
    # Pin the actual snapshot and plug-in sources; no mutable engine imports.
    paths = [HERE / 'runner.py', HERE / 'cohort.py', HERE / 'cohort/MANIFEST.json']
    paths += sorted((HERE / 'engine_copy').rglob('*.py'))
    paths += [HERE / 'engine_copy/MANIFEST.json']
    if name == 'layover':
        paths += sorted(HERE.glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths)) if p.exists()}


def load_method():
    name = '_c3_layover_method'
    spec = importlib.util.spec_from_file_location(name, HERE / 'method.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def one_master(planner):
    native = planner.solve_master
    def single(*args, **kwargs):
        kwargs['workers'] = 1
        return native(*args, **kwargs)
    planner.solve_master = single
    try:
        yield
    finally:
        planner.solve_master = native


def aboard_restows(instance, plan, replay):
    """Exact current ID counts and legacy-D15 future ID closure.

    Future closure mirrors _projection's ordinary support/cover closure. Refuse
    the attribution if its totals disagree with checker counts (e.g. active C6
    crossing adds blockers); never substitute it for objective or pass gate.
    """
    from stowage.conventions import cells_of_slot, parse_slot
    original = {p.container_id for p in instance.onboard.placements}
    now = [m.container_id for m in plan.moves if m.kind.startswith('restow_') and m.container_id in original]
    cells = {c.id: c for c in instance.vessel.cells + instance.vessel.fixed_load_cells}
    stacks = {s.id: s for s in instance.vessel.stacks + instance.vessel.fixed_load_stacks}
    covers = {c.id: c for c in instance.vessel.hatch_covers}
    boxes = {b.id: b for b in instance.containers}
    placements = {p.container_id: p.slot for p in replay.final_state.ship.placements}
    memberships, stack_boxes = {}, defaultdict(list)
    for box, slot in placements.items():
        memberships[box] = {cells[c].stack_id for c in cells_of_slot(slot)}
        for sid in memberships[box]:
            stack_boxes[sid].append((parse_slot(slot).tier, box))
    for members in stack_boxes.values():
        members.sort()
    first = {}
    for call in instance.rotation.calls[instance.call_index+1:]:
        first.setdefault(call.locode, call.index)
    active, future = set(placements), []
    for checked in replay.count.future:
        targets = {b for b in active if first.get(boxes[b].pod) == checked.call_index}
        remaining = {sid: [b for _, b in members if b in active] for sid, members in stack_boxes.items()}
        above = defaultdict(set)
        for members in remaining.values():
            for low, high in zip(members, members[1:]):
                above[low].add(high)
        required = set(targets)
        opened = {stacks[sid].hatch_cover_id for b in targets for sid in memberships[b] if not stacks[sid].on_deck}
        for cover in opened:
            for sid in covers[cover].deck_stack_ids:
                required.update(remaining.get(sid, ()))
        pending = list(required)
        while pending:
            for high in above[pending.pop()]:
                if high not in required:
                    required.add(high)
                    pending.append(high)
        blockers = required - targets
        valid = len(blockers) == checked.restowed_boxes
        future.append(dict(call_index=checked.call_index, attribution_matches_checker=valid,
            aboard_boxes=sorted(blockers & original) if valid else None,
            aboard_lifts=len(blockers & original)*instance.objective.future_restow_lifts_per_box if valid else None))
        active.difference_update(targets)
    return dict(now_lifts=len(now), now_boxes=sorted(set(now)), future=future,
        future_lifts=sum(x['aboard_lifts'] for x in future) if all(x['attribution_matches_checker'] for x in future) else None)


def inspect_plan(instance, result):
    from stowage.moves import apply_plan
    from stowage.checker import check
    if result is None:
        return dict(passed=False, replay_complete=False, failure_reason='entry point returned no finished plan')
    plan = result.plan if hasattr(result, 'plan') else result
    if plan is None:
        return dict(passed=False, replay_complete=False, failure_reason='entry point returned no finished plan')
    replay = apply_plan(instance, plan)
    aboard = {p.container_id for p in replay.final_state.ship.placements}
    complete = replay.legal and set(instance.load_list).issubset(aboard) and not(set(instance.discharge_list) & aboard)
    report = check(instance, plan)
    count = report.count
    return dict(passed=bool(report.passed), replay_complete=complete,
        replay_legal=bool(replay.legal), loaded=len(set(instance.load_list)&aboard),
        lifts=float(count.weighted_nonproductive) if report.passed else None,
        diagnostic_lifts=float(count.weighted_nonproductive),
        parts=dict(relocations=count.relocations, hatch_now=count.hatch_cover_lifts,
            hatch_later=sum(f.hatch_cover_lifts for f in count.future), restow_now=count.restow_lifts,
            restow_later=sum(f.restow_lifts for f in count.future)),
        aboard_restows=aboard_restows(instance, plan, replay),
        inherited_violations=sum(bool(v.inherited) for v in report.violations),
        violations=[dict(rule_id=v.rule_id, severity=v.severity, inherited=v.inherited, message=v.message) for v in report.violations],
        plan=plan.model_dump(mode='json'), failure_reason=None if report.passed else 'independent full checker rejected plan')


def run_method(instance, args):
    from alns import Problem, Sequencer
    from stowage.actions.space import Place
    module = load_method()
    problem = Problem(instance)
    context = dict(problem=problem, stored=None, stored_plan=None, stored_lifts=None,
        finalize_s=args.finalize_s, tries=args.tries, method_metadata={})
    started = perf_counter()
    candidates = module.propose(instance, seed=args.seed, budget_s=args.budget_s, context=context)
    propose_s = perf_counter()-started
    ranked, errors = [], []
    for index, candidate in enumerate(candidates or []):
        try:
            actions = Sequencer(problem).order(candidate) if isinstance(candidate, dict) else tuple(candidate)
            if actions is None:
                raise ValueError('sequencing returned no actions')
            new_loads = [a.box for a in actions if isinstance(a, Place) and a.box in instance.load_list]
            if sorted(new_loads) != sorted(instance.load_list):
                raise ValueError('every new load must be placed exactly once')
            scored = problem.score(actions)
            if scored is None:
                raise ValueError('real replay rejected candidate')
            ranked.append((scored.objective, index, scored))
        except Exception as error:
            errors.append(dict(index=index, error=f'{type(error).__name__}: {error}'))
    ranked.sort(key=lambda x: (x[0], x[1]))
    attempts, best = [], None
    for objective, index, scored in ranked[:args.tries]:
        started = perf_counter()
        try:
            outcome = module.finish(instance, scored.plan, seed=args.seed, finalize_s=args.finalize_s,
                label=f'layover-{index}', context=context)
            result, metadata = outcome if isinstance(outcome, tuple) and len(outcome)==2 else (outcome, {})
            checked = inspect_plan(instance, result)
            attempt = dict(candidate=index, search_lifts=objective, finish_s=perf_counter()-started,
                           metadata=metadata, **checked)
            attempts.append(attempt)
            if checked['passed']:
                best = checked
                break
        except Exception as error:
            attempts.append(dict(candidate=index, finish_s=perf_counter()-started,
                                 error=f'{type(error).__name__}: {error}'))
    return (best or dict(passed=False, replay_complete=bool(ranked),
        failure_reason='no candidate passed finishing/full checker')) | dict(
        propose_s=propose_s, replay_candidates=len(ranked), candidate_errors=errors,
        attempts=attempts, method_metadata=context['method_metadata'])


def run_one(record, name, args):
    from stowage.io import load_instance
    path = ROOT / record['path']
    instance = load_instance(path)
    started = perf_counter()
    row = dict(method=name, instance_id=instance.id, input=record['path'],
        input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=record['size'],
        occupancy=record['occupancy_target'], onboard_boxes=len(instance.onboard.placements),
        load_boxes=len(instance.load_list), config=vars(args), passed=False, replay_complete=False)
    row['source_sha256'] = provenance(name)
    try:
        if name == 'layover':
            row.update(run_method(instance, args))
        else:
            import planner
            with one_master(planner):
                if name == 'frontier09':
                    effective_search_s = args.search_s if args.search_s is not None else min(120., max(20., 6.*len(instance.load_list)))
                    result, metadata = planner.plan_and_check(instance, search_s=effective_search_s,
                        finalize_s=args.finalize_s, seed=args.seed, tries=args.tries)
                    row['effective_search_s'] = effective_search_s
                else:
                    from engine.planner import plan_and_check
                    result, metadata = plan_and_check(instance, budget_s=args.engine_extra_s,
                        finalize_s=args.finalize_s, seed=args.seed)
            row.update(inspect_plan(instance, result), record=jsonable(metadata))
            if result is None:
                # As-is portfolio metadata does not preserve complete source
                # plans in every failure lane; absence is not evidence of zero.
                row['replay_complete'] = None
                row['replay_gate_note'] = 'No returned plan; internal complete replay gate unavailable'
    except Exception as error:
        row.update(failure_reason=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    row.update(wall_s=perf_counter()-started, complete=True)
    return row


def summarize(out):
    rows = [json.loads(p.read_text()) for p in out.glob('*/*.json') if p.name != 'summary.json']
    groups = []
    for size in ('tiny', 'small', 'medium'):
        for occupancy in (.30, .50):
            group = dict(size=size, occupancy=occupancy, methods={})
            for method in ('frontier09', 'engine', 'layover'):
                items = [r for r in rows if r.get('size')==size and r.get('occupancy')==occupancy and r.get('method')==method]
                passes = [r for r in items if r['passed']]
                group['methods'][method] = dict(maps=len(items), passes=len(passes),
                    replay_complete=sum(bool(r.get('replay_complete')) for r in items),
                    replay_unavailable=sum(r.get('replay_complete') is None for r in items),
                    mean_lifts=sum(r['lifts'] for r in passes)/len(passes) if passes else None,
                    mean_wall_s=sum(r['wall_s'] for r in items)/len(items) if items else None,
                    mean_aboard_restow_now=sum(r['aboard_restows']['now_lifts'] for r in passes)/len(passes) if passes else None,
                    mean_restow_now=sum(r['parts']['restow_now'] for r in passes)/len(passes) if passes else None,
                    mean_restow_later=sum(r['parts']['restow_later'] for r in passes)/len(passes) if passes else None)
            groups.append(group)
    dump(out/'summary.json', dict(cohort='TRAIN non-DG dry20 diagnostic; six predeclared seeds; one map per cell', groups=groups))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--methods', nargs='+', choices=('frontier09','engine','layover'), default=['frontier09','engine','layover'])
    parser.add_argument('--size', choices=('tiny','small','medium'))
    parser.add_argument('--occupancy', type=float)
    parser.add_argument('--budget-s', type=float, default=60.)
    parser.add_argument('--search-s', type=float, default=None)
    parser.add_argument('--engine-extra-s', type=float, default=0.)
    parser.add_argument('--finalize-s', type=float, default=300.)
    parser.add_argument('--tries', type=int, default=2)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', default='results/run1')
    parser.add_argument('--overwrite', action='store_true')
    args=parser.parse_args()
    out=(HERE/args.out).resolve()
    if not out.is_relative_to(HERE / 'results'):
        raise ValueError('output must remain in this experiment/results')
    print(json.dumps(dict(pid=os.getpid(), methods=args.methods, out=str(out))), flush=True)
    manifest=json.loads((HERE/'cohort/MANIFEST.json').read_text())
    for record in manifest['maps']:
        if args.size and record['size'] != args.size:
            continue
        if args.occupancy is not None and record['occupancy_target'] != args.occupancy:
            continue
        for name in args.methods:
            target=out/name/(Path(record['path']).stem+'.json')
            if target.exists() and not args.overwrite:
                prior=json.loads(target.read_text())
                digest=hashlib.sha256((ROOT/record['path']).read_bytes()).hexdigest()
                if (prior.get('complete') and prior.get('input_sha256')==digest
                        and prior.get('config')==vars(args) and prior.get('source_sha256')==provenance(name)):
                    continue
            row=run_one(record,name,args)
            dump(target,row)
            summarize(out)
            print(json.dumps({k:row.get(k) for k in ('method','instance_id','passed','replay_complete','lifts','wall_s','failure_reason')}),flush=True)
    summarize(out)


if __name__=='__main__':
    main()
