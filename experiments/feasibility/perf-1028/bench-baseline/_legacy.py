"""Legacy v1 screens (plan 4.4 v1), kept verbatim only because the two-step baseline adapter
(``stowage.solver.twostep_adapter``, owner 4.8) imports them. Not used by the v2 engine. Remove once 4.8 builds its
plans through ``make_feasibility_engine`` (the leader's G1 decision: solvers generate through ``legal_actions``).

``CompletionCapacity``: a bipartite matching joins each remaining box to usable physical cells (Hall, 1935); a long
box requests two cells; a rejection bound, never a sufficient certificate.
``GlobalBounds``: an LP relaxation distributing remaining mass over empty cells plus adjustable ballast under the
3.7 rows (Bertsimas & Tsitsiklis, ch. 1); a necessary screen.
APPROXIMATION (v1, unchanged): matching relaxes paired-slot coupling, shared stack mass, future DG/OOG conflicts.
"""
from __future__ import annotations

import warnings
from collections import OrderedDict, defaultdict
from collections.abc import Iterable

import numpy as np
from scipy.optimize import OptimizeWarning, linprog
from scipy.sparse import csr_matrix, hstack, vstack
from scipy.sparse.csgraph import maximum_bipartite_matching

from stowage.checker.rules import _parameter
from stowage.checker.rules_special import _parameter as special_parameter
from stowage.conventions import cells_of_slot, parse_slot
from stowage.linear import band_for_instance, build_linear_model, linear_constraints
from stowage.model import Instance, ShipState

GLOBAL_RULE_IDS = ("iscode.stability.margin", "iacs.strength.margin")


