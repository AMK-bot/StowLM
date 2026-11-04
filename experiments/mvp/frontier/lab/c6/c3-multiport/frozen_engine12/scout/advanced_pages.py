# Provenance: combines lab/c2/fx-scout/pages.py and lab/c3/c2-layout/pages{,_lp}.py with engine fast pricing.
"""Pure/mixed page models with count-limited construction and a safety deadline.

Restrictions here select a constructor family. They never certify map
infeasibility or replace replay and the full checker.
"""
from dataclasses import dataclass, replace
from time import monotonic

import numpy as np
from ortools.sat.python import cp_model

from .closed_pages_lp import Model as ClosedPure
from .mixed_pages import Model as MixedBase
from .pages_fast import Model as FastModel
from .pages import Model as SlowPricing
from .rmp import restricted_master


@dataclass(frozen=True)
class Work:
    lp_rounds: int = 40
    polish_rounds: int = 8
    dive_nodes: int = 80
    cp_conflicts: int = 10000
    cp_deterministic: float = 1.


class WorkExhausted(Exception):
    pass


def configure_solver(solver, work, deadline, seed=0):
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = max(.001, deadline-monotonic())
    solver.parameters.max_number_of_conflicts = work.cp_conflicts
    solver.parameters.max_deterministic_time = work.cp_deterministic


class Counted:
    def generate(self, *, deadline, phase=1, max_rounds=400, **kwargs):
        deadline = min(deadline, self.safety_deadline)
        if getattr(self, '_in_dive', False):
            self._dive_nodes += 1
            if self._dive_nodes > self.work.dive_nodes:
                raise WorkExhausted()
        limit = self.work.lp_rounds if phase == 1 else self.work.polish_rounds
        base = getattr(self, '_generate_base', super().generate)
        root = base(deadline=deadline, phase=phase,
                    max_rounds=min(max_rounds, limit), **kwargs)
        full_root = (set(kwargs['active_boxes']) == set(self.boxes)
                     and set(kwargs['active_stacks']) == set(self.stacks))
        if (self.use_root_master and phase == 1 and full_root
                and root['uncovered'] <= 1e-7 and self.seed_chosen is None):
            chosen, row = restricted_master(self, root['x'], deadline=deadline,
                seconds=max(0., deadline-monotonic()), cuts=self.page_cuts, work=self.work)
            self.rmp_history = getattr(self, 'rmp_history', [])+[row]
            if chosen is not None:
                self.seed_chosen = chosen
        return root

    def _seed_columns_valid(self, chosen):
        boxes, stacks = set(), set()
        for k in chosen:
            c = self.columns[k]
            page = self.pages[c.page]
            if (k in self.banned or boxes.intersection(c.boxes)
                    or stacks.intersection(page.stacks)
                    or not self.legal(page, c.boxes) or not self.lash_ok(page, c.boxes)):
                return False
            boxes.update(c.boxes)
            stacks.update(page.stacks)
        return boxes == set(self.boxes) and self.respects_cuts(chosen)

    def _valid_seed(self, chosen):
        if not self._seed_columns_valid(chosen):
            return False
        return (self.lcg_target is None or
                sum(self.lcg_moment(self.columns[k]) for k in chosen) <= 1e-6)

    def dive(self, *, deadline, **kwargs):
        self._dive_nodes, self._in_dive = 0, True
        try:
            if self.seed_chosen is not None:
                if self._valid_seed(self.seed_chosen):
                    return list(self.seed_chosen), dict(left=0, dive_nodes=0, tail='checked_seed')
                self.seed_chosen = None
            chosen, row = super().dive(deadline=min(deadline, self.safety_deadline), **kwargs)
            row['dive_nodes'] = self._dive_nodes
            if chosen is not None and not self.respects_cuts(chosen):
                return None, dict(row, reason='certified page cut')
            return chosen, row
        except WorkExhausted:
            return None, dict(left=len(self.boxes), dive_nodes=self._dive_nodes-1,
                              reason='fixed dive node limit')
        finally:
            self._in_dive = False

    def tail(self, boxes, stacks, *, seconds, seed=0, **kwargs):
        # The deadline is a safety guard; conflicts/deterministic solver work are
        # the ordinary stopping rule, independent of machine throughput.
        model = cp_model.CpModel()
        use = [k for k, c in enumerate(self.columns) if k not in self.banned
               and set(c.boxes) <= boxes and set(self.pages[c.page].stacks) <= stacks]
        x = {k: model.NewBoolVar(f'x{k}') for k in use}
        for box in sorted(boxes):
            covering = [x[k] for k in use if box in self.columns[k].boxes]
            if not covering:
                return None
            model.AddExactlyOne(covering)
        for stack in sorted(stacks):
            model.AddAtMostOne([x[k] for k in use if stack in self.pages[self.columns[k].page].stacks])
        model.Minimize(sum(int(100*self.column_cost(self.columns[k], 3))*x[k] for k in use))
        solver = cp_model.CpSolver()
        configure_solver(solver, self.work, self.safety_deadline, seed)
        if solver.Solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        return [k for k in use if solver.Value(x[k])]


