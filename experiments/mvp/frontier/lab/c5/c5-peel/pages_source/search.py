"""Cold, bounded page search for the peel ladder.

This module constructs candidates only. The unchanged full checker, called by
the controller, determines the level and the final ordering. Page penalties
are search approximations, never checker substitutes or proofs.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import importlib.util
import math
from pathlib import Path
import random
import sys
from time import monotonic


def _pages():
    """Import only the private engine snapshot, including in standalone tests."""
    here = Path(__file__).resolve().parent
    if 'engine' not in sys.modules:
        spec = importlib.util.spec_from_file_location('engine', here/'engine_snapshot'/'__init__.py',
            submodule_search_locations=[str(here/'engine_snapshot')])
        module = importlib.util.module_from_spec(spec)
        sys.modules['engine'] = module
        spec.loader.exec_module(module)
    if Path(sys.modules['engine'].__file__).resolve().parent != here/'engine_snapshot':
        raise RuntimeError('peel search refuses a live engine import')
    from engine.scout import pages_fast as pages
    return pages


def ashore_key(instance, actions):
    """Exact subset preference: fewest boxes, then TEU, then gross mass."""
    loaded = {a.box for a in actions if hasattr(a, 'slot')}
    boxes = {b.id: b for b in instance.containers}
    left = [boxes[b] for b in instance.load_list if b not in loaded]
    return (len(left), sum(b.length_ft/20 for b in left), sum(b.gross_mass_kg for b in left))


@contextmanager
def _single_highs(pages):
    """Explicit HiGHS thread cap; restore the process-local call in all paths."""
    original = pages.linprog
    def one(*args, **kwargs):
        kwargs['options'] = {**kwargs.get('options', {}), 'threads': 1}
        return original(*args, **kwargs)
    pages.linprog = one
    try:
        yield
    finally:
        pages.linprog = original


def make_model(problem, level, *, oracle=None, lash_limit=1., deck_tiers=None):
    """A private subclass: peeled rules never mutate the original page model."""
    pages = _pages()
    class PeelModel(pages.Model):
        def _static(self):
            # At L3 E8 heat is peeled too; the regular method probes the full
            # checker and would silently restore that removed restriction.
            return self.T.static.copy() if level >= 3 else super()._static()

        def legal(self, page, boxes, *, any_port=False):
            T = self.T
            port = None if any_port else self.port_of(page)
            if port is not None and any(self.pod[b] != port for b in boxes):
                return False
            # Keep these stronger T1/T0 guards even when T1 is peelable.
            if sum(bool(T.is_dg[b]) for b in boxes) > 1:
                return False
            if any(bool(T.closes_stack[b]) for b in boxes[:-1]):
                return False
            if len(boxes) > len(page.slots) or sum(float(T.height[b]) for b in boxes) > page.hmax+1e-9:
                return False
            if level < 5 and sum(float(T.share[b]) for b in boxes) > page.limit_kg+1e-6:
                return False
            if level < 2:
                ranks = [int(T.rank[b]) for b in boxes]
                for i in range(1, len(boxes)):
                    if ranks[i] > min(ranks[:i])+T.e2_tol:
                        return False
                    if self.heavy[boxes[i]] and not all(self.heavy[b] for b in boxes[:i]):
                        return False
            return all(self.static[b, page.slots[k]] for k, b in enumerate(boxes))

        def knapsack(self, page_index, height):
            # L4 securing is soft: the physical stack mass is still enforced.
            # L5 also softens stack mass, while tier/height/fit remain hard.
            if level >= 4:
                page = self.pages[page_index]
                limit = (sum(float(self.T.share[b]) for b in self.boxes)
                         if level >= 5 else page.limit_kg)
                return 240, lambda b, k: math.ceil(240*float(self.T.share[b])/max(1., limit)-1e-9)
            return super().knapsack(page_index, height)

        def lash_ok(self, page, boxes):
            if level >= 4:
                return self.lash_ratio(page, boxes) <= lash_limit
            return super().lash_ok(page, boxes)

        def lash_ratio(self, page, boxes):
            if self.oracle is None or not page.deck:
                return 0.
            return float(self.oracle.ratio([self.T.box_ids[b] for b in boxes],
                [self.T.slot_ids[page.slots[k]] for k in range(len(boxes))]))

        def soft_breach(self, column):
            page = self.pages[column.page]
            securing = max(0., self.lash_ratio(page, column.boxes)-1.) if level >= 4 else 0.
            mass = max(0., sum(float(self.T.share[b]) for b in column.boxes)-page.limit_kg)/1000. if level >= 5 else 0.
            return securing+mass

        def _column_cost(self, column, phase):
            return super()._column_cost(column, phase)+100.*self.soft_breach(column)

        def tail(self, boxes, stacks, *, seconds, seed=0, prefer_high=True):
            # The snapshot tail has only a wall cap. Give its restricted
            # complete-cover master the same explicit deterministic/work caps.
            from types import SimpleNamespace
            use = [k for k, c in enumerate(self.columns) if set(c.boxes) <= boxes
                   and set(self.pages[c.page].stacks) <= stacks]
            view = SimpleNamespace(boxes=list(boxes), pages=self.pages, T=self.T,
                columns=[self.columns[k] for k in use], soft_breach=self.soft_breach,
                column_cost=self.column_cost)
            chosen, _ = integer_partial(view, allow_partial=False,
                seconds=max(0., min(10., seconds)), seed=seed)
            return [use[k] for k in chosen] if chosen is not None else None

    return PeelModel(problem, oracle=oracle, lash_limit=min(1., lash_limit), deck_tiers=deck_tiers,
                     hold_reward=0. if level >= 1 else 1.)


def integer_partial(model, *, allow_partial, seconds, seed, conflicts=1000000,
                    deterministic=30., hint_chosen=None):
    """Restricted master with per-box slack, solved lexicographically.

    A sequence of objectives avoids overflow-prone artificial big-M weights.
    Each proven minimum is fixed before moving to its next tie break. If work
    expires, the last incumbent is returned without claiming an optimum.
    """
    from ortools.sat.python import cp_model
    cp = cp_model.CpModel()
    x = [cp.NewBoolVar(f'page{k}') for k in range(len(model.columns))]
    cover, use = defaultdict(list), defaultdict(list)
    for k, column in enumerate(model.columns):
        for b in column.boxes:
            cover[b].append(x[k])
        for stack in model.pages[column.page].stacks:
            use[stack].append(x[k])
    slack = {}
    for b in model.boxes:
        if allow_partial:
            slack[b] = cp.NewBoolVar(f'ashore{b}')
            cp.Add(sum(cover[b])+slack[b] == 1)
        else:
            cp.AddExactlyOne(cover[b])
    for terms in use.values():
        cp.Add(sum(terms) <= 1)
    # Always keep a concrete feasible subset before numerical search. A short
    # wall allowance can otherwise expire in presolve without returning the
    # readily available partial packing. The all-ashore solution is feasible,
    # but prefer a greedily covered subset as the hint and fallback incumbent.
    remaining, free = set(model.boxes), {s for p in model.pages for s in p.stacks}
    greedy = []
    for k in sorted(range(len(model.columns)),
                    key=lambda k: (-len(model.columns[k].boxes), model.columns[k].page, k)):
        column = model.columns[k]
        stacks = set(model.pages[column.page].stacks)
        if set(column.boxes) <= remaining and stacks <= free:
            greedy.append(k)
            remaining.difference_update(column.boxes)
            free.difference_update(stacks)
    feasible_hint = allow_partial or not remaining
    if hint_chosen is not None:
        hint_boxes, hint_stacks = set(), set()
        valid = True
        for k in hint_chosen:
            if k < 0 or k >= len(model.columns):
                valid = False
                break
            column = model.columns[k]
            stacks = set(model.pages[column.page].stacks)
            if hint_boxes.intersection(column.boxes) or hint_stacks.intersection(stacks):
                valid = False
                break
            hint_boxes.update(column.boxes)
            hint_stacks.update(stacks)
        if valid and hint_boxes == set(model.boxes):
            greedy, remaining, feasible_hint = list(hint_chosen), set(), True
    if feasible_hint:
        selected = set(greedy)
        for k, variable in enumerate(x):
            cp.AddHint(variable, int(k in selected))
        for b, variable in slack.items():
            cp.AddHint(variable, int(b in remaining))
    T = model.T
    objectives = ([sum(slack.values()),
        sum(int(T.units[b])*slack[b] for b in model.boxes),
        sum(round(1000*float(T.mass[b]))*slack[b] for b in model.boxes)] if allow_partial else [0]) + [
        sum(round(10000*model.soft_breach(c))*x[k] for k, c in enumerate(model.columns)),
        sum(round(100*model.column_cost(c, 3))*x[k] for k, c in enumerate(model.columns))]
    deadline = monotonic()+max(0., seconds)
    chosen, proved, statuses = (greedy if feasible_hint else None), [], []
    for objective in objectives:
        left = deadline-monotonic()
        if left <= 0:
            break
        cp.Minimize(objective)
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = seed
        solver.parameters.max_time_in_seconds = left
        solver.parameters.max_number_of_conflicts = conflicts
        solver.parameters.max_deterministic_time = deterministic
        status = solver.Solve(cp)
        statuses.append(solver.StatusName(status))
        if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            break
        chosen = [k for k in range(len(x)) if solver.Value(x[k])]
        if status != cp_model.OPTIMAL:
            break
        value = solver.Value(objective) if not isinstance(objective, int) else objective
        proved.append(value)
        cp.Add(objective == value)
    return chosen, {'lex_fixed': proved, 'columns': len(model.columns),
                    'fallback_subset': feasible_hint and allow_partial,
                    'fallback_complete': feasible_hint and not remaining,
                    'fallback_ashore': len(remaining), 'allow_partial': allow_partial,
                    'cp_conflicts': conflicts, 'cp_deterministic': deterministic,
                    'cp_status': statuses}


def _same_run_columns(model, previous, chosen, *, deadline, limit=4000):
    """Copy only invocation-local concrete columns through the current gates."""
    if previous is None:
        return None, 0
    where = {(p.kind, p.stacks): k for k, p in enumerate(model.pages)}
    wanted = list(chosen or ())
    order = wanted+list(range(max(0, len(previous.columns)-limit), len(previous.columns)))
    mapping, seen = {}, set()
    for old_index in order:
        if monotonic() >= deadline:
            break
        if old_index in seen:
            continue
        seen.add(old_index)
        column = previous.columns[old_index]
        old_page = previous.pages[column.page]
        page_index = where.get((old_page.kind, old_page.stacks))
        if page_index is None:
            continue
        # add() independently checks the new static, height, mass and securing
        # restrictions. A prior complete plan is never trusted across models.
        model.add(page_index, column.boxes)
        new_index = model.pool.get((page_index, column.boxes))
        if new_index is not None:
            mapping[old_index] = new_index
    hint = [mapping[k] for k in wanted] if wanted and all(k in mapping for k in wanted) else None
    return hint, len(mapping)


def _complete_seed(model, *, seed, deadline, hint=None, max_nodes=80):
    """Build remaining-box columns by bounded diving, preserving a full seed."""
    if hint is not None and {b for k in hint for b in model.columns[k].boxes} == set(model.boxes):
        return hint, {'reason': 'same-invocation complete hint', 'nodes': 0}
    first = model.first_fit(set(model.boxes), set(model.stacks), random.Random(seed))
    if first is not None:
        return first, {'reason': 'complete first-fit', 'nodes': 0}
    original = model.generate
    nodes = 0
    class NodeLimit(Exception):
        pass
    def bounded(*args, **kwargs):
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise NodeLimit()
        return original(*args, **kwargs)
    model.generate = bounded
    try:
        chosen, row = model.dive(deadline=deadline, rng=random.Random(seed),
                                 rounds=5, tail_boxes=12, retreats=4)
        return chosen, dict(row, nodes=nodes)
    except NodeLimit:
        return None, {'reason': 'complete dive node work exhausted', 'nodes': max_nodes}
    finally:
        model.generate = original


def _invocation_seeds(problem, seeds):
    """Accept complete in-memory plans/actions, never a path or saved record."""
    from stowage.model import MovePlan
    from alns import Scored
    out = []
    for seed in seeds:
        if isinstance(seed, Scored):
            scored = problem.score(seed.actions)
        elif isinstance(seed, MovePlan):
            try:
                scored = problem.score(problem.space.from_plan(problem.instance, seed))
            except (ValueError, KeyError, TypeError):
                continue
        elif isinstance(seed, (tuple, list)):
            scored = problem.score(seed)
        else:
            continue
        if scored is not None and ashore_key(problem.instance, scored.actions)[0] == 0:
            out.append(scored)
    return out


def _score_subset(problem, model, chosen, seed):
    from alns import Sequencer
    chosen, _ = model.balance(chosen, random.Random(seed), rounds=500)
    assignment = model.assignment(chosen)
    # The frozen OOG repair loops over all model.boxes. Restrict its local view
    # to the actual subset, restore even if a checker probe raises.
    original = model.boxes
    model.boxes = [b for b in original if model.T.box_ids[b] in assignment]
    try:
        assignment = model.clear_oog(assignment)
    finally:
        model.boxes = original
    actions = Sequencer(problem).order(assignment)
    return problem.score(actions) if actions is not None else None


def generate(instance, level, seed, budget_s, context):
    """Return replay-legal Scored candidates; never read earlier run artifacts.

    Fixed work caps: three models, 40+20 root pricing rounds/model, an
    80-node complete dive with 5 pricing rounds/node and 4 retreats, 1,000,000 CP
    conflicts/objective, 30 deterministic seconds/objective, 500 heel swaps.
    Each master gets a minimum 10-second wall allowance; atomic work may
    exceed the proposal stop. A 120-second proposal supports all three models.
    context['complete_only'] forbids ashore variables and partial fallbacks;
    it defaults true at L4/L5. No safety failure discards a complete stow here.
    context['complete_seeds'] may hold complete Scored/MovePlan/action tuples
    obtained earlier in this invocation; paths and serialized records refuse.
    """
    if not 1 <= level <= 5:
        raise ValueError('relaxed generator requires L1..L5')
    if instance.onboard.placements or instance.discharge_list:
        context.setdefault('method_metadata', {})['peel_search_skip'] = 'empty-arrival page family only'
        return []
    pages = _pages()
    from alns import Problem
    from stowage.moves import apply_plan
    from engine.scout.lash_oracle import Fast
    problem = context.get('problem') or Problem(instance)
    deadline = monotonic()+max(0., budget_s)
    rows = context.setdefault('method_metadata', {}).setdefault('peel_search', [])
    candidates = _invocation_seeds(problem, context.get('complete_seeds', ()))
    oracle = None
    if candidates:
        oracle = Fast(instance, apply_plan(instance, candidates[0].plan).final_state.ship)
    complete_only = bool(context.get('complete_only', level >= 4))
    previous, previous_chosen = None, None
    # First get a cold reference. Subsequent securing limits tighten from above.
    limits = (math.inf, 1.3, 1.05) if level >= 4 else (1., 1., 1.)
    with _single_highs(pages):
        for attempt, limit in enumerate(limits):
            if monotonic() >= deadline:
                break
            share = max(.01, (deadline-monotonic())*(.45 if attempt == 0 else
                                                    1./(len(limits)-attempt)))
            local_deadline = min(deadline, monotonic()+share)
            model = make_model(problem, level, oracle=oracle, lash_limit=limit,
                               deck_tiers=None if attempt < 2 else 4)
            started = monotonic()
            try:
                hint, copied = _same_run_columns(model, previous, previous_chosen,
                    deadline=min(local_deadline, started+2.))
                if candidates:
                    from engine.benders.seed import seed_patterns
                    for candidate in candidates:
                        if ashore_key(instance, candidate.actions)[0] == 0:
                            seeded = seed_patterns(model, candidate.plan)
                            if seeded is not None:
                                hint = seeded
                                break
                root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                    deadline=started+max(.2, share-10.)*.6, phase=1, max_rounds=40)
                if monotonic() < local_deadline-10.:
                    model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                        deadline=local_deadline-10., phase=3, max_rounds=20)
                dive_row = {'reason': 'no complete-dive allowance'}
                if monotonic() < local_deadline-10. and root['uncovered'] <= 3.:
                    complete_hint, dive_row = _complete_seed(model, seed=seed+attempt,
                        deadline=local_deadline-10., hint=hint)
                    if complete_hint is not None:
                        hint = complete_hint
                chosen, info = integer_partial(model,
                    allow_partial=level >= 3 and not complete_only,
                    seconds=max(10., local_deadline-monotonic()), seed=seed+attempt,
                    hint_chosen=hint)
                row = dict(level=level, attempt=attempt, lp_uncovered=root['uncovered'],
                           securing_upper=None if math.isinf(limit) else limit,
                           complete_only=complete_only, copied_same_run_columns=copied,
                           complete_dive=dive_row, **info)
                if chosen is not None:
                    scored = _score_subset(problem, model, chosen, seed+attempt)
                    if scored is not None:
                        candidates.append(scored)
                        row.update(ashore=list(ashore_key(instance, scored.actions)), lifts=scored.objective)
                        if oracle is None:
                            oracle = Fast(instance, apply_plan(instance, scored.plan).final_state.ship)
                previous, previous_chosen = model, chosen
                rows.append(row)
            except Exception as error:
                rows.append(dict(level=level, attempt=attempt, error=f'{type(error).__name__}: {error}'))
    unique = {tuple((a.box, a.slot) for a in s.actions): s for s in candidates}
    return sorted(unique.values(), key=lambda s: (ashore_key(instance, s.actions), s.objective))