class CompletionCapacity:
    """Immutable geometry precomputation; safe to share between engine copies."""

    def __init__(self, instance: Instance):
        self.instance = instance
        self.boxes = {b.id: b for b in instance.containers}
        self.cells = {c.id: c for c in instance.vessel.cells}
        self.stacks = {s.id: s for s in instance.vessel.stacks}
        self.cell_indices = {cid: i for i, cid in enumerate(sorted(self.cells))}
        slots = set(self.cells)
        for cid in self.cells:
            a = parse_slot(cid)
            slot = f'{a.bay + 1:03d}{a.row:02d}{a.tier:02d}'
            if all(c in self.cells for c in cells_of_slot(slot)):
                slots.add(slot)
        self.footprints = {slot: cells_of_slot(slot) for slot in sorted(slots)}
        self.candidates = {}
        heavy = _parameter(instance, 'expert.e6.heavy_under_deck', 'heavy_threshold')
        by_kind = {}
        crane_limit = special_parameter(instance, 'port.e3.quay_height', 'max_on_deck_tiers', 'max_tiers')
        if crane_limit is None:
            crane_limit = instance.port.quay_crane.max_on_deck_tiers
        for box in instance.containers:
            classes = {box.dg.imdg_class, *box.dg.subsidiary_risks} if box.dg else set()
            is_heavy = isinstance(heavy, (int, float)) and box.gross_mass_kg >= heavy
            kind = (box.length_ft, box.reefer, tuple(box.special_stowage), tuple(sorted(classes)), is_heavy)
            if kind in by_kind:
                self.candidates[box.id] = by_kind[kind]
                continue
            candidates = []
            for slot, ids in self.footprints.items():
                if (box.length_ft == 20) != (len(ids) == 1):
                    continue
                cells = [self.cells[c] for c in ids]
                if not all(getattr(c, f'accepts_{box.length_ft}') for c in cells):
                    continue
                if box.reefer and not any(c.reefer_plug for c in cells):
                    continue
                deck = parse_slot(slot).on_deck
                if ('under_deck' in box.special_stowage and deck
                    or 'on_deck' in box.special_stowage and not deck
                    or deck and is_heavy):
                    continue
                if any(self.stacks[c.stack_id].dg_classes_allowed is not None
                       and not classes <= set(self.stacks[c.stack_id].dg_classes_allowed) for c in cells):
                    continue
                if deck and isinstance(crane_limit, (int, float)):
                    if any(self.stacks[c.stack_id].cell_ids.index(c.id) + 1
                           > crane_limit for c in cells):
                        continue
                candidates.append(slot)
            self.candidates[box.id] = by_kind[kind] = tuple(candidates)

    def violations(self, ship: ShipState, remaining_ids: Iterable[str]) -> tuple[str, ...]:
        """Necessary room bounds for a fixed partial stow, with no mutation."""
        remaining = tuple(sorted(set(remaining_ids)))
        if not remaining:
            return ()
        if any(cid not in self.boxes for cid in remaining):
            return ('checker.missing_vessel_data',)
        occupied = set()
        columns = defaultdict(list)
        masses = defaultdict(float)
        heights = defaultdict(float)
        for p in ship.placements:
            box = self.boxes[p.container_id]
            ids = cells_of_slot(p.slot)
            occupied.update(ids)
            for cid in ids:
                sid = self.cells[cid].stack_id
                columns[sid].append((cid, box))
                masses[sid] += box.gross_mass_kg / len(ids)
                heights[sid] += box.height_m + (box.oog.over_height_m if box.oog else 0)
        limits = {sid: max(v for v in (s.max_mass_kg, s.max_mass_20_kg, s.max_mass_40_kg) if v is not None)
                  for sid, s in self.stacks.items()}
        if sum(self.boxes[c].gross_mass_kg for c in remaining) > sum(max(0, limits[s] - masses[s]) for s in self.stacks) + 1e-6:
            return ('expert.physical.stack_weight',)
        tolerance = _parameter(self.instance, 'expert.e2.weight_order', 'tolerance')
        threshold = _parameter(self.instance, 'expert.e1.heavy_bottom', 'heavy_threshold')
        rows, cols = [], []
        row = 0
        for boxid in remaining:
            box = self.boxes[boxid]
            available = set()
            for slot in self.candidates[boxid]:
                ids = self.footprints[slot]
                if occupied.intersection(ids):
                    continue
                valid = True
                for cid in ids:
                    sid = self.cells[cid].stack_id
                    if masses[sid] + box.gross_mass_kg / len(ids) > limits[sid] + 1e-6:
                        valid = False
                        break
                    if heights[sid] + box.height_m + (box.oog.over_height_m if box.oog else 0) > self.stacks[sid].max_height_m + 1e-9:
                        valid = False
                        break
                    for lowercid, lower in columns[sid]:
                        if lowercid >= cid:
                            continue
                        if (box.length_ft == 20 and lower.length_ft != 20
                            or 'top_stowage' in lower.special_stowage
                            or lower.oog is not None and lower.oog.over_height_m > 0
                            or isinstance(tolerance, (int, float)) and box.gross_mass_kg > lower.gross_mass_kg + tolerance
                            or isinstance(threshold, (int, float)) and box.gross_mass_kg >= threshold > lower.gross_mass_kg):
                            valid = False
                            break
                    if not valid:
                        break
                if valid:
                    available.update(self.cell_indices[c] for c in ids)
            if not available:
                return ('expert.physical.size_fit',)
            for _ in range(1 if box.length_ft == 20 else 2):
                rows.extend([row] * len(available))
                cols.extend(sorted(available))
                row += 1
        matrix = csr_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(row, len(self.cells)))
        match = maximum_bipartite_matching(matrix, perm_type='column')
        if np.any(match < 0):
            return ('expert.physical.slot_occupancy',)
        return ()


def completion_violations(instance: Instance, ship: ShipState, remaining_ids: Iterable[str]) -> tuple[str, ...]:
    """Convenience wrapper; repeated calls should reuse ``CompletionCapacity``."""
    return CompletionCapacity(instance).violations(ship, remaining_ids)


