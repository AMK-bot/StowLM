"""WARM projected-restow search from the explicitly authorized C3 witness.

This driver is deliberately separate from the cold entry points. It reads an
archived accepted plan/report, so NONE of its outcomes count as cold passes.
Fixed-topology box exchange is followed by whole-column relocation/cover
restriction masters that can change occupied pages. Replay scores actual D15
work; only the unchanged full checker can retain a cheaper candidate.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
WITNESS = ROOT/'experiments/mvp/frontier/lab/c3/c2-benders/results/verified-independent'


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _write(path, data):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False)+'\n')
    temporary.replace(path)


def _parts(report):
    count = report['count']
    return dict(lifts=float(count['weighted_nonproductive']), relocations=count['relocations'],
                hatch_now=count['hatch_cover_lifts'],
                hatch_later=sum(row['hatch_cover_lifts'] for row in count.get('future', [])),
                restow_now=count['restow_lifts'],
                restow_later=sum(row['restow_lifts'] for row in count.get('future', [])))


def _native_manifest():
    result = {}
    for name, module in tuple(sys.modules.items()):
        source = getattr(module, '__file__', None)
        if name.startswith('stowage.') and source and Path(source).suffix == '.py':
            path = Path(source).resolve()
            if path.is_relative_to(ROOT/'src'):
                result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _pattern_master(model, reference, *, deadline, seed, restriction, protected):
    """Exact restricted pool, approximate work objective; final replay decides."""
    from ortools.sat.python import cp_model
    page_tools = _load('_c4_warm_page_tools', HERE/'page_tools.py')
    cold = _load('_c4_warm_cold_code', HERE/'cold.py')
    cp = cp_model.CpModel()
    use, cover, stacks = {}, defaultdict(list), defaultdict(list)
    moments = {}
    for k in reference:
        column = model.columns[k]
        geometry = page_tools._column_geometry(model, column.page, column.boxes)
        moments[k] = (cold._vertical_tm(model, column)*1000., geometry[1], geometry[2])
    kind, target, port = restriction
    for k, column in enumerate(model.columns):
        page = model.pages[column.page]
        if k in model.banned:
            continue
        if not page.deck and page.cover == target:
            if kind == 'closed_hold_cover' or (kind == 'single_port_hold_cover' and
                                               any(model.pod[b] != port for b in column.boxes)):
                continue
        x = cp.NewBoolVar(f'column-{k}'); use[k] = x
        for box in column.boxes:
            cover[box].append(x)
        for stack in page.stacks:
            stacks[stack].append(x)
        geometry = page_tools._column_geometry(model, column.page, column.boxes)
        moments[k] = (cold._vertical_tm(model, column)*1000., geometry[1], geometry[2])
    if any(not cover[b] for b in model.boxes) or any(k not in use for k in protected):
        return None, dict(status='restricted_pool_uncovered')
    for b in model.boxes:
        cp.AddExactlyOne(cover[b])
    for terms in stacks.values():
        cp.AddAtMostOne(terms)
    for k in protected:
        cp.Add(use[k] == 1)
    baseline = [sum(moments[k][axis] for k in reference) for axis in range(3)]
    mass = sum(float(model.T.mass[b]) for b in model.boxes)
    # Integer rows guide search only. Native moment guards below the master
    # reject any unsafe-side rounding, before independent full acceptance.
    z = {k: round(moments[k][0]*1000.) for k in use}
    mx = {k: round(moments[k][1]*1000.) for k in use}
    my = {k: round(moments[k][2]*1000.) for k in use}
    cp.Add(sum(z[k]*use[k] for k in use) <= round(baseline[0]*1000.))
    for coefficients, value, tolerance in ((mx, baseline[1], mass*.05), (my, baseline[2], 50.)):
        cp.Add(sum(coefficients[k]*use[k] for k in use) >= math.ceil((value-tolerance)*1000.))
        cp.Add(sum(coefficients[k]*use[k] for k in use) <= math.floor((value+tolerance)*1000.))
    terms = [round(1000.*column.cost)*use[k] for k, column in enumerate(model.columns) if k in use]
    for hatch in sorted({p.cover for p in model.pages if p.cover is not None}, key=str):
        hold = [k for k in use if model.pages[model.columns[k].page].cover == hatch and
                not model.pages[model.columns[k].page].deck]
        if not hold:
            continue
        now = cp.NewBoolVar(f'current-open-{hatch}')
        for k in hold:
            cp.AddImplication(use[k], now)
        terms.append(2000*now)
        for pod in model.ports:
            opening = cp.NewBoolVar(f'future-open-{hatch}-{pod}')
            for k in hold:
                if any(model.pod[b] == pod for b in model.columns[k].boxes):
                    cp.AddImplication(use[k], opening)
            terms.append(2000*opening)
            for k in use:
                c = model.columns[k]; page = model.pages[c.page]
                if not page.deck or page.cover != hatch:
                    continue
                later = sum(model.pod[b] > pod for b in c.boxes)
                if later:
                    both = cp.NewBoolVar(f'restow-{hatch}-{pod}-{k}')
                    cp.Add(both >= use[k]+opening-1)
                    terms.append(2000*later*both)
    cp.Minimize(sum(terms))
    for k in reference:
        if k in use:
            cp.AddHint(use[k], 1)
    solver = cp_model.CpSolver(); solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = max(.001, min(12., deadline-time.monotonic()))
    status = solver.Solve(cp)
    row = dict(status=solver.StatusName(status), columns=len(use), restriction=list(restriction),
               scope='restricted whole-column pool; projected work guidance, not a global optimum')
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, row
    chosen = [k for k in use if solver.Value(use[k])]
    # Apply exact floating row guards to the rounded CP candidate.
    actual = [sum(moments[k][axis] for k in chosen) for axis in range(3)]
    if actual[0] > baseline[0]+1e-7 or abs(actual[1]-baseline[1]) > mass*.05+1e-7 or abs(actual[2]-baseline[2]) > 50.+1e-7:
        row['status'] = 'exact_moment_guard_rejected'
        return None, row
    return chosen, row


def _layout_candidates(instance, plan, *, seed, deadline, metadata):
    """Relocate whole patterns to different pages, then close/dedicate covers."""
    page_tools = _load('_c4_warm_page_tools', HERE/'page_tools.py')
    physics = _load('_c4_warm_physics', HERE/'physics.py')
    pages, alns, oog = page_tools._runtime()
    problem = alns.Problem(instance)
    evaluator = physics.PhysicsEvaluator(instance, plan)
    lash = page_tools._load('_c4_warm_fast', page_tools.LAB/'c1/fx-scout/lash_oracle.py')
    oracle = lash.Fast(instance, evaluator.ship)
    model = page_tools._model_class(pages)(problem, oracle=oracle, lash_limit=math.inf,
                                          deck_tiers=None, low_first_fit=False)
    model.deadline = deadline
    reference = page_tools._seed(model, plan)
    if reference is None:
        metadata['reason'] = 'witness outside pure-page relocation family'
        return
    metadata['original_columns'] = len(reference)
    baseline = page_tools._selection_metrics(model, reference)
    protected = [k for k in reference if any(b in model.special for b in model.columns[k].boxes)]
    hold_usage = defaultdict(list)
    for k in reference:
        page = model.pages[model.columns[k].page]
        if not page.deck:
            hold_usage[page.cover].append(k)
    targets = sorted(hold_usage, key=lambda hatch: (sum(len(model.columns[k].boxes) for k in hold_usage[hatch]), str(hatch)))[:2]
    model.lash_limit = 1.
    # Relocation pool permits new occupied pages; this is wider than the
    # preceding box permutation over a fixed set of occupied slot positions.
    for k in reference:
        if time.monotonic() >= deadline or len(model.columns) >= 6000:
            break
        if k in protected:
            continue
        c = model.columns[k]; original = model.pages[c.page]
        for destination, page in enumerate(model.pages):
            if time.monotonic() >= deadline:
                break
            if page.kind != original.kind or destination == c.page:
                continue
            if page_tools._column_geometry(model, destination, c.boxes)[3] > baseline['roof_max_m']+1e-8:
                continue
            model.add(destination, c.boxes)
    metadata.update(pool_columns=len(model.columns), protected_columns=len(protected), targets=list(targets))
    restrictions = [('closed_hold_cover', hatch, None) for hatch in targets]
    for hatch in targets:
        ports = sorted({model.pod[b] for k in hold_usage[hatch] for b in model.columns[k].boxes})
        restrictions += [('single_port_hold_cover', hatch, port) for port in ports[:2]]
    for ordinal, restriction in enumerate(restrictions):
        if time.monotonic() >= deadline:
            break
        chosen, row = _pattern_master(model, reference, deadline=deadline, seed=seed+ordinal,
                                      restriction=restriction, protected=protected)
        metadata.setdefault('attempts', []).append(row)
        if chosen is None:
            continue
        scored, diagnostics = page_tools._scored(model, chosen, alns, oog, seed=seed+ordinal)
        row['replay'] = diagnostics
        if scored is None:
            continue
        if diagnostics['cargo_kg_m'] > baseline['cargo_kg_m']+1e-8 or abs(diagnostics['cargo_lcg_m']-baseline['cargo_lcg_m']) > .05+1e-8:
            row['status'] = 'postprocess_moment_guard_rejected'
            continue
        yield scored.plan.model_copy(update={'ballast': plan.ballast}), row


def run_warm(*, out, seed=0, budget_s=360., polish_s=120.):
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS'):
        os.environ[name] = '1'
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
    sys.dont_write_bytecode = True
    for path in (HERE, ROOT/'src'):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from stowage.io import load_instance
    from stowage.model import MovePlan, CheckReport
    from stowage.checker import check
    from stowage.moves import apply_plan
    directory = Path(out).resolve()
    if not directory.is_relative_to(HERE):
        raise ValueError('warm output outside C4 experiment')
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); deadline = started+max(0., budget_s)
    original = {name: (WITNESS/name).read_bytes() for name in
                ('input.json', 'plan.json', 'full-report.json', 'verification.json')}
    verification = json.loads(original['verification.json'])
    for name, field in (('input.json', 'input_sha256'), ('plan.json', 'plan_sha256'), ('full-report.json', 'report_sha256')):
        if hashlib.sha256(original[name]).hexdigest() != verification[field]:
            raise ValueError('archived witness hash mismatch: '+name)
    instance = load_instance(WITNESS/'input.json')
    plan = MovePlan.model_validate_json(original['plan.json'])
    report = CheckReport.model_validate_json(original['full-report.json'])
    if not report.passed:
        raise ValueError('warm incumbent lacks saved full-checker pass')
    baseline = _parts(report.model_dump(mode='json'))
    meta = dict(entry='warm_restows.py', cold=False, warm_archived_witness=True,
        included_in_cold_pass_count=False, seed=seed, requested_budget_s=budget_s,
        archived_witness=str(WITNESS.relative_to(ROOT)), baseline=baseline, attempts=[],
        source_sha256={name: hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in
                       ('warm_restows.py', 'repair.py', 'permutation.py', 'page_tools.py', 'cold.py', 'physics.py')},
        native_source_before_sha256=_native_manifest(),
        witness_sha256={name: hashlib.sha256(value).hexdigest() for name, value in original.items()})
    _write(directory/'manifest.json', meta)
    repair = _load('_c4_warm_repair', HERE/'repair.py')
    plan, row = repair.polish_restows(instance, plan, report, seed=seed,
        budget_s=min(max(0., polish_s), max(0., deadline-time.monotonic()-60.)))
    meta['attempts'].append(row)
    if row.get('accepted_report'):
        report = CheckReport.model_validate(row['accepted_report'])
    layouts = {}
    for candidate, row in _layout_candidates(instance, plan, seed=seed+100,
        deadline=max(time.monotonic(), deadline-45.), metadata=layouts):
        replay = apply_plan(instance, candidate)
        if not replay.legal or replay.count.weighted_nonproductive >= report.count.weighted_nonproductive:
            row['acceptance'] = 'not_cheaper_real_replay'; continue
        checked = check(instance, candidate)
        row.update(full_checker_passed=checked.passed, checked_parts=_parts(checked.model_dump(mode='json')),
                   violations=[v.model_dump(mode='json') for v in checked.violations])
        _write(directory/f'layout-attempt-{len(layouts.get("attempts", []))}.json', checked.model_dump(mode='json'))
        if checked.passed and checked.count.weighted_nonproductive < report.count.weighted_nonproductive:
            plan, report = candidate, checked
    meta['layout_search'] = layouts
    # Fresh public acceptance also checks the unchanged baseline if no search
    # candidate survived. All results remain explicitly WARM.
    before = time.monotonic(); final = check(instance, plan)
    meta.update(final_checker_s=time.monotonic()-before, full_checker_passed=final.passed,
                final=_parts(final.model_dump(mode='json')), wall_s=time.monotonic()-started,
                improved=final.passed and final.count.weighted_nonproductive < baseline['lifts'],
                native_source_sha256=_native_manifest())
    meta['native_sources_unchanged'] = all(meta['native_source_sha256'].get(path) == digest
                                         for path, digest in meta['native_source_before_sha256'].items())
    _write(directory/'plan.json', plan.model_dump(mode='json'))
    _write(directory/'full-report.json', final.model_dump(mode='json'))
    _write(directory/'summary.json', meta)
    for name, value in original.items():
        if (WITNESS/name).read_bytes() != value:
            raise RuntimeError('archived witness changed during read-only warm run')
    return plan, meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=HERE/'results/warm-restows')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--budget-s', type=float, default=360.)
    parser.add_argument('--polish-s', type=float, default=120.)
    args = parser.parse_args()
    print(json.dumps({'owned_pid': os.getpid(), 'cold': False, 'warm_archived_witness': True}), flush=True)
    _, summary = run_warm(out=args.out, seed=args.seed, budget_s=args.budget_s, polish_s=args.polish_s)
    print(json.dumps({key: summary[key] for key in ('cold', 'warm_archived_witness', 'full_checker_passed', 'baseline', 'final', 'improved', 'wall_s')}), flush=True)
