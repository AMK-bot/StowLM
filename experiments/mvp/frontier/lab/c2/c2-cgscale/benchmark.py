"""Paired, per-stage page-model benchmark on the approved medium12 cohort.

Run each variant in a separate process. Construction has separate phase-1 and
phase-3/dive budgets; audits and real replay are timed outside those budgets.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
from time import monotonic
import traceback

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
SCOUT = LAB / 'c1/fx-scout'
REFERENCE_LEGAL = None
# Keep optional library cache misses inside the experiment's writable scope.
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
for path in (ROOT/'src', ROOT/'experiments/mvp/frontier', LAB, SCOUT):
    sys.path.insert(0, str(path))

from alns import Problem, Sequencer
from stowage.io import load_instance


def load_module(path, name):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.__source_sha256 = digest
    return module


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    temporary.replace(path)


def census(model):
    """Exact equality over all arrays consumed by price/legality/cost."""
    T = model.T
    attrs = ('mass', 'share', 'height', 'rank', 'units', 'closes_stack',
             'is_dg', 'fallback', 'heat')
    groups = Counter()
    for b in model.boxes:
        scalars = tuple(getattr(T, key)[b].item() if hasattr(getattr(T, key)[b], 'item')
                        else getattr(T, key)[b] for key in attrs)
        groups[(scalars, model.pod[b], model.heavy[b], model.static[b].tobytes())] += 1
    return dict(rows=len(groups), multiplicities=sorted(groups.values(), reverse=True),
                fields=list(attrs)+['pod', 'heavy', 'static'])


def instrument(model):
    totals = dict(price_s=0., solve_lp_s=0., price_calls=0, solve_lp_calls=0)
    for name in ('price', 'solve_lp'):
        original = getattr(model, name)
        def timed(*args, _original=original, _name=name, **kwargs):
            started = monotonic()
            try:
                return _original(*args, **kwargs)
            finally:
                totals[_name+'_s'] += monotonic()-started
                totals[_name+'_calls'] += 1
        setattr(model, name, timed)
    return totals


def audit(model):
    bad = []
    for k, column in enumerate(model.columns):
        page = model.pages[column.page]
        if len(column.boxes) > len(page.slots) or len(set(column.boxes)) != len(column.boxes):
            bad.append(dict(column=k, reason='shape'))
        elif not model.legal(page, column.boxes) or (REFERENCE_LEGAL is not None and
                                                   not REFERENCE_LEGAL(model, page, column.boxes)):
            bad.append(dict(column=k, reason='legal'))
        elif not model.lash_ok(page, column.boxes):
            bad.append(dict(column=k, reason='lash'))
    return dict(columns=len(model.columns), illegal=bad, passed=not bad)


def measure(path, module, args, output):
    started = monotonic()
    instance = load_instance(ROOT/path)
    row = dict(instance_id=instance.id, input=path, n_boxes=len(instance.load_list),
               variant=args.variant, lp_budget_s=args.lp_s, dive_budget_s=args.dive_s,
               seed=args.seed,
               input_sha256=hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),
               source_sha256=getattr(module, '__source_sha256', hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()),
               static_adapter_sha256=getattr(module, '__static_adapter_sha256', None),
               dependency_sha256=getattr(module, '__dependency_sha256', {}),
               complete=False)
    row['stage'] = 'problem'
    write(output, row)
    tick = monotonic()
    problem = Problem(instance)
    row['problem_initialization_s'] = monotonic()-tick
    row['stage'] = 'model'
    write(output, row)
    tick = monotonic()
    model = module.Model(problem)
    row['initialization_s'] = monotonic()-tick
    row['class_census'] = census(model)
    row['stage'] = 'lp'
    write(output, row)
    counters = instrument(model)
    boxes, stacks = set(model.boxes), set(model.stacks)
    tick = monotonic()
    root = model.generate(active_boxes=boxes, active_stacks=stacks,
                          deadline=tick+args.lp_s, phase=1)
    row['lp_s'] = monotonic()-tick
    row['lp'] = {key: value for key, value in root.items() if key != 'x'}
    row['lp_profile'] = dict(counters)
    row['root_rmp'] = list(getattr(model, 'rmp_history', ()))
    row['root_pattern_audit'] = audit(model)
    row['stage'] = 'phase3_dive'
    write(output, row)
    tick = monotonic()
    deadline = tick+args.dive_s
    if getattr(model, 'seed_chosen', None) is not None:
        phase3 = dict(status='skipped_feasible_incumbent')
    else:
        phase3 = model.generate(active_boxes=boxes, active_stacks=stacks,
                                deadline=deadline, phase=3, max_rounds=30)
    row['phase3_s'] = monotonic()-tick
    row['phase3'] = {key: value for key, value in phase3.items() if key != 'x'}
    tick = monotonic()
    chosen, info = model.dive(deadline=deadline, rng=random.Random(args.seed))
    row['dive_s'] = monotonic()-tick
    row['dive'] = info
    row['phase3_dive_s'] = row['phase3_s']+row['dive_s']
    row['complete_stow'] = False
    row['replay_legal'] = False
    if chosen is not None:
        tick = monotonic()
        chosen, heel = model.balance(chosen, random.Random(args.seed))
        assignment = model.clear_oog(model.assignment(chosen))
        row['balance_clear_s'] = monotonic()-tick
        row['heel_tm'] = heel
        row['complete_stow'] = set(assignment) == set(instance.load_list)
        row['mixes_hold_stack'] = problem.mixes_hold_stack(assignment)
        tick = monotonic()
        actions = Sequencer(problem).order(assignment)
        row['sequence_s'] = monotonic()-tick
        tick = monotonic()
        scored = problem.score(actions) if actions is not None else None
        row['score_s'] = monotonic()-tick
        row['replay_legal'] = bool(scored is not None and row['complete_stow'])
        row['lifts'] = scored.objective if scored else None
        row['assignment'] = assignment
    row['pattern_audit'] = audit(model)
    row['rmp'] = list(getattr(model, 'rmp_history', ()))
    row['profile'] = counters
    row['wall_s'] = monotonic()-started
    row['complete'] = True
    row['stage'] = 'done'
    write(output, row)
    return row


def main():
    global REFERENCE_LEGAL
    parser = argparse.ArgumentParser()
    parser.add_argument('--variant', choices=('original', 'optimized', 'rmp'), required=True)
    parser.add_argument('--lp-s', type=float, default=60.)
    parser.add_argument('--dive-s', type=float, default=60.)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--only', nargs='*', default=[])
    parser.add_argument('--static-adapter', type=Path,
                        help='Use a separately verified, equivalent _static only; original pricing/master stay unchanged.')
    args = parser.parse_args()
    print(json.dumps(dict(event='process', pid=os.getpid(), variant=args.variant)), flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    # Reuse the canonical module name when the RMP method imports this same fix.
    load_module(LAB/'c1/leader/e9fix.py', 'e9fix').install()
    source = SCOUT/'pages.py' if args.variant == 'original' else HERE/('rmp_method.py' if args.variant == 'rmp' else 'pages.py')
    dependencies = {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                    for name in ('pages.py', 'method.py')} if args.variant == 'rmp' else {}
    module = load_module(source, '_benchmark_pages_'+args.variant)
    module.__dependency_sha256 = dependencies
    reference = module if args.variant == 'original' else load_module(SCOUT/'pages.py', '_benchmark_reference_pages')
    REFERENCE_LEGAL = reference.Model.legal
    if args.static_adapter:
        if args.variant != 'original':
            parser.error('--static-adapter is only meaningful for the original variant')
        adapter = load_module(args.static_adapter, '_benchmark_static_adapter')
        module.Model._static = adapter.Model._static
        module.__static_adapter_sha256 = adapter.__source_sha256
    paths = [line.strip() for line in (LAB/'sets/medium12.txt').read_text().splitlines()
             if line.strip() and not line.startswith('#')]
    # Expose the scale bottleneck first while retaining the fixed cohort.
    paths.sort(key=lambda path: ('feeder_1700' not in path, path))
    for path in paths:
        if args.only and not any(token in path for token in args.only):
            continue
        target = args.out/(Path(path).stem+'.json')
        if target.exists():
            cached = json.loads(target.read_text())
            if (cached.get('complete') and cached.get('variant') == args.variant
                    and cached.get('source_sha256') == module.__source_sha256
                    and cached.get('dependency_sha256', {}) == module.__dependency_sha256
                    and cached.get('lp_budget_s') == args.lp_s
                    and cached.get('dive_budget_s') == args.dive_s
                    and cached.get('seed', 0) == args.seed):
                continue
        print(json.dumps(dict(event='start', input=path)), flush=True)
        try:
            row = measure(path, module, args, target)
            print(json.dumps({key: row.get(key) for key in ('instance_id', 'lp_s', 'phase3_s', 'dive_s', 'complete_stow', 'replay_legal')}), flush=True)
        except Exception:
            error = traceback.format_exc()
            write(target, dict(input=path, error=error, complete=True))
            print(error, flush=True)


if __name__ == '__main__':
    main()
