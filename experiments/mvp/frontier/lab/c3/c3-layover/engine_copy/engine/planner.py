"""Cold-start portfolio, additive improvement stages, and the unchanged full gate."""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
from contextlib import contextmanager, ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic

# Also support direct execution of this file from the repository root.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
for path in (ROOT / 'src', HERE.parent, HERE.parent / 'lab'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR', str(HERE / 'results/vessel-cache'))

from alns import Problem
from stowage.actions import get_action_space
from stowage.checker import check
from stowage.model import PlannerResult
from engine import NAME, VERSION
from engine.config import Config
from engine import portfolio


class _Proved(Exception):
    pass


def source_sha256():
    digest = hashlib.sha256()
    for path in sorted(HERE.rglob('*.py')):
        if 'results' not in path.relative_to(HERE).parts:
            digest.update(str(path.relative_to(HERE)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def plan_and_check(instance, *, budget_s=None, finalize_s=600., seed=0, config=Config()):
    """Return a fully checked plan and stage attribution.

    On <=60 boxes, budget_s buys EXTRA work beyond the historical search and
    constructor floor. Finishing also has its own budget. Thus a small explicit
    budget cannot silently turn this entry point into a weaker 0.9.0 planner.
    Ablation switches explicitly waive the corresponding part of that floor.
    Atomic replay/checker calls can exceed a stage's wall deadline.
    """
    n = len(instance.load_list)
    extra = (120. if n <= 60 else 375.) if budget_s is None else float(budget_s)
    if not math.isfinite(extra) or extra < 0 or not math.isfinite(finalize_s) or finalize_s < 0:
        raise ValueError('budgets must be finite and nonnegative')
    began = monotonic()
    baseline_s = portfolio.baseline_search_s(instance) if n <= 60 else 0.
    finish_s = max(300., finalize_s) if n <= 60 else finalize_s
    stages = {field.name: {'enabled': getattr(config, field.name)} for field in fields(config)
              if isinstance(getattr(config, field.name), bool)}
    record = dict(name=NAME, version=VERSION, config=asdict(config), stages=stages,
                  extra_budget_s=extra, baseline_search_s=baseline_s,
                  finalize_s=finish_s, proved_optimal=False, tries=[],
                  source_sha256=source_sha256())
    archive, origins = {}, {}
    best_result, best_lifts = None, float('inf')
    bound = None
    problem = None
    attempted = set()
    resources = ExitStack()
    mask_oracle = None

    def retain(scored, origin):
        if scored is None or {a.box for a in scored.actions} != set(instance.load_list):
            return
        identity = portfolio._key(scored)
        origins.setdefault(identity, []).append(origin)
        # Keep the cheapest real loading order of each distinct stow.
        if identity not in archive or scored.objective < archive[identity].objective:
            archive[identity] = scored

    def finish_candidate(label, plan, **kwargs):
        nonlocal best_result, best_lifts
        before = monotonic()
        retain(problem.score(tuple(get_action_space('boxslot').from_plan(instance, plan))), label)
        if config.finish:
            from engine.finishing import finish_chain
            try:
                result, row = finish_chain(instance, plan, seed=seed, finalize_s=finish_s,
                                           label=label, ballast_lp=config.ballast_lp)
            except Exception as error:
                # An acceleration failure cannot remove the unchanged lane.
                from engine.frozen import planner as frozen_planner
                try:
                    # A rejected recipe must not suppress native ballast search,
                    # including when an optional acceleration raised first.
                    native_plan = (plan.model_copy(update={'ballast': None})
                                   if getattr(plan, 'ballast', None) is not None else plan)
                    result, row = frozen_planner._finish(instance, native_plan, seed=seed,
                                                         finalize_s=finish_s, label=label)
                except Exception as native_error:
                    result, row = None, dict(label=label, passed=False,
                                            native_error=f'{type(native_error).__name__}: {native_error}')
                row['acceleration_error'] = f'{type(error).__name__}: {error}'
        else:
            report = check(instance, plan)
            result = PlannerResult(status='ok', plan=plan, planner=NAME, planner_version=VERSION,
                                   seed=seed) if report.passed else None
            row = dict(label=label, lane='checker_only', passed=bool(report.passed),
                       weighted=float(report.count.weighted_nonproductive) if report.passed else None)
        row['engine_finish_s'] = monotonic()-before
        for lane in row.get('stages', []):
            if lane.get('stage') == 'ballast_lp':
                stages['ballast_lp'].setdefault('attempts', []).append(lane)
        record['tries'].append(row)
        if result is not None and result.plan is not None:
            # finish_chain's public gate is mandatory; use its checked objective.
            lifts = row.get('weighted')
            if lifts is None:
                report = check(instance, result.plan)
                if not report.passed:
                    return None, row
                lifts = float(report.count.weighted_nonproductive)
            if lifts < best_lifts:
                best_lifts, best_result = float(lifts), result.model_copy(update={
                    'planner': NAME, 'planner_version': VERSION})
                record['winner'] = label
            # Arbitrary fractional objective weights have no known positive
            # lattice spacing. A numerical tolerance cannot certify equality.
            if bound is not None and best_lifts == bound:
                record['proved_optimal'] = True
                raise _Proved
        return result, row

    def finish_ranked(candidates, prefix):
        for rank, scored in enumerate(sorted(candidates, key=lambda s: s.objective)):
            # Every cheaper candidate is attempted until a pass exists; a failure
            # never spends a fixed number of tries and hides a viable fallback.
            if best_result is not None and scored.objective >= best_lifts:
                continue
            identity = tuple((a.box, a.slot) for a in scored.actions)
            if identity in attempted:
                continue
            attempted.add(identity)
            finish_candidate(f'{prefix}-{rank}', scored.plan)

    def finish_start(_instance, plan, *, label, **kwargs):
        """Match the frozen collector's instance/plan/keyword-label callback."""
        return finish_candidate(label, plan, **kwargs)

    def stage(name, call):
        before = monotonic()
        try:
            return call()
        finally:
            stages[name]['wall_s'] = monotonic()-before

    @contextmanager
    def additive(name):
        """An optional heuristic failure must not erase a checked incumbent."""
        before = monotonic()
        try:
            yield
        except _Proved:
            raise
        except Exception as error:
            stages[name].setdefault('errors', []).append(f'{type(error).__name__}: {error}')
        finally:
            stages[name].setdefault('wall_s', monotonic()-before)

    def fusion_parents():
        selected = sorted(archive.values(), key=lambda s: s.objective)[:config.archive_size]
        seen = {portfolio._key(s) for s in selected}
        for _, scored in bank.starts if bank is not None else []:
            if portfolio._key(scored) not in seen:
                selected.append(scored)
                seen.add(portfolio._key(scored))
        return selected

    def page_verdict(map_instance, plan):
        from engine.gates.screen import ladder_evidence
        evidence = ladder_evidence(map_instance, plan)
        row = stages['diagnostic_screen']
        row['calls'] = row.get('calls', 0)+1
        row['full_fallbacks'] = row.get('full_fallbacks', 0)+int(evidence.used_full_check)
        row['partial_failures'] = row.get('partial_failures', 0)+int(not evidence.used_full_check)
        # Possible passes always come from the unchanged public checker.
        return evidence.status != 'fail', evidence.rules, evidence.bow_down

    try:
        if config.e9_fix:
            from engine.e9fix import install
            stage('e9_fix', install)
        else:
            from engine.e9fix import uninstall
            uninstall()
        problem = Problem(instance)
        if config.cached_mask:
            with additive('cached_mask'):
                from engine.gates.mask import bind
                mask_oracle = resources.enter_context(bind(problem))
        if config.bound and extra > 0:
            with additive('bound'):
                allowance = max(.01, min(30., extra*config.bound_share))
                if n > 60 and config.medium_bound:
                    from engine.scout.layout_bound import lower_bound
                    details = stage('medium_bound', lambda: lower_bound(instance,
                        budget_s=allowance, seed=seed, tables=problem.engine.T))
                    bound = float(details['lower_bound'])
                    stages['medium_bound'].update(details)
                else:
                    from engine.bounds import map_bound
                    bound, details = stage('restow_bound' if config.restow_bound else 'bound',
                        lambda: map_bound(instance, budget_s=allowance, seed=seed,
                                          problem=problem, restows=config.restow_bound and n <= 60))
                stages['bound'].update(details)
                if n <= 60:
                    stages['restow_bound']['lower_bound'] = bound
        bank = None
        if config.starts:
            before = monotonic()
            if config.witness_start and n <= 60 and extra > 0:
                with additive('witness_start'):
                    from engine.bounds.repair import repair_witness, chain_candidates
                    allocation = min(20., extra*.10)
                    witness = stages['bound'].get('relaxation_assignment')
                    deadline = monotonic()+allocation
                    candidates = (repair_witness(problem, witness, deadline=deadline, seed=seed)
                                  if witness else chain_candidates(problem, deadline=deadline, seed=seed, limit=4))
                    for actions in candidates:
                        retain(problem.score(actions), 'witness')
                    stages['witness_start'].update(candidates=len(candidates), from_bound=bool(witness))
            # Larger maps spend most construction time on pages, before columns.
            if n <= 60:
                stages['starts']['frontier'] = {}
                try:
                    bank = portfolio.collect_starts(instance, problem=problem, search_s=baseline_s,
                        finalize_s=finish_s, seed=seed, finish=finish_start,
                        record=stages['starts']['frontier'])
                finally:
                    stages['starts']['wall_s'] = monotonic()-before
                stages['starts']['frontier'] = bank.record
                for label, scored in bank.starts:
                    retain(scored, label)
            with additive('starts'):
                if n > 60 and config.layout_search:
                    from engine.scout.layout import propose as pages
                else:
                    from engine.scout.method import propose as pages
                page_context = dict(problem=problem, method_metadata={}, engine_config=config,
                                    retain_ballast_candidates=config.finish and config.ballast_lp)
                if config.diagnostic_screen and n > 60 and config.layout_search:
                    page_context['page_verdict'] = page_verdict
                page_budget = extra*.80 if n > 60 else min(60., extra*config.page_share)
                if page_budget > 0:
                    page_actions = pages(instance, seed=seed, budget_s=page_budget, context=page_context)
                    for actions in page_actions:
                        retain(problem.score(actions), 'pages')
                stages['starts']['pages'] = page_context['method_metadata']
                if n > 60 and config.layout_search:
                    stages['layout_search']['record'] = page_context['method_metadata']
            if n > 60:
                finish_ranked(list(archive.values()), 'pages')
                remaining = extra-(monotonic()-began)
                # Frozen plan_big reserves 240 seconds for two-step before up
                # to four column attempts. Do not launch it on a 30s remainder.
                if best_result is None and remaining >= 300.:
                    from engine.frozen import planner as frozen_planner
                    import columns
                    native = columns.solve_columns
                    native_finish = frozen_planner._finish
                    def one_worker(*args, **kwargs):
                        kwargs['workers'] = 1
                        return native(*args, **kwargs)
                    columns.solve_columns = one_worker
                    if not config.finish:
                        # The frozen large portfolio finishes its own starts;
                        # the ablation must apply to those internal calls too.
                        frozen_planner._finish = finish_start
                    try:
                        result, details = frozen_planner.plan_big(instance,
                            master_s=min(120., (remaining-240.)/5),
                            search_s=min(120., (remaining-240.)/5),
                            finalize_s=finish_s, seed=seed)
                    finally:
                        columns.solve_columns = native
                        frozen_planner._finish = native_finish
                    stages['starts']['big_fallback'] = details
                    if result is not None and result.plan is not None:
                        retain(problem.score(tuple(get_action_space('boxslot').from_plan(instance, result.plan))), 'big')
                        finish_candidate('big', result.plan)
            stages['starts'].update(wall_s=monotonic()-before, distinct=len(archive),
                                    best=min((s.objective for s in archive.values()), default=None))
        if config.search and n <= 60 and archive:
            # Preserve the original two starts and their entire 2/3, 1/3 budget.
            # Extra page starts receive a separate additive search allocation.
            if bank is None:
                raise RuntimeError('small-map archive requires a start bank')
            found, details = stage('search', lambda: portfolio.baseline_search(instance, bank,
                search_s=baseline_s, seed=seed, keep=max(3, config.archive_size)))
            stages['search'].update(details)
            for scored in found:
                retain(scored, 'alns')
            original_keys = {portfolio._key(s) for _, s in bank.starts[:2]}
            extra_starts = sorted((s for key, s in archive.items() if key not in original_keys),
                                  key=lambda s: s.objective)
            if extra_starts and extra > 0:
                from alns import search
                found, stats = search(instance, extra_starts[0].actions,
                    time_limit_s=min(15., extra*.05), seed=seed, problem=problem,
                    keep=config.archive_size, gate=bank.clear)
                for scored in found:
                    retain(scored, 'extra_alns')
                stages['search']['extra_iterations'] = stats.iterations
        if config.fusion and archive and extra > 0:
            with additive('fusion'):
                from engine.vlsn.search import run
                from engine.vlsn.lazy_fusion import recombine
                before = monotonic()
                allocation = min(30., extra*config.fusion_share)
                parents = fusion_parents()
                actions, details = run(problem, [s.actions for s in parents],
                                      budget_s=allocation*.65, seed=seed, keep=config.archive_size)
                for candidate in actions:
                    retain(problem.score(candidate), 'cycles')
                # Include distinct constructors as well as good search archive stows.
                parents = fusion_parents()
                fused = recombine(problem, [s.actions for s in parents],
                                  deadline=max(monotonic(), before+allocation), seed=seed, keep=2)
                for candidate in fused:
                    retain(problem.score(candidate), 'fusion')
                stages['fusion'].update(cycles=details, lazy=dict(recombine.last_stats), wall_s=monotonic()-before)
        if config.classes and archive and extra > 0:
            with additive('classes'):
                from engine.classes.method import propose_exact_classes
                before = monotonic()
                parent = min(archive.values(), key=lambda s: s.objective)
                context = dict(problem=problem, stored={a.box: a.slot for a in parent.actions},
                               stored_plan=parent.plan, stored_lifts=parent.objective, method_metadata={})
                for actions in propose_exact_classes(instance, seed=seed,
                        budget_s=min(20., extra*config.classes_share), context=context):
                    retain(problem.score(actions), 'classes')
                stages['classes'].update(context['method_metadata'], wall_s=monotonic()-before)
        if config.bound and archive:
            with additive('bound'):
                from engine.bounds import relocation_bound
                stages['bound']['fixed_stow'] = relocation_bound(problem,
                    min(archive.values(), key=lambda s: s.objective), budget_s=min(2., max(.01, extra*.01)), seed=seed)
        finish_ranked(list(archive.values()), 'ranked')
    except _Proved:
        record['stop_reason'] = 'full_checker_pass_matches_map_lower_bound'
    finally:
        resources.close()
        if mask_oracle is not None:
            stages['cached_mask'].update(mask_oracle.stats)
    record.update(wall_s=monotonic()-began, replay_legal_stows=len(archive),
                  passed=best_result is not None, lifts=best_lifts if best_result is not None else None,
                  lower_bound=bound,
                  gap=best_lifts-bound if best_result is not None and bound is not None else None)
    stages['finish'].update(attempts=len(record['tries']), passed=best_result is not None,
                            winner=record.get('winner'))
    layout_record = stages['layout_search'].get('record', {})
    contributions = layout_record.get('contributions', {})
    feature_counts = dict(asymmetric_deck='asymmetric_deck_trials',
                          unary_cache='unary_cache_hits', oog_repair='oog_repair_moved_boxes',
                          restricted_master='restricted_master_calls', securing_cuts='securing_cuts')
    for name in ('layout_search', 'asymmetric_deck', 'unary_cache', 'oog_repair',
                 'restricted_master', 'securing_cuts', 'fast_pricing', 'aggregation'):
        stages[name]['applicable'] = n > 60 and config.starts and config.layout_search
        if name in feature_counts:
            stages[name]['contributed'] = contributions.get(feature_counts[name], 0)
    stages['restow_bound']['applicable'] = n <= 60 and config.bound
    stages['medium_bound']['applicable'] = n > 60 and config.bound
    record['complete_replay_legal'] = bool(archive) or bool(
        layout_record.get('complete_replay_legal_stows')) or any(
            row.get('lifts') is not None for row in stages['starts'].get('pages', {}).get('ladder', []))
    sources = dict(starts={'twostep', 'blocked', 'yard_first', 'master', 'pages', 'big', 'witness'},
                   witness_start={'witness'},
                   search={'alns', 'extra_alns'}, fusion={'cycles', 'fusion'}, classes={'classes'})
    for name, labels in sources.items():
        contributed = [s for key, s in archive.items() if labels.intersection(origins[key])]
        stages[name].update(replay_legal_stows=len(contributed),
                           best_replay_lifts=min((s.objective for s in contributed), default=None))
    stages['bound']['proved_optimal'] = record['proved_optimal']
    record['archive'] = [dict(lifts=s.objective, origins=origins[key])
                         for key, s in sorted(archive.items(), key=lambda item: item[1].objective)]
    return best_result, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance')
    parser.add_argument('--budget-s', type=float)
    parser.add_argument('--finalize-s', type=float, default=600.)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--disable', action='append', default=[], choices=
                        [field.name for field in fields(Config) if field.type is bool])
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    from catalog import ensure_safe
    from stowage.io import load_instance
    instance = load_instance(ROOT / ensure_safe(args.instance))
    result, record = plan_and_check(instance, budget_s=args.budget_s, finalize_s=args.finalize_s,
        seed=args.seed, config=Config(**{name: False for name in args.disable}))
    if result is not None:
        record['plan'] = result.plan.model_dump(mode='json')
    payload = json.dumps(record, indent=2, default=str)+'\n'
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload)


if __name__ == '__main__':
    main()