class GlobalBounds:
    """Reusable immutable coefficients, with a bounded cache of state answers (v1)."""

    def __init__(self, instance, margin_scale=1):
        if not np.isfinite(margin_scale) or margin_scale < 1:
            raise ValueError("margin_scale must be finite and at least one")
        self.instance = instance
        self.margin_scale = float(margin_scale)
        self.model = None
        self.available = False
        self.unavailable_reason = None
        self._cache = OrderedDict()
        self._boxes = {box.id: box for box in instance.containers}
        self._discharge = frozenset(instance.discharge_list)
        self.calls = 0
        self.lp_calls = 0
        self.lp_indeterminate = 0
        if instance.vessel.hull is None:
            self.unavailable_reason = "fixture has no hull: global physics not modelled"
            return
        try:
            band = band_for_instance(instance)
            self.model = build_linear_model(instance, band=band)
            self.constraints = linear_constraints(self.model, direction="necessary", margin_scale=margin_scale)
        except ValueError as exc:
            self.unavailable_reason = str(exc)
            return
        model = self.model
        self.available = True
        self._index = {cid: i for i, cid in enumerate(model.cell_ids)}
        self._tank_idx = np.flatnonzero(model.tank_adjustable)
        windows = getattr(model, "_tank_mass_bounds_kg", {})
        self._tank_bounds = [windows.get(model.tank_ids[i], (0., model.tank_capacity_kg[i]))
                             for i in self._tank_idx]
        rows, cols = [], []
        for j, stack in enumerate(instance.vessel.stacks):
            for cid in stack.cell_ids:
                rows.append(j)
                cols.append(self._index[cid])
        self._stack_matrix = csr_matrix((np.ones(len(rows)), (rows, cols)),
            shape=(len(instance.vessel.stacks), len(model.cell_ids)))
        self._stack_caps = np.asarray([max(stack.max_mass_kg,
            stack.max_mass_20_kg or 0., stack.max_mass_40_kg or 0.)
            for stack in instance.vessel.stacks])

    def violations(self, ship, remaining_ids):
        """Catalogue rule ids when even the relaxed departure is impossible."""
        self.calls += 1
        if not self.available:
            return GLOBAL_RULE_IDS if self.instance.vessel.hull is not None else ()
        remaining = tuple(sorted(set(remaining_ids)))
        placements = tuple(sorted((p.container_id, p.slot) for p in ship.placements
                                  if p.container_id not in self._discharge))
        key = (placements, remaining)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        answer = self._solve(placements, remaining)
        self._cache[key] = answer
        if len(self._cache) > 512:
            self._cache.popitem(last=False)
        return answer

    def _solve(self, placements, remaining):
        model, cs = self.model, self.constraints
        cargo = np.zeros(len(model.cell_ids))
        occupied = np.zeros(len(cargo), dtype=bool)
        for bid, slot in placements:
            cells = cells_of_slot(slot)
            for cid in cells:
                i = self._index[cid]
                cargo[i] += self._boxes[bid].gross_mass_kg / len(cells)
                occupied[i] = True
        remain_mass = sum(self._boxes[bid].gross_mass_kg for bid in remaining)
        empty = np.flatnonzero(~occupied) if remaining else np.zeros(0, dtype=int)
        cell_cap = max((self._boxes[bid].gross_mass_kg /
                       (1 if self._boxes[bid].length_ft == 20 else 2)
                       for bid in remaining), default=0.)
        bounds = [(0., cell_cap)] * len(empty) + self._tank_bounds
        fixed = cs.a_cell @ cargo + cs.const
        coefficients = np.column_stack((cs.a_cell[:, empty], cs.a_tank[:, self._tank_idx]))
        matrices, rhs = [], []
        upper = np.isfinite(cs.ub)
        lower = np.isfinite(cs.lb)
        matrices.extend((csr_matrix(coefficients[upper]), csr_matrix(-coefficients[lower])))
        rhs.extend((cs.ub[upper]-fixed[upper], fixed[lower]-cs.lb[lower]))
        stack = hstack((self._stack_matrix[:, empty],
                        csr_matrix((len(self._stack_caps), len(self._tank_idx)))), format="csr")
        matrices.append(stack)
        rhs.append(self._stack_caps-self._stack_matrix@cargo)
        matrix = vstack(matrices, format="csr")
        right = np.concatenate(rhs)
        eq = csr_matrix(np.r_[np.ones(len(empty)), np.zeros(len(self._tank_idx))][None, :])
        if not bounds:
            return () if remain_mass < 1e-6 and np.all(right >= -1e-6) else GLOBAL_RULE_IDS
        scale = np.maximum(1., np.maximum(np.abs(right),
            np.asarray(abs(matrix).max(axis=1).toarray()).ravel()))
        self.lp_calls += 1
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=OptimizeWarning,
                                    message="Unrecognized options detected.*")
            result = linprog(np.zeros(len(bounds)), A_ub=matrix.multiply((1/scale)[:, None]),
                b_ub=right/scale, A_eq=eq, b_eq=[remain_mass], bounds=bounds,
                method="highs", options={"threads": 1, "random_seed": 0,
                    "primal_feasibility_tolerance": 1e-9,
                    "dual_feasibility_tolerance": 1e-9})
        if result.success:
            return ()
        # D49/D23: solver trouble is not a proof that the necessary relaxation
        # is infeasible. SciPy/HiGHS status 2 alone certifies infeasibility.
        if getattr(result, "status", None) == 2:
            return GLOBAL_RULE_IDS
        self.lp_indeterminate += 1
        return ()