class PureModel(Counted, ClosedPure):
    def __init__(self, problem, *, work=Work(), safety_deadline=float('inf'),
                 fast_pricing=True, **kwargs):
        self.work, self.safety_deadline = work, safety_deadline
        self.use_fast_pricing = fast_pricing
        self.roofs = kwargs.pop('roofs', None) or {}
        self.use_root_master = kwargs.pop('restricted_master', True)
        # Counted supplies the count-limited RMP instead of the 0.11 timed RMP.
        super().__init__(problem, restricted_master=False, **kwargs)

    def _capped(self, page):
        page = super()._capped(page)
        if page.deck and self.roofs:
            room = min(self.roofs.get(j, float('inf'))-float(self.T.stack_base_z[j])
                       for j in page.stacks)
            hmax = max(0., min(page.hmax, room))
            tiers = max(1, int((hmax+1e-9)//2.591))
            page = replace(page, hmax=hmax, slots=page.slots[:tiers])
        return page

    def price(self, *args, **kwargs):
        method = FastModel.price if self.use_fast_pricing else SlowPricing.price
        return method(self, *args, **kwargs)


class MixedModel(Counted, MixedBase):
    def __init__(self, problem, *, work=Work(), safety_deadline=float('inf'),
                 fast_pricing=True, unary_cache=True, asymmetric_deck=True,
                 restricted_master=True, aggregation=True, closed_hold=(),
                 deck_tiers=None, roofs=None, mixed=True, reserve=True, **kwargs):
        self.work, self.safety_deadline = work, safety_deadline
        self.use_fast_pricing, self.unary_cache = fast_pricing, unary_cache
        self.asymmetric_deck, self.aggregate_tiers = asymmetric_deck, aggregation
        self.use_root_master = restricted_master
        self.closed_hold = frozenset(closed_hold)
        self.page_cuts = []
        self.deck_tiers = deck_tiers
        self.ports = sorted({problem.pod(b) for b in problem.instance.load_list})
        super().__init__(problem, roofs=roofs, mixed=mixed, reserve=reserve, **kwargs)
        self.pricing_cache, self.master_matrix = {}, None
        self.master_matrix_size, self.seed_chosen = 0, None

    def _static(self):
        cache_name = '_engine_advanced_exact_unary'
        out = getattr(self.p, cache_name, None) if self.unary_cache else None
        self.unary_cache_hit = out is not None
        if out is None:
            # Use the differentially checked exact E8 adapter, not C2's fitted
            # standard-height heat rectangles. Cache before layout restrictions.
            out = FastModel._static(self)
            if self.unary_cache:
                setattr(self.p, cache_name, out.copy())
        out = out.copy()
        if self.closed_hold:
            for s in range(len(self.T.slot_ids)):
                if any(not self.T.stack_deck[int(j)] and self.T.stack_cover[int(j)] in self.closed_hold
                       for j in self.T.slot_stacks[s] if j >= 0):
                    out[:, s] = False
            if self.T.e6_threshold is not None:
                for b in self.boxes:
                    box = self.p.boxes[self.T.box_ids[b]]
                    if (self.T.mass[b] >= self.T.e6_threshold and not box.dg
                            and not box.oog and not box.special_stowage):
                        out[b, self.T.slot_deck] = False
        return out

    def port_of(self, page):
        port = self.layout.get(page.cover)
        if page.deck:
            if page.cover in self.closed_hold:
                return None
            if self.layout_deck == 'no_restow':
                if self.asymmetric_deck:
                    return port if port is not None and port < max(self.ports) else None
                return port
            if not self.layout_deck:
                return None
        return port

    def _capped(self, page):
        page = super()._capped(page)
        if page is not None and page.deck and self.deck_tiers:
            caps = self.deck_tiers if isinstance(self.deck_tiers, (tuple, list)) else (self.deck_tiers,)*3
            from stowage.conventions import parse_slot
            bays = sorted({parse_slot(slot).bay for i, slot in enumerate(self.T.slot_ids)
                           if self.T.slot_deck[i] and self.T.slot_n[i] == 1})
            bay = parse_slot(self.T.slot_ids[page.slots[0]]).bay
            part = min(2, sum(b < bay for b in bays)*3//max(1, len(bays)))
            page = self._cut(page, page.hmax, caps[part])
        return page

    def respects_cuts(self, chosen):
        selected = set(chosen)
        return all(len(selected.intersection(cut['columns'])) <= cut['rhs']
                   for cut in self.page_cuts if cut.get('certified'))

    _pricing = FastModel._pricing
    _groups = FastModel._groups
    _flat_price = FastModel._flat_price
    solve_lp = FastModel.solve_lp
    generate = Counted.generate

    def price(self, page_index, *args, **kwargs):
        if self.pages[page_index].kind == 'mixed':
            return MixedBase.price(self, page_index, *args, **kwargs)
        method = FastModel.price if self.use_fast_pricing else SlowPricing.price
        return method(self, page_index, *args, **kwargs)

    # Fast generation uses the same page interface and concrete box identities;
    # mixed pricing remains the inherited ordered-position recurrence.
    def _generate_base(self, **kwargs):
        return FastModel.generate(self, **kwargs)
