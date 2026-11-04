"""Reconstruct the archived D185 candidates and retain independently auditable evidence.

The C1 result rows did not retain failed stows. Reconstruction is explicitly
distinguished from historical identity: matching lifts cannot establish that.
All input paths come from the fixed lab set, never a discovery glob over maps.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from time import monotonic

for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
            'MKL_NUM_THREADS', 'HIGHS_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[key] = '1'
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = ROOT / 'experiments/mvp/frontier/lab'
SCOUT = LAB / 'c1/fx-scout'
FINISH = LAB / 'c1/fx-finish'
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'),
               str(HERE), str(SCOUT), str(FINISH), str(LAB/'c1/leader')]

from alns import Problem
from early_bound import securing_bound
from e9fix import install
from lash_oracle import Fast
from method import one_stow
from pages import Model
from stowage.io import load_instance
from stowage.model.plan import MovePlan
from stowage.moves import apply_plan


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    temporary.replace(path)


def paths():
    return [p.strip() for p in (LAB/'sets/medium12.txt').read_text().splitlines()
            if p.strip() and not p.startswith('#')]


def source_bindings():
    selected = [SCOUT/'pages.py', SCOUT/'method.py', SCOUT/'lash_oracle.py',
                FINISH/'early_bound.py', LAB/'c1/leader/e9fix.py']
    selected += sorted((ROOT/'src/stowage/checker').rglob('*.py'))
    selected += sorted((ROOT/'src/stowage/solver').glob('ballast*.py'))
    return {str(p.relative_to(ROOT)): digest(p) for p in selected}


def archive_census(out):
    records = []
    for rel in paths():
        instance = load_instance(ROOT/rel)
        archive = SCOUT/'results/medium12-1'/f'{instance.id}.json'
        old = json.loads(archive.read_text())
        for row in old.get('method_metadata', {}).get('ladder', []):
            if row.get('lifts') is None:
                continue
            records.append(dict(instance_id=instance.id, input=rel,
                rung=row['rung'], historical_lifts=row['lifts'],
                historical_rules=row.get('rules', []),
                archived_plan_available=old.get('plan') is not None,
                archived_assignment_available=old.get('assignment') is not None,
                verdict='unknown', verdict_s=0.,
                reason='Failed candidate bytes were not retained; cannot certify its identity from aggregate metadata.',
                archive_sha256=digest(archive)))
    write(out/'historical-census.json', records)
    return records


def evaluate(instance, plan, *, hydro_budget, full_check=False):
    start = monotonic()
    replay = apply_plan(instance, plan)
    placed = {q.container_id for q in replay.final_state.ship.placements}
    complete = replay.legal and set(instance.load_list) <= placed
    row = dict(replay_legal=replay.legal, all_loaded=set(instance.load_list) <= placed,
               complete_stow=complete, lifts=float(replay.count.weighted_nonproductive),
               full_checker_passed=False)
    if not complete:
        row.update(verdict='unknown', reason='Incomplete or replay-illegal reconstruction', total_s=monotonic()-start)
        return row
    before = monotonic()
    bound = securing_bound(instance, replay.final_state.ship)
    row.update(securing_bound=bound, securing_s=monotonic()-before)
    if hydro_budget > 0:
        from ballast import solve_ballast
        hydro = solve_ballast(instance, plan, budget_s=hydro_budget,
                              exact_recheck=False)
        candidate = hydro.pop('candidate_plan', None)
        row['ballast'] = hydro
    else:
        candidate = None
    row['verdict'] = 'proved_cannot' if bound.get('proof') else 'unknown'
    if not bound.get('proof') and row.get('ballast', {}).get('status') == 'infeasible_proven':
        row['verdict'] = 'proved_cannot'
    # Even a hydro success remains a search result until the unchanged full checker.
    if full_check or (candidate is not None and not bound.get('proof')):
        from stowage.checker import check
        before = monotonic()
        checked = candidate if candidate is not None else plan
        report = check(instance, checked)
        row.update(full_checker_passed=report.passed,
                   full_checker_s=monotonic()-before,
                   full_checker_rules=dict(Counter(v.rule_id for v in report.violations)))
        if report.passed:
            row.update(verdict='ballast_fixes', passing_plan=checked.model_dump(mode='json'),
                       passing_report=report.model_dump(mode='json'))
    row['total_s'] = monotonic()-start
    return row


def reconstruct(args):
    install()
    out = (HERE/args.out).resolve()
    if not out.is_relative_to(HERE):
        raise ValueError('output must stay inside c2/c2-benders')
    out.mkdir(parents=True, exist_ok=True)
    before_sources = source_bindings()
    settings = dict(construct_s=args.construct_s, hydro_s=args.hydro_s,
                    full_check=args.full_check, source_bindings=before_sources,
                    audit_sha256=digest(__file__))
    cache_key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    write(out/'run-config.json', dict(settings, cache_key=cache_key))
    write(out/'source-before.json', before_sources)
    historical = archive_census(out)
    rows = []
    for ordinal, rel in enumerate(paths()[:args.maps]):
        instance = load_instance(ROOT/rel)
        old = json.loads((SCOUT/'results/medium12-1'/f'{instance.id}.json').read_text())
        targets = [q for q in old.get('method_metadata', {}).get('ladder', [])
                   if q.get('lifts') is not None]
        if not targets:
            continue
        problem = Problem(instance)
        reference, oracle = None, None
        for target in targets:
            rung = target['rung']
            rowpath = out/f'{instance.id}-r{rung}.json'
            planpath = out/f'{instance.id}-r{rung}-plan.json'
            if rowpath.exists() and planpath.exists() and not args.fresh:
                row = json.loads(rowpath.read_text())
                if (row.get('input_sha256') == digest(ROOT/rel)
                        and row.get('cache_key') == cache_key):
                    rows.append(row)
                    if rung == 'plain':
                        reference = MovePlan.model_validate_json(planpath.read_text())
                    continue
            start = monotonic()
            if rung == 'plain':
                # Reuse the initial independent smoke result, which used the same source,
                # seed and model, to avoid duplicating a busy numerical process.
                smoke = HERE/'results/smoke-plan.json'
                if ordinal == 0 and smoke.exists() and not args.fresh:
                    plan = MovePlan.model_validate_json(smoke.read_text())
                    construction = dict(source='retained initial smoke', lifts=114.)
                    scored = None
                else:
                    scored, construction = one_stow(problem, Model(problem), seed=0,
                        deadline=monotonic()+args.construct_s)
                    plan = scored.plan if scored else None
            else:
                if reference is None:
                    rows.append(dict(instance_id=instance.id, rung=rung, verdict='unknown',
                                     reason='No reconstructed plain reference'))
                    continue
                if oracle is None:
                    oracle = Fast(instance, apply_plan(instance, reference).final_state.ship)
                settings = ast.literal_eval(target['settings'])
                layout = ast.literal_eval(target['layout'])
                if layout.get('layout'):
                    raise ValueError('This audit expects the archived no-layout D185 rungs')
                scored, construction = one_stow(problem, Model(problem, oracle=oracle, **settings),
                    seed=int(rung), deadline=monotonic()+args.construct_s)
                plan = scored.plan if scored else None
            construction_s = monotonic()-start
            row = dict(instance_id=instance.id, input=rel, input_sha256=digest(ROOT/rel),
                       cache_key=cache_key,
                       rung=rung, construction=construction, construction_s=construction_s,
                       archived_lifts=target['lifts'], historical_identity_verified=False)
            if plan is not None:
                planpath.write_text(plan.model_dump_json(indent=2))
                if rung == 'plain':
                    reference = plan
                row.update(evaluate(instance, plan, hydro_budget=args.hydro_s,
                                    full_check=args.full_check))
                row.update(plan_path=str(planpath.relative_to(ROOT)), plan_sha256=digest(planpath),
                           archived_lifts_match=row['lifts'] == target['lifts'])
            else:
                row.update(verdict='unknown', complete_stow=False,
                           reason='Reconstruction exceeded bounded constructor search')
            write(rowpath, row)
            rows.append(row)
            print(json.dumps({k: row.get(k) for k in ('instance_id','rung','complete_stow',
                'verdict','securing_s','total_s','archived_lifts_match')}), flush=True)
    after_sources = source_bindings()
    write(out/'source-after.json', after_sources)
    summary = dict(historical_complete_stows=len(historical),
        historical_identity_verified=0,
        historical_verdicts=dict(Counter(r['verdict'] for r in historical)),
        reconstructed_attempts=len(rows),
        complete_stows=sum(bool(r.get('complete_stow')) for r in rows),
        maps_with_complete_stows=len({r['instance_id'] for r in rows if r.get('complete_stow')}),
        full_checker_passes=sum(bool(r.get('full_checker_passed')) for r in rows),
        verdicts=dict(Counter(r['verdict'] for r in rows)),
        native_sources_unchanged=before_sources == after_sources,
        rows=rows)
    write(out/'summary.json', summary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='results/audit')
    parser.add_argument('--maps', type=int, default=12)
    parser.add_argument('--construct-s', type=float, default=120.)
    parser.add_argument('--hydro-s', type=float, default=3.)
    parser.add_argument('--full-check', action='store_true')
    parser.add_argument('--fresh', action='store_true')
    reconstruct(parser.parse_args())
