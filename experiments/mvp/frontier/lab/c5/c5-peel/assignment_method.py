"""Completion-first, checker-authoritative ladder on a copied assignment LP."""
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from assignment_runtime import load_assignment
from runtime import load_engine
load_engine()
from finishing import Candidates
from stowage.checker import check
from stowage.model import PlannerResult

HERE = Path(__file__).resolve().parent
DEFAULT_BUDGETS = {'proposal_s': 240., 'finish_s': 120., 'max_candidates': 2}

def _result(plan, seed):
    return PlannerResult(status='ok', plan=plan, planner='frontier.assignment-peel',
                         planner_version='0.3.0', seed=seed)

def _stow_key(plan):
    # Different ballast starts can lead to different local improvements.
    return plan.model_dump_json()

@contextmanager
def observe_optimizer(module, bank):
    original = module.optimize
    seen = getattr(bank, '_finished_stows', set())
    bank._finished_stows = seen
    def observed(instance, plan, **kwargs):
        outcome = original(instance, plan, **kwargs)
        seen.add(_stow_key(plan))
        return outcome
    module.optimize = observed
    try:
        yield
    finally:
        module.optimize = original

@contextmanager
def observe_checks(module, bank):
    original = module.check
    pending = []
    def observed(instance, plan):
        report = original(instance, plan)
        pending.append((plan, report))
        return report
    module.check = observed
    try:
        yield
    finally:
        module.check = original
        # Ranking/analysis happens after the ordinary timed invocation; its
        # callback only appends reports already computed by the method.
        for plan, report in pending:
            bank.observe(plan, report, source='ordinary-assignment-check')

def ordinary_assignment(instance, *, seed=0, budget_s=240., finalize_s=120., bank=None):
    """The copied ordinary propose/finish, two tries, without changing its returns."""
    ordinary = load_assignment()
    ordinary.install()
    from alns import Problem
    context = {'problem': Problem(instance), 'method_metadata': {},
               'finalize_s': finalize_s, 'tries': 2, 'stored': None, 'stored_plan': None}
    started = monotonic()
    # Observing reports already computed by the method adds no checks to L0.
    if bank is None:
        bank = Candidates(instance)
    import ballast_opt
    with observe_checks(ordinary, bank), observe_checks(ballast_opt, bank), observe_optimizer(ballast_opt, bank):
        actions = ordinary.propose(instance, seed=seed, budget_s=budget_s, context=context)
        candidates, seen = [], set()
        for sequence in actions:
            scored = context['problem'].score(sequence)
            key = tuple((a.box, a.slot) for a in sequence)
            if scored is not None and key not in seen and {a.box for a in scored.actions} == set(instance.load_list):
                seen.add(key)
                candidates.append(scored)
        candidates.sort(key=lambda row: row.objective)
        tries = []
        for index, scored in enumerate(candidates[:2]):
            try:
                plan, metadata = ordinary.finish(instance, scored.plan, seed=seed,
                    finalize_s=finalize_s, label=f'lab-{index}', context=context)
                tries.append(metadata)
                if plan is not None:
                    report = check(instance, plan)
                    if report.passed:
                        row = bank.observe(plan, report, source='ordinary-assignment-finished')
                        if row is None:
                            row = next(r for r in bank.rows if r['plan'].model_dump_json() == plan.model_dump_json())
                        return _result(plan, seed), dict(context=context, tries=tries,
                            wall_s=monotonic()-started, selected=row)
            except Exception as error:
                tries.append({'candidate': index, 'error': f'{type(error).__name__}: {error}'})
    # These were already generated in this invocation, never read from disk.
    for plan in context.get('_candidate_plans', {}).values():
        bank.observe(plan, source='ordinary-assignment-rejected-complete')
    return None, dict(context=context, tries=tries, wall_s=monotonic()-started)

def normal(instance, *, seed=0, budget_s=240., finalize_s=120., bank=None):
    # The securing lane's published decision is medium only. Preserve the
    # ordinary frozen engine on tiny/small rather than applying dense targets.
    if len(instance.load_list) <= 60:
        from engine.planner import plan_and_check
        return plan_and_check(instance, seed=seed, budget_s=budget_s, finalize_s=finalize_s)
    return ordinary_assignment(instance, seed=seed, budget_s=budget_s,
                               finalize_s=finalize_s, bank=bank)

