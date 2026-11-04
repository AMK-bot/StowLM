# Copied from experiments/mvp/frontier/lab/c5/c5-peel/assignment_masks.py; frozen body unchanged.
"""Scoped assignment-model peeling. The full checker and vendor sources stay intact.

Construct fresh Problem/Model objects inside the context. Visibility frames stay
available to tagged LP rows; peeled visibility never prunes assignment edges or
rejects a column through Model.legal before the elastic LP can price its breach.
"""
from contextlib import contextmanager
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import inspect

import numpy as np
import stowage.feasibility._engine as native

from masks import peeled_masks, peeled_rule_ids, E1, E2
from stowage.checker import rules, rules_special
from stowage.conventions import cells_of_slot
from stowage.model import Placement

VENDOR = Path(__file__).resolve().parent/'assignment_vendor'
VISIBILITY = 'solas.v22.visibility'
STACK_WEIGHT = 'expert.physical.stack_weight'
DG = 'imdg.segregation.7_2_4'
HEAT = 'expert.e8.away_from_heat'


def _static_with_peeled_heat(model, peeled):
    """Drop the booking, retaining missing heat/geometry evidence as T0."""
    T, p = model.T, model.p
    out = T.static.copy()
    ship = p.instance.onboard
    _, _, usable_cells, problems = rules._geometry_index(p.instance)
    direct = not ship.placements and not problems
    valid_ids = {box.id for box in p.instance.containers}
    for b in model.boxes:
        if not T.heat[b]:
            continue
        box = p.boxes[T.box_ids[b]]
        for s in np.flatnonzero(out[b]):
            slot = T.slot_ids[s]
            if direct and all(cid in usable_cells for cid in cells_of_slot(slot)):
                candidate = ship.model_copy(update={'placements': [
                    Placement.model_construct(container_id=box.id, slot=slot)]})
                found = rules_special.special_violations(p.instance,
                    rules._usable_ship(candidate, valid_ids, usable_cells),
                    call_index=p.instance.call_index, focus_container_id=box.id)
            else:
                found = rules.placement_violations(p.instance, ship, box, slot)
            # Original static heat mask owns this booking and its evidence,
            # rather than unrelated support conditions of an empty-ship probe.
            if any(v.severity == 'hard' and (v.rule_id == 'checker.missing_vessel_data'
                   or (v.rule_id == HEAT and HEAT not in peeled)) for v in found):
                out[b, s] = False
    return out


@contextmanager
def assignment_masks(level, *, model_cls=None):
    """Peel only table-authorised restrictions, restoring class methods finally.

    ``model_cls`` is the privately loaded vendor pages_sight.Model. Passing it
    avoids importing any ambient ``pages`` module belonging to another lane.
    L0 is an exact no-op, including class identities and numerical matrices.
    """
    peeled = peeled_rule_ids(level)
    if not peeled:
        yield
        return
    if model_cls is None:
        import pages_sight
        model_cls = pages_sight.Model
    path = Path(inspect.getfile(model_cls)).resolve()
    if not path.is_relative_to(VENDOR):
        raise RuntimeError(f'assignment masks refuse non-vendor Model: {path}')
    original_init, original_static = model_cls.__init__, model_cls._static
    original_build = native.build_tables
    original_clear_oog = getattr(model_cls, 'clear_oog', None)
    names_to_restore = ['__init__', '_static', 'legal']
    if original_clear_oog is not None:
        names_to_restore.append('clear_oog')
    originals = {name: model_cls.__dict__.get(name) for name in names_to_restore}

    def static(self):
        out = (_static_with_peeled_heat(self, peeled) if HEAT in peeled
               else original_static(self))
        self._assignment_before_sight = out.copy()
        return out

    def init(self, *args, **kwargs):
        problem = args[0] if args else kwargs.get('problem')
        factual = None
        if problem is not None and hasattr(problem, 'engine'):
            # Feasibility keeps projected enforcement tables. The assignment
            # LP needs original rule facts to build non-vacuous peeled rows.
            factual = original_build(problem.instance)
            relaxed = problem.engine.T
            names = ('e1_threshold', 'e2_valid', 'e2_tol', 'e2_natural',
                     'e2_bounds', 'e2_order', 'rank', 'e6_threshold', 'e6_fallback')
            problem.engine.T = replace(relaxed, **{name: getattr(factual, name) for name in names})
            try:
                original_init(self, *args, **kwargs)
            finally:
                problem.engine.T = relaxed
            self.assignment_original_tables = factual
            self.assignment_original_boxes = problem.boxes
        else:
            original_init(self, *args, **kwargs)

        if VISIBILITY in peeled:
            # The native constructor applies a hard sight mask. Restore the
            # exact matrix from immediately before it, while keeping frames and
            # sight_limit for the assignment LP's separately tagged soft rows.
            self.static = self._assignment_before_sight.copy()
            refined = defaultdict(list)
            for key, indices in self.types.items():
                for j in indices:
                    page = self.pages[j]
                    signature = self.static[np.ix_(self.boxes, page.slots)].tobytes()
                    refined[key+(signature,)].append(j)
            self.types = refined
            self.type_of = {j: key for key, indices in refined.items() for j in indices}
            self.pricing_cache.clear()
            self.rarity = {b: int(self.static[b].sum()) for b in self.boxes}

    def legal(self, page, boxes, *, any_port=False):
        T = self.T
        if len(boxes) > len(page.slots):
            return False
        port = None if any_port else self.port_of(page)
        if port is not None and any(self.pod[b] != port for b in boxes):
            return False
        if DG not in peeled and sum(bool(T.is_dg[b]) for b in boxes) > 1:
            return False
        if any(bool(T.closes_stack[b]) for b in boxes[:-1]):
            return False
        if sum(float(T.height[b]) for b in boxes) > page.hmax+1e-9:
            return False
        if STACK_WEIGHT not in peeled and sum(float(T.share[b]) for b in boxes) > page.limit_kg+1e-6:
            return False
        ranks = [int(T.rank[b]) for b in boxes]
        for i in range(1, len(boxes)):
            if E2 not in peeled and ranks[i] > min(ranks[:i])+T.e2_tol:
                return False
            if E1 not in peeled and self.heavy[boxes[i]] and not all(self.heavy[b] for b in boxes[:i]):
                return False
        if not all(self.static[b, page.slots[k]] for k, b in enumerate(boxes)):
            return False
        if VISIBILITY not in peeled and page.deck and getattr(self, 'sight_frames', None):
            roof = max(float(T.stack_base_z[j]) for j in page.stacks)
            for tier, b in enumerate(boxes):
                roof += float(T.height[b])
                if roof > self.sight_limit(page, tier, b)+1e-8:
                    return False
        return True

    def clear_oog(self, assignment):
        # The copied repair assumes every model box has an assigned slot, both
        # for the OOG itself and potential swap partners. A partial plan must
        # expose only its actual cargo to that same unchanged repair.
        original_boxes = self.boxes
        if all(self.T.box_ids[b] in assignment for b in original_boxes):
            return original_clear_oog(self, assignment)
        self.boxes = [b for b in original_boxes if self.T.box_ids[b] in assignment]
        try:
            return original_clear_oog(self, assignment)
        finally:
            self.boxes = original_boxes

    try:
        model_cls.__init__, model_cls._static, model_cls.legal = init, static, legal
        if original_clear_oog is not None:
            model_cls.clear_oog = clear_oog
        with peeled_masks(level):
            yield
    finally:
        for name, value in originals.items():
            if value is None:
                delattr(model_cls, name)
            else:
                setattr(model_cls, name, value)
