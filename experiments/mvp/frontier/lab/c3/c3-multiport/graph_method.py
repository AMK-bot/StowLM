"""Mixed pages with per-call restows and a port-to-hatch activation sequence.

The frozen page constructor supplies physical pattern legality and knapsack
pricing. This file adds circle-MWIS pricing seeds and replaces its two-port
integer objective with exact union accounting at arbitrarily many ports.
All returned candidates are ranked by unchanged real move replay.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import random
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from alns import Sequencer
from frozen_engine.e9fix import install
from frozen_engine.scout.pages import Model as PageModel
from frozen_engine.scout.lash_oracle import Fast
from graph_model import assignment_bound, circle_mwis, load_chords, pattern_events
from ortools.sat.python import cp_model
from stowage.moves import apply_plan
from stowage.actions.space import Place
from stowage.solver.gold_warmstart_search import precedence_sets
from stowage.checker.rules_weight import e6_departure_violations


def e6_rejections(instance, ship):
    """Exact completed-stow E6 failures; this is global, never a unary gate."""
    return [v for v in e6_departure_violations(instance, ship, call_index=instance.call_index)
            if str(v.rule_id) == 'expert.e6.heavy_under_deck']


def graph_order(problem, assignment):
    """Precedence-feasible order with PACE-style pairwise crossing preference.

    For two currently free boxes b,c, loading b first creates an endpoint
    crossing exactly when its discharge precedes c's. Minimise that pairwise
    regret after yard blockers; favour furthest discharge on ties (Belady
    inspiration). Cache-optimal spilling and SSA chordality do not transfer to
    these capacitated physical pages, so this is only a replay-scored heuristic.
    """
    actions = [Place(b, s) for b, s in assignment.items()]
    predecessors = precedence_sets(problem.instance, actions)
    pending, done, taken, output = set(range(len(actions))), set(), set(), []
    while pending:
        ready = [i for i in pending if predecessors[i] <= done]
        if not ready:
            return None
        def priority(i):
            b = actions[i].box
            yard = problem.instance_yard.get(problem.yard_stack.get(b), ())
            above = yard[yard.index(b)+1:] if b in yard else ()
            blockers = sum(c not in taken for c in above)
            crossing = sum(problem.pod(b) < problem.pod(actions[j].box) for j in pending if j != i)
            return blockers, crossing, -problem.pod(b), b
        i = min(ready, key=priority)
        output.append(actions[i])
        pending.remove(i)
        done.add(i)
        taken.add(actions[i].box)
    return tuple(output)


class Model(PageModel):
    """Physical pages retain mixed PODs; covers carry a sequence of calls."""

    def __init__(self, *args, **kwargs):
        self._heat_pattern_cache = {}
        super().__init__(*args, **kwargs)

    def _static(self):
        """Defer E8 geometry to the actual stack prefix, retaining mask unary rules.

        Empty-ship placement probes put an unsupported upper box at stack base
        in the checker's actual-height geometry. Such a probe cannot safely
        refuse upper slots that real lower boxes raise clear of a heat source.
        The inherited override only adds that empty-probe E8 filter, so keep
        its underlying native static table and check full patterns below.
        """
        return self.T.static.copy()

    def _heat_ok(self, page, boxes):
        if not any(self.T.heat[b] for b in boxes):
            return True
        key = (page, tuple(boxes))
        if key in self._heat_pattern_cache:
            return self._heat_pattern_cache[key]
        from stowage.checker.rules import placement_violations
        from stowage.model.ship import Placement
        placements = list(self.p.instance.onboard.placements)
        allowed = True
        for i, b in enumerate(boxes):
            slot, box_id = self.T.slot_ids[page.slots[i]], self.T.box_ids[b]
            if self.T.heat[b]:
                ship = self.p.instance.onboard.model_copy(update={'placements': list(placements)})
                found = placement_violations(self.p.instance, ship, self.p.boxes[box_id], slot)
                if any(str(v.rule_id) == 'expert.e8.away_from_heat' for v in found):
                    allowed = False
                    break
            placements.append(Placement.model_construct(container_id=box_id, slot=slot))
        self._heat_pattern_cache[key] = allowed
        return allowed

    def legal(self, page, boxes, *, any_port=False):
        return super().legal(page, boxes, any_port=any_port) and self._heat_ok(page, boxes)

    def lash_ok(self, page, boxes):
        # Inherited balance moves a whole pattern after calling lash_ok rather
        # than legal/add; preserve the same necessary heat gate on that path.
        return self._heat_ok(page, boxes) and super().lash_ok(page, boxes)

    def enumerate_tiny(self, deadline):
        """All legal ordered page subsets for at most six load boxes.

        LP feasibility duals can all be zero after a cheap first fit; exact
        tiny enumeration prevents that degeneracy from freezing its pool.
        Only a deadline interruption can truncate this restricted family.
        """
        if len(self.boxes) > 6:
            return False
        for page_index, page in enumerate(self.pages):
            eligible = [b for b in self.boxes if (self.T.units[b] == 1) == (page.kind == '20')]
            pending = [((), tuple(eligible))]
            while pending:
                if monotonic() >= deadline:
                    return False
                prefix, remaining = pending.pop()
                if len(prefix) >= len(page.slots):
                    continue
                for b in remaining:
                    boxes = prefix + (b,)
                    if self.legal(page, boxes):
                        self.add(page_index, boxes)
                        pending.append((boxes, tuple(c for c in remaining if c != b)))
        return True

    def enrich_circle(self, deadline):
        """MWIS under LP prices, then capacitated pricing on that subset.

        MWIS is exact for the uncapacitated noncrossing relaxation. The
        subsequent rounded knapsack is conservative. Neither step proves
        exhaustive pricing of the capacitated physical page family.
        """
        _, _, prices, _, _ = self.solve_lp(active_boxes=set(self.boxes), active_stacks=set(self.stacks),
                                          hold_full=False, phase=1)
        for pages in self.types.values():
            if monotonic() >= deadline:
                break
            page = self.pages[pages[0]]
            items = [b for b in self.order if (self.T.units[b] == 1) == (page.kind == '20')]
            weights = {b: max(.001, float(prices[self.row[b]])) for b in items}
            _, independent = circle_mwis(load_chords(items, self.pod), weights)
            chosen = set(independent)
            order = [b for b in items if b in chosen]
            for _, boxes in self.price(pages[0], prices, boxes=order, phase=1).values():
                for k in pages:
                    self.add(k, boxes)
            # A physical page is a finite register bank. Make a second seed
            # from valuable boxes; when it overflows/refuses physics, spill the
            # furthest-next-use box out of this page. The master must place
            # that box elsewhere, so nothing is dropped from the load list.
            preferred = set(sorted(items, key=lambda b: (-weights[b], self.pod[b], b))[:len(page.slots)+1])
            spill_seed = [b for b in items if b in preferred]
            while spill_seed and (len(spill_seed) > len(page.slots) or not self.legal(page, spill_seed)):
                victim = max(spill_seed, key=lambda b: (self.pod[b], -weights[b], b))
                spill_seed.remove(victim)
            if spill_seed:
                for k in pages:
                    self.add(k, spill_seed)

    def graph_integer(self, *, seconds, seed, fixed=(), hold_reward=0., forbidden=()):
        """Exact selected-pool master; its bound is NOT a global map bound.

        For each hold-cover/call, y is the OR of chosen hold patterns visiting
        that call. For each selected deck pattern/call its upper boxes are
        charged once if blocked internally OR y opens their cover. This union
        avoids double counting and works at 3, 4, 5 or any number of calls.
        """
        model = cp_model.CpModel()
        x = [model.NewBoolVar(f'p{k}') for k in range(len(self.columns))]
        coverage, stack_use = defaultdict(list), defaultdict(list)
        for k, c in enumerate(self.columns):
            for b in c.boxes:
                coverage[b].append(x[k])
            for s in self.pages[c.page].stacks:
                stack_use[s].append(x[k])
        for b in self.boxes:
            if not coverage[b]:
                return None
            model.AddExactlyOne(coverage[b])
        for variables in stack_use.values():
            model.AddAtMostOne(variables)
        for chosen in forbidden:
            # A known-invalid COMPLETE combination, never an individual deck
            # pattern: other selected pages can repair E6 compatibility.
            model.Add(sum(x[k] for k in chosen) <= len(chosen)-1)

        objective, weights = [], self.p.instance.objective
        scale = 1000
        def coefficient(value):
            return int(round(scale * float(value)))
        restow = coefficient(weights.future_restow * weights.future_restow_lifts_per_box)
        later_hatch = coefficient(2 * weights.future_hatch_cover)
        now_hatch = coefficient(2 * weights.hatch_cover)
        edge = {}
        for cover in sorted({p.cover for p in self.pages if p.cover is not None}):
            hold = [k for k, c in enumerate(self.columns)
                    if not self.pages[c.page].deck and self.pages[c.page].cover == cover]
            used = model.NewBoolVar(f'cover{cover}')
            use_vars = []
            for call in self.ports:
                carrying = [x[k] for k in hold if any(self.pod[b] == call for b in self.columns[k].boxes)]
                y = model.NewBoolVar(f'open{cover}_{call}')
                model.AddMaxEquality(y, carrying or [0])
                edge[cover, call] = y
                use_vars.append(y)
                objective.append(later_hatch * y)
            model.AddMaxEquality(used, use_vars)
            objective.append(now_hatch * used)
        for k, c in enumerate(self.columns):
            page = self.pages[c.page]
            events = pattern_events(c.boxes, self.pod)
            objective.append(restow * len(events) * x[k])
            if not page.deck:
                # Optional feasibility bias, never called a lift or a bound.
                objective.append(-coefficient(hold_reward) * sum(int(self.T.units[b]) for b in c.boxes) * x[k])
                continue
            for call in self.ports:
                remaining = sum(self.pod[b] > call and (call, b) not in events for b in c.boxes)
                if not remaining or (page.cover, call) not in edge:
                    continue
                both = model.NewBoolVar(f'block{k}_{call}')
                model.AddMinEquality(both, [x[k], edge[page.cover, call]])
                objective.append(restow * remaining * both)
        # A heel feasibility preference can outweigh lift differences. This is
        # explicitly a search objective, never reported as a pure lift bound.
        heel = []
        for k, c in enumerate(self.columns):
            page = self.pages[c.page]
            moment = sum(float(self.T.mass[b]) / 1000. * self.p.cells_tcg(self.T.slot_ids[page.slots[i]])
                         for i, b in enumerate(c.boxes))
            heel.append(int(round(moment)) * x[k])
        magnitude = max(1, int(sum(float(self.T.mass[b]) / 1000. for b in self.boxes) * 100))
        deviation = model.NewIntVar(0, magnitude, 'heel')
        model.AddAbsEquality(deviation, sum(heel))
        objective.append(deviation)
        model.Minimize(sum(objective))
        for k in fixed:
            if k < len(x):
                model.AddHint(x[k], 1)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(.01, seconds)
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = seed
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        return dict(status=solver.StatusName(status), chosen=[k for k in range(len(x)) if solver.Value(x[k])],
                    restricted_search_objective=solver.ObjectiveValue()/scale,
                    restricted_search_bound=solver.BestObjectiveBound()/scale,
                    bound_scope='restricted pattern pool plus hold/heel feasibility preference; not global lifts')


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    install()
    problem = context['problem']
    meta = context.setdefault('method_metadata', {})
    meta.update(method='mixed pages; circle-MWIS pricing; per-call hatch/restow union',
                warm_start=False, candidates=[], master=[])
    if instance.onboard.placements:
        meta['skipped'] = 'empty-arrival page family'
        return []
    start = monotonic()
    deadline = start + max(0., budget_s)
    rng = random.Random(seed)
    model = Model(problem)
    archive, replays, seen = [], [], {}

    def retain(model, chosen, label):
        if chosen is None:
            return None, None
        assignment = model.clear_oog(model.assignment(chosen))
        key = tuple(sorted(assignment.items()))
        if key in seen:
            return seen[key]
        if len(assignment) != len(instance.load_list) or problem.mixes_hold_stack(assignment):
            return None, None
        orders = [Sequencer(problem).order(assignment), graph_order(problem, assignment)]
        scored_options = [problem.score(ordered) for ordered in orders if ordered is not None]
        scored_options = [s for s in scored_options if s is not None]
        scored = min(scored_options, key=lambda s: s.objective) if scored_options else None
        if scored is None:
            meta['candidates'].append(dict(label=label, replay_legal=False))
            seen[key] = (None, None)
            return None, None
        bound = assignment_bound(instance, assignment)
        ship = apply_plan(instance, scored.plan).final_state.ship
        rejected = e6_rejections(instance, ship)
        eligible = not rejected
        meta['candidates'].append(dict(label=label, replay_legal=True, lifts=scored.objective,
                                       conditional_bound=bound, e6_eligible=eligible,
                                       e6_rejections=[v.model_dump(mode='json') for v in rejected]))
        replays.append(scored)
        if eligible:
            archive.append(scored)
        seen[key] = (scored, eligible)
        return scored, eligible

    # A cold feasible anchor also supplies GM/draught for securing-aware pages.
    initial = model.first_fit(set(model.boxes), set(model.stacks), rng)
    anchor, _ = retain(model, initial, 'cold_first_fit')
    if monotonic() < deadline:
        root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                              deadline=start+.28*budget_s, max_rounds=60)
        meta['coverage_lp'] = {k: root[k] for k in ('uncovered', 'rounds', 'columns', 'proved')}
        if root['uncovered'] <= 3. and monotonic() < deadline:
            chosen, info = model.dive(deadline=min(deadline, start+.48*budget_s), rng=rng)
            candidate, _ = retain(model, chosen, 'cold_coverage_dive')
            if candidate is not None:
                anchor = candidate
    # Securing-aware pricing is essential on medium maps. Retain the plain
    # pool's feasible candidates, but build a fresh physics-filtered family.
    if anchor is not None and len(instance.load_list) > 30 and monotonic() < deadline:
        ship = apply_plan(instance, anchor.plan).final_state.ship
        model = Model(problem, oracle=Fast(instance, ship), deck_tiers=3, lash_limit=.94, hold_reward=3.)
        model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=1,
                       deadline=min(deadline, start+.66*budget_s), max_rounds=60)
    if len(model.boxes) <= 6:
        meta['tiny_pattern_family_exhaustive'] = model.enumerate_tiny(min(deadline, start+.72*budget_s))
    elif monotonic() < deadline:
        # Feasibility-only LPs can end at objective zero with zero box duals.
        # Price the physical moment/hold/stack objective explicitly so the
        # graph master gets mixed alternatives instead of just its first fit.
        diverse = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=3,
                                 deadline=min(deadline, start+.72*budget_s), max_rounds=30)
        meta['diversity_lp'] = {k: diverse[k] for k in ('uncovered', 'rounds', 'columns', 'proved')}
    if monotonic() < deadline:
        model.enrich_circle(min(deadline, start+.78*budget_s))
    attempts, attempt_limit, forbidden = 0, 3, []
    while monotonic() < deadline-.15 and attempts < attempt_limit:
        seconds = min(10. if len(model.boxes)<40 else 25., (deadline-monotonic())/(attempt_limit-attempts))
        result = model.graph_integer(seconds=seconds, seed=seed+attempts,
                                     hold_reward=0. if attempts == 0 else (1. if attempts == 1 else 4.),
                                     forbidden=forbidden)
        attempts += 1
        if result is None:
            continue
        meta['master'].append({k: v for k, v in result.items() if k != 'chosen'})
        candidate, eligible = retain(model, result['chosen'], f'graph_master_{attempts}')
        if candidate is not None and eligible is False:
            # OOG repair can change the master assignment. A safe master cut
            # needs an E6 witness on the ORIGINAL selected combination too.
            from stowage.model.ship import Placement
            raw = model.assignment(result['chosen'])
            raw_ship = instance.onboard.model_copy(update={'placements': [
                Placement.model_construct(container_id=b, slot=s) for b, s in raw.items()]})
            if e6_rejections(instance, raw_ship):
                forbidden.append(tuple(result['chosen']))
                attempt_limit = min(6, attempt_limit+1)
        balanced, _ = model.balance(result['chosen'], rng, rounds=300)
        retain(model, balanced, f'graph_master_{attempts}_balanced')
    meta.update(columns=len(model.columns), replay_legal_stows=len(replays),
                e6_eligible_stows=len(archive), e6_no_good_cuts=len(forbidden), wall_s=monotonic()-start,
                discharge_ports=len(model.ports), global_sequence_only_restow_lower_bound=0)
    archive.sort(key=lambda s: s.objective)
    return [s.actions for s in archive[:6]]