def _public_engine(metadata):
    result = {key: value for key, value in metadata.items() if key not in ('context', 'selected')}
    if 'context' in metadata:
        result['proposal'] = metadata['context'].get('method_metadata', {})
    return result

def finish_candidate(instance, plan, bank, *, level, seed, budget_s, label):
    """Same real area/securing ballast optimizer, retaining failed output too."""
    started = monotonic()
    raw = bank.observe(plan, source=f'L{level}-assignment-raw')
    if raw is not None and raw['report'].passed:
        return
    seen = getattr(bank, '_finished_stows', set())
    bank._finished_stows = seen
    key = _stow_key(plan)
    if key in seen:
        bank.finishing_records.append({'level': level, 'elapsed_s': 0.,
            'metadata': {'reused_same_invocation_finishing': True}})
        return
    from ballast_opt import optimize
    try:
        candidate, metadata = optimize(instance, plan,
            out=HERE/'assignment-ballast'/str(__import__('os').getpid())/instance.id/label,
            budget_s=max(1., budget_s), max_evaluations=12, max_iterations=2)
        bank.observe(candidate, source=f'L{level}-assignment-finished')
        seen.add(key)
    except (ValueError, RuntimeError, ArithmeticError, KeyError, OSError) as error:
        metadata = {'error': f'{type(error).__name__}: {error}'}
    bank.finishing_records.append({'level': level, 'budget_s': budget_s,
        'elapsed_s': monotonic()-started, 'metadata': metadata})

def generate(instance, *, level, seed, budget_s, partial=False, reference=None, bank=None):
    """Run the original GM loop with tier-aware assignment rows and no page fallback."""
    ordinary = load_assignment()
    from assignment_masks import assignment_masks
    from elastic import ElasticAssignment
    from alns import Problem
    from assignment_proposal import propose as elastic_propose
    made = []
    def assignment(*args, **kwargs):
        solver = ElasticAssignment(*args, level=level, allow_partial=partial, **kwargs)
        made.append(solver)
        return solver
    context = {'method_metadata': {}, 'stored': None, 'stored_plan': None}
    started = monotonic()
    with assignment_masks(level, model_cls=ordinary.Model):
        context['problem'] = Problem(instance)
        elastic_propose(instance, seed=seed, budget_s=budget_s, context=context,
            level=level, allow_partial=partial, factory=assignment, initial_reference=reference)
    if bank is not None:
        for key, plan in context.get('_candidate_plans', {}).items():
            bank.observe(plan, context.get('_candidate_reports', {}).get(key),
                         source=f'L{level}-elastic-'+context.get('_candidate_kinds', {}).get(key, 'candidate'))
    diagnostics = []
    for solver in made:
        diagnostics.extend(getattr(solver, 'diagnostics', []))
    return list(context.get('_candidate_plans', {}).values()), {
        'partial_enabled': partial, 'elapsed_s': monotonic()-started,
        'proposal': context['method_metadata'], 'elastic': diagnostics}

def _reference_from_checked(instance, bank):
    """Reuse only physical evidence computed on this invocation's candidates."""
    rows = [row for row in bank.rows if row['analysis']['level'] is not None]
    if not rows:
        return None
    selected = min(rows, key=bank.rank)
    load_assignment()
    from chainlab import condition_terms
    from stowage.checker.full import departure_condition
    import math
    try:
        terms = condition_terms(instance, departure_condition(instance, selected['plan']))
        if not all(math.isfinite(terms[key]) and terms[key] > 0 for key in ('gm', 'draught', 'cb')):
            return None
    except (ValueError, ArithmeticError, KeyError):
        return None
    return dict(gm=terms['gm']+.05, measured_gm=terms['gm'], draught=terms['draught'],
        cb=terms['cb'], source='same-invocation checked '+selected['source'],
        source_checker_level=selected['analysis']['level'])

