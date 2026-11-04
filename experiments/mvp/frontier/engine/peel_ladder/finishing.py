# Copied from experiments/mvp/frontier/lab/c5/c5-peel/finishing.py; frozen body unchanged.
"""Retain rejected candidates without changing any checker verdict or engine gate."""
from contextlib import contextmanager
from time import monotonic
from stowage.checker import check
from stowage.model import BallastCondition
from tiers import analyze_report, rank_report

class Candidates:
    def __init__(self, instance):
        self.instance = instance
        self.rows = []
        self.seen = set()
        self.best = {}
        self.finishing_records = []

    def rank(self,row):
        key=rank_report(row['report'],instance=self.instance)
        # Preserve unknown totals; compare available measured components before
        # lifts so a co-occurring qualitative booking cannot hide securing.
        return (*key[:5],row['analysis']['measured_breach_sum'],key[5])

    def best_completed(self,level):
        rows=[r for r in self.rows if r['analysis']['level'] is not None
              and r['analysis']['level']<=level and r['analysis']['ashore_count']==0]
        return min(rows,key=self.rank) if rows else None

    def best_safe_partial(self):
        rows=[r for r in self.rows if r['analysis']['level'] is not None
              and r['analysis']['level']<=3 and r['analysis']['ashore_count']>0]
        return min(rows,key=self.rank) if rows else None

    def observe(self, plan, report=None, source='candidate'):
        if plan is None:
            return None
        key = plan.model_dump_json()
        if key in self.seen:
            return None
        self.seen.add(key)
        report = check(self.instance, plan) if report is None else report
        analysis = analyze_report(report,instance=self.instance)
        row = dict(plan=plan, report=report, analysis=analysis, source=source)
        self.rows.append(row)
        level = analysis['level']
        if level is not None:
            for allowed in range(level, 6):
                old = self.best.get(allowed)
                if old is None or self.rank(row) < self.rank(old):
                    self.best[allowed] = row
        return row

@contextmanager
def retain_failures(bank):
    """Observe ordinary lanes; original success/failure returns are untouched."""
    from runtime import load_engine
    load_engine()
    from engine.finishing import chain
    original_checked = chain._checked
    original_lp = chain.solve_ballast
    original_native = chain.frozen_planner._finish
    def checked(instance, result, *, seed):
        accepted, report = original_checked(instance, result, seed=seed)
        plan = getattr(result, 'plan', result)
        if plan is not None:
            bank.observe(plan, report=report, source='ordinary-finish-check')
        return accepted, report
    def lp(instance, plan, **kwargs):
        result = original_lp(instance, plan, **kwargs)
        if result.get('candidate_plan') is not None:
            bank.observe(result['candidate_plan'], source='ordinary-ballast-lp')
        return result
    def native(instance, plan, **kwargs):
        bank.observe(plan, source='ordinary-native-input')
        result, record = original_native(instance, plan, **kwargs)
        if result is not None:
            bank.observe(result.plan, source='ordinary-native-result')
        return result, record
    chain._checked, chain.solve_ballast, chain.frozen_planner._finish = checked, lp, native
    try:
        yield bank
    finally:
        chain._checked, chain.solve_ballast, chain.frozen_planner._finish = original_checked, original_lp, original_native

def finish_relaxed(instance, plan, bank, *, level, budget_s, seed=0, max_recipes=9):
    """Rank real full-checker breaches across bounded physical ballast recipes."""
    from stowage.solver.ballast_v4_seeds import trim_seed_recipes
    deadline = monotonic()+budget_s
    bank.observe(plan, source=f'L{level}-raw')
    # Ordinary finishing with real ballast LP, area refinement and native V4.
    # Every rejected lane is retained; the full checker verdict is untouched.
    from runtime import load_engine
    load_engine()
    try:
        with retain_failures(bank):
            if len(instance.load_list)>60:
                from engine.finishing import finish_chain
                result,metadata=finish_chain(instance,plan,seed=seed,finalize_s=budget_s,
                    label=f'L{level}-real-finish',securing=False,inherited=False,
                    area_securing_s=max(1.,budget_s*.4))
            else:
                # The relaxed lane uses the requested native V4 work; calling the
                # portfolio chain would silently impose a new 300-second floor.
                from engine.frozen import planner
                result,metadata=planner._finish(instance,plan,seed=seed,
                    finalize_s=budget_s,label=f'L{level}-real-finish')
            if result is not None:bank.observe(result.plan,source=f'L{level}-real-finished')
    except (ValueError,RuntimeError,ArithmeticError,KeyError,OSError) as error:
        metadata=dict(status='finishing_error',error=f'{type(error).__name__}: {error}')
    bank.finishing_records.append(dict(level=level,budget_s=budget_s,
        elapsed_s=monotonic()-(deadline-budget_s),metadata=metadata))
    # Explicit finite recipe/tightening work uses any remaining finishing work.
    # Every recipe is checked in the unchanged instance. No policy is softened.
    recipes = trim_seed_recipes(instance)
    count = 0
    for recipe in recipes:
        if count >= max_recipes or monotonic() >= deadline:
            break
        candidate = plan.model_copy(update={'ballast': BallastCondition(
            tank_masses_kg=recipe.tank_masses_kg, source='finisher')})
        bank.observe(candidate, source=f'L{level}-ballast-{recipe.label}')
        count += 1
    # Coordinate tightening in ballast space: finite proposals, measured breach
    # ranking; allows reducing a T2/T1 breach instead of silently ignoring it.
    if level >= 4 and monotonic() < deadline:
        best = bank.best.get(level)
        if best is not None and best['plan'].ballast is not None:
            masses = dict(best['plan'].ballast.tank_masses_kg)
            for factor in (.9, 1.1):
                if monotonic() >= deadline:
                    break
                candidate = best['plan'].model_copy(update={'ballast': BallastCondition(
                    tank_masses_kg={k:v*factor for k,v in masses.items()}, source='finisher')})
                bank.observe(candidate, source=f'L{level}-tighten-{factor}')
    return bank.best.get(level)

@contextmanager
def capture_checked():
    """Record already-computed reports with no extra check or ranking in L0."""
    from runtime import load_engine
    load_engine()
    from engine.finishing import chain
    from engine import finishing as public_finishing
    original=chain._checked
    original_finish=public_finishing.finish_chain
    captured=[]
    def checked(instance,result,*,seed):
        accepted,report=original(instance,result,seed=seed)
        plan=getattr(result,'plan',result)
        if plan is not None and report is not None:
            captured.append((plan,report))
        return accepted,report
    def finish(instance,plan,**kwargs):
        captured.append((plan,None))
        return original_finish(instance,plan,**kwargs)
    chain._checked=checked
    public_finishing.finish_chain=finish
    try:
        yield captured
    finally:
        chain._checked=original
        public_finishing.finish_chain=original_finish