def solve(instance, *, seed=0, budgets=None, normal_call=None):
    work = dict(DEFAULT_BUDGETS, **(budgets or {}))
    bank = Candidates(instance)
    started = monotonic()
    result, metadata = (normal_call or normal)(instance, seed=seed,
        budget_s=work['proposal_s'], finalize_s=work['finish_s'], bank=bank)
    trials = [dict(level=0, elapsed_s=monotonic()-started,
        status='full checker pass' if result is not None else 'no completed plan found at L0 within this work',
        engine=_public_engine(metadata))]
    # Shared serialization preserves the D242 row contract and exact plan.
    from assignment_records import _package
    if result is not None and result.plan is not None:
        report = check(instance, result.plan)
        if report.passed:
            row = bank.observe(result.plan, report, source='ordinary-L0-unchanged')
            if row is None:
                row = next(r for r in bank.rows if r['plan'].model_dump_json() == result.plan.model_dump_json())
            return _package(instance, bank, complete=row, searched_level=0,
                            trials=trials, seed=seed, result=result)
    for level in range(1, 6):
        print('ASSIGNMENT LADDER', instance.id, f'L{level}', 'proposal', flush=True)
        before = monotonic()
        reference = _reference_from_checked(instance, bank)
        candidates, diagnostic = generate(instance, level=level, seed=seed,
            budget_s=work['proposal_s'] * (.7 if level == 3 else 1.), partial=False, reference=reference, bank=bank)
        for plan in candidates:
            bank.observe(plan, source=f'L{level}-elastic-complete')
        if level == 3:
            partials, partial_diagnostic = generate(instance, level=level, seed=seed,
                budget_s=max(1., work['proposal_s']-(monotonic()-before)), partial=True, reference=reference, bank=bank)
            diagnostic['partial_search'] = partial_diagnostic
            for plan in partials:
                bank.observe(plan, source='L3-elastic-partial')
        pool = sorted((r for r in bank.rows if r['analysis']['level'] is not None), key=bank.rank)
        complete_pool = [r for r in pool if r['analysis']['ashore_count'] == 0]
        if level >= 4 and complete_pool:
            pool = complete_pool
        elif level == 3:
            partial_pool = [r for r in pool if r['analysis']['ashore_count'] > 0]
            pool = complete_pool[:1] + partial_pool[:1]
        finish_started = monotonic()
        for i, row in enumerate(pool[:work['max_candidates']]):
            left = work['finish_s']-(monotonic()-finish_started)
            if left <= 0:
                break
            finish_candidate(instance, row['plan'], bank, level=level, seed=seed,
                budget_s=left/max(1, min(len(pool), work['max_candidates'])-i), label=f'L{level}-{i}')
        complete = bank.best_completed(level)
        safe = bank.best_safe_partial()
        trials.append(dict(level=level, elapsed_s=monotonic()-before,
            status='completed' if complete else f'no completed plan found at L{level} within this work',
            candidates=len(candidates), safe_partial_ashore=safe['analysis']['ashore_count'] if safe else None,
            search=diagnostic))
        print('ASSIGNMENT LADDER', instance.id, f'L{level}', trials[-1]['status'], flush=True)
        if complete is not None:
            return _package(instance, bank, complete=complete, searched_level=level,
                            trials=trials, seed=seed)
    return _package(instance, bank, complete=None, searched_level=5, trials=trials, seed=seed)

def propose(instance, *, seed, budget_s, context):
    result, record = solve(instance, seed=seed, budgets={
        'proposal_s': budget_s, 'finish_s': context.get('finalize_s', 120.)})
    context.setdefault('method_metadata', {})['peel'] = record
    if result is None:
        return []
    from stowage.actions import get_action_space
    actions = tuple(get_action_space('boxslot').from_plan(instance, result.plan))
    context['_peel_result'], context['_peel_actions'] = result, actions
    return [actions]

def finish(instance, plan, *, seed, finalize_s, label, context):
    from stowage.actions import get_action_space
    actions = tuple(get_action_space('boxslot').from_plan(instance, plan))
    if actions != context.get('_peel_actions'):
        raise ValueError('finish requires unchanged propose actions')
    return context.get('_peel_result'), {'reused_peel_finish': True,
        'peel': context.get('method_metadata', {}).get('peel')}
