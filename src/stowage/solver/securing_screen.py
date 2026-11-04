"""Incremental deck securing using the checker's IRS calculation unchanged.

A stack can be below its weight rating and still tip, rack, or overload a rod.
This screen computes those forces before adding a box. Sources: IRS Main Rules
July 2026 Pt.3 Ch.5 §§2.1–2.2 and IRS-G-DES-19 July 2025 §§3.3–3.6,
implemented in :mod:`stowage.checker.lashing` (including its synthetic gear).

APPROXIMATION: screen at D56's declared design GM and shallow-draught limit
(T approaches zero). For deck cargo above the roll centre, this maximizes the checker's
roll lever and pitch acceleration over positive loading draughts. This rejects
some arrangements that could pass at their actual condition; it never relaxes
an IRS limit. The final full checker remains mandatory.

The checker currently has no loading-condition injection seam. We execute its
original function bytecode with private dependency bindings, rather than copy
its force equations or mutate shared checker globals. Only the loading condition,
geometry catalogs and cached guide design are injected. Each evaluation owns its
namespace, so concurrent screens cannot alter each other's or the checker's data.
"""
from __future__ import annotations

import builtins
import math
import time
from dataclasses import dataclass
from types import FunctionType, SimpleNamespace

import numpy as np

from stowage.actions.space import Place
from stowage.checker import lashing, visibility
from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import ShipState
from stowage.model.ship import Placement


_CHECKER_ERRORS = (ValueError, KeyError, IndexError, ArithmeticError, FileNotFoundError,
                   TypeError, AttributeError, NotImplementedError, np.linalg.LinAlgError)


@dataclass(frozen=True)
class SecuringScreenResult:
    passed: bool
    worst_ratio: float
    reason: str | None = None


def _bind(function, **bindings):
    namespace = {**function.__globals__, **bindings}
    return FunctionType(function.__code__, namespace, function.__name__,
                        function.__defaults__, function.__closure__)


class _PrefixExemption(Exception):
    """A one-tier end-frame chain may change its rod anchor after extension."""


class DeckSecuringScreen:
    """Per-instance cached screen; pass only affected, physically connected stacks.

    Long boxes link two physical 20-foot columns: both columns and their lower
    supports must be evaluated together. Cache keys retain slots and container
    IDs (the instance, including masses/heights/OOG, must remain immutable).
    ``metrics`` counts calls, hits, actual IRS evaluation wall seconds and full
    placement API wall seconds (including collection, graph traversal and caching).
    Whole-state screening covers planned-connected deck components only;
    untouched background-only components remain the full checker's responsibility.
    """

    policy_name = 'design-envelope'
    _exempt_single_chain_prefix = False

    def __init__(self, instance):
        self.instance = instance
        self.boxes = {box.id: box for box in instance.containers}
        self.cells, self.stacks = visibility._cargo_catalogs(instance.vessel)
        self.fixed_loads = visibility._fixed_loads(instance.vessel)
        self.design = lashing.derive_cell_guide_design(instance.vessel)
        hull = instance.vessel.hull
        self.design_gm = lashing._lashing_design_gm(hull)
        ref = hull.hull_offsets_ref.split('#')[0].split('/')[-1].removesuffix('.toml')
        geometry, _ = visibility._geometry(ref)
        self.condition = SimpleNamespace(gm_m=self.design_gm,
            t_aft_m=math.ulp(0.),
            t_fwd_m=math.ulp(0.), geometry=geometry)
        self._condition_signature = (self.condition.gm_m, self.condition.t_aft_m, self.condition.t_fwd_m)
        # The shallow roll-centre bound applies only above the deepest possible
        # roll centre; fail closed for an unexpected deck definition below it.
        self.invalid_deck = any(s.on_deck and s.base_z_m < hull.depth_m/2
                                for s in self.stacks.values())
        self.background_edges = []
        for load in self.fixed_loads:
            if load.mass_kg > 0 and load.kind in ('unknown', 'other'):
                raise ValueError('positive fixed load lacks securing cargo kind')
            if load.kind == 'cargo' and load.cargo_boxes is None:
                raise ValueError('fixed cargo lacks per-box securing records')
            for box in load.cargo_boxes or []:
                self.background_edges.append(tuple({self.cells[c].stack_id for c in box.cell_ids}))
        self.cache = {}
        self.metrics = dict(calls=0, cache_hits=0, evaluations=0, evaluation_seconds=0.,
                            placement_calls=0, placement_seconds=0.)

    def check_placement(self, expander, action):
        started = time.perf_counter()
        self.metrics['placement_calls'] += 1
        try:
            if not isinstance(action, Place) or not parse_slot(action.slot).on_deck:
                return SecuringScreenResult(True, 0.)
            deferred = self._defer_placement(expander, action)
            if deferred:
                self._count('prefix_future_repair_exemptions')
                return SecuringScreenResult(True, 0., deferred)
            placements = [Placement(container_id=cid, slot=slot)
                          for cid, (where, slot) in expander.where.items()
                          if where == 'ship' and cid != action.box]
            placements.append(Placement(container_id=action.box, slot=action.slot))
            targets = {self.cells[c].stack_id for c in cells_of_slot(action.slot)}
            return self.evaluate(placements, stack_ids=targets, _prefix=True)
        except _CHECKER_ERRORS as exc:
            return self._exception(exc)
        finally:
            self.metrics['placement_seconds'] += time.perf_counter()-started

    def _defer_placement(self, expander, action):
        return None

    def check_state(self, expander, *, prefix=False):
        """Check planned-connected deck components; prefix=True enables deferrals."""
        try:
            return self.evaluate([Placement(container_id=cid, slot=slot)
                                  for cid, (where, slot) in expander.where.items() if where == 'ship'], _prefix=prefix)
        except _CHECKER_ERRORS as exc:
            return self._exception(exc)

    def evaluate(self, placements, *, stack_ids=None, _prefix=False):
        """Check planned-connected deck components, or those touching stack_ids.

        Background cargo connected to those components participates. Untouched
        background-only components are left to the mandatory final full checker.
        """
        self.metrics['calls'] += 1
        try:
            if (self.condition.gm_m, self.condition.t_aft_m, self.condition.t_fwd_m) != self._condition_signature:
                raise ValueError('securing injected loading condition changed after construction')
            return self._evaluate(placements, stack_ids=stack_ids, prefix=_prefix)
        except _CHECKER_ERRORS as exc:
            return self._exception(exc)

    def _evaluate(self, placements, *, stack_ids=None, prefix=False):
        if self.invalid_deck:
            return self._exception(ValueError('deck below conservative roll-centre envelope'))
        records = []
        edges = list(self.background_edges)
        for p in placements:
            if not parse_slot(p.slot).on_deck:
                continue
            sids = {self.cells[c].stack_id for c in cells_of_slot(p.slot)}
            records.append((p, sids))
            edges.append(tuple(sids))
        pending = set(stack_ids) if stack_ids is not None else set().union(*(s for _, s in records))
        worst = 0.
        reason = None
        while pending:
            component = {min(pending)}
            while True:
                expanded = component | {sid for edge in edges if component.intersection(edge) for sid in edge}
                if expanded == component:
                    break
                component = expanded
            pending -= component
            chosen = [p for p, s in records if s & component]
            key = (bool(prefix and self._exempt_single_chain_prefix), tuple(sorted(component)), tuple(sorted((p.slot, p.container_id) for p in chosen)))
            if key in self.cache:
                self.metrics['cache_hits'] += 1
                result = self.cache[key]
            else:
                started = time.perf_counter()
                result = self._evaluate_component(chosen, component, prefix=prefix)
                self.metrics['evaluation_seconds'] += time.perf_counter()-started
                self.metrics['evaluations'] += 1
                self.cache[key] = result
            worst = max(worst, result.worst_ratio)
            if not result.passed:
                return SecuringScreenResult(False, worst, result.reason)
            if result.reason is not None and reason is None:
                reason = result.reason
        return SecuringScreenResult(True, worst, reason)

    def _count(self, name):
        self.metrics[name] = self.metrics.get(name, 0)+1

    def _reject(self, kind, reason, worst=math.inf):
        self._count('rejections:'+kind)
        return SecuringScreenResult(False, worst, reason)

    def _exception(self, exc):
        self._count('exceptions:'+type(exc).__name__)
        return self._reject('exception:'+type(exc).__name__, str(exc))

    def _geometry_failure(self, reason):
        return self._reject('geometry', reason)

    def _evaluate_component(self, placements, component, *, prefix=False):
        catalogs = ({cid: cell for cid, cell in self.cells.items() if cell.stack_id in component},
                    {sid: self.stacks[sid] for sid in component})
        loads = []
        for load in self.fixed_loads:
            if load.kind == 'cargo':
                boxes = [b for b in load.cargo_boxes or []
                         if any(self.cells[c].stack_id in component for c in b.cell_ids)]
                if boxes:
                    loads.append(load.model_copy(update={'cargo_boxes': boxes}))
        cargo_geometry = _bind(visibility._all_cargo_geometry,
                              _cargo_catalogs=lambda vessel: catalogs,
                              _fixed_loads=lambda vessel: loads)
        proxy = SimpleNamespace(**vars(visibility))
        condition_calls = []
        motion_calls = []
        expected_gm, expected_aft, expected_fwd = self._condition_signature
        expected_draft = (expected_aft+expected_fwd)/2

        def injected_condition(instance, ship):
            actual = (self.condition.gm_m, self.condition.t_aft_m, self.condition.t_fwd_m)
            if actual != self._condition_signature:
                raise ValueError('securing injected loading condition changed after construction')
            condition_calls.append(actual)
            return self.condition

        def guarded_motions(hull, draught, gm, cb):
            motion_calls.append((gm, draught))
            if gm != expected_gm or draught != expected_draft:
                raise ValueError('checker did not use the injected securing GM/draught')
            return lashing._motions(hull, draught, gm, cb)

        proxy._loading_condition = injected_condition
        proxy._cargo_catalogs = lambda vessel: catalogs
        proxy._fixed_loads = lambda vessel: loads
        proxy._all_cargo_geometry = cargo_geometry
        original_import = builtins.__import__

        def private_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'stowage.checker.visibility':
                return proxy
            return original_import(name, globals, locals, fromlist, level)

        private_builtins = {**vars(builtins), '__import__': private_import}
        original_graph = _bind(lashing._load_graph, __builtins__=private_builtins)

        def graph(instance, ship, motions):
            loads = original_graph(instance, ship, motions)
            if (prefix and self._exempt_single_chain_prefix
                    and any(len(chain) == 1 for chain in lashing._chains(loads))):
                raise _PrefixExemption('single-tier prefix end-frame anchor deferred')
            return loads

        checker = _bind(lashing._check_lashing, __builtins__=private_builtins,
                        _load_graph=graph, _motions=guarded_motions,
                        derive_cell_guide_design=lambda vessel: self.design)
        ship = ShipState(vessel_id=self.instance.vessel.id, placements=placements)
        try:
            report = checker(self.instance, ship, call_index=self.instance.call_index)
            if not condition_calls or not motion_calls:
                raise ValueError('checker did not evidence the injected securing GM/draught')
            # Geometry can fail after the injected motions were computed but
            # before the checker publishes any metrics. Preserve its diagnosis;
            # completed calculations must still evidence the requested GM.
            if not report.metrics and report.violations:
                return self._geometry_failure(report.violations[0].message)
            if report.metrics.get('gm_m') != expected_gm:
                raise ValueError('checker did not evidence the injected securing GM/draught')
            ratios = [value for name, value in report.metrics.items() if name.startswith('max_') and name.endswith('_ratio')]
            worst = max(ratios, default=0.)
            # Include mass-rating failures, which have physical units rather than
            # a named ratio metric. An unsupported graph has no numeric force.
            for violation in report.violations:
                if violation.measured is not None and violation.limit:
                    worst = max(worst, violation.measured/violation.limit)
            passed = report.ran and not report.violations and math.isfinite(worst)
            reason = report.error or (report.violations[0].message if report.violations else None)
            if not passed:
                rating = any(v.rule_id == 'expert.physical.stack_weight'
                             or v.message.startswith('Container exceeds synthetic IRS')
                             for v in report.violations)
                return self._reject('rating' if rating else 'force', reason, worst or math.inf)
            return SecuringScreenResult(True, worst)
        except _PrefixExemption as exc:
            self._count('prefix_single_chain_exemptions')
            return SecuringScreenResult(True, 0., str(exc))
        except _CHECKER_ERRORS as exc:
            return self._exception(exc)


class NecessaryDeckSecuringScreen(DeckSecuringScreen):
    """Deck-only necessary mask screen at the minimum acceleration envelope.

    APPROXIMATION: use the IS-Code 0.15 m GM floor and the vessel's deepest
    checker-permitted draught (hull.max_draught_m, not scantling draught unless
    equal). Port restrictions can only reduce that upper bound. Stronger vessel
    GM minima are deliberately not credited. Above D/2, these conditions lower
    the unchanged IRS accelerations over every checker-admissible condition.
    Force monotonicity and prefix extension are empirically regression-tested,
    not symbolically proven; final full checking remains mandatory.

    A prefix component containing ANY single-tier end-frame chain is exempt:
    adding a second box changes its rod anchor, including the inner end of a
    20-foot box supporting a long box on its outer end. Terminal evaluations do
    not exempt it. Unknown bounds or model/injection exceptions fail open:
    'passed' here means not proven impossible, never checker acceptance.
    Prefix force checks also defer while discharge or future hold access could
    remove/restow deck supports. The cheap hold test deliberately credits any
    free hold cell plus remaining load/buffer cargo, regardless of its fit.
    """

    policy_name = 'necessary-envelope'
    _exempt_single_chain_prefix = True

    def __init__(self, instance):
        self.instance = instance
        self.bounds_available = False
        self.unavailable_reason = None
        self.metrics = dict(calls=0, cache_hits=0, evaluations=0, evaluation_seconds=0.,
                            placement_calls=0, placement_seconds=0.)
        try:
            super().__init__(instance)
            hull = instance.vessel.hull
            draft = getattr(hull, 'max_draught_m', None)
            if (isinstance(draft, bool) or not isinstance(draft, (int,float))
                    or not math.isfinite(draft) or draft <= 0):
                raise ValueError('necessary securing requires an evidenced finite positive max_draught_m')
            if self.design_gm < .15 or self.invalid_deck:
                raise ValueError('necessary securing acceleration bounds unavailable for this hull/deck')
            self.condition = SimpleNamespace(gm_m=.15,t_aft_m=float(draft),
                                             t_fwd_m=float(draft),geometry=self.condition.geometry)
            self._condition_signature = (.15,float(draft),float(draft))
            self.bounds_available = True
        except _CHECKER_ERRORS as exc:
            self.unavailable_reason = str(exc)
            self._count('unavailable:'+type(exc).__name__)

    def _exception(self, exc):
        self._count('exceptions:'+type(exc).__name__)
        self._count('fail_open:exception:'+type(exc).__name__)
        return SecuringScreenResult(True, 0., 'necessary securing unknown: '+str(exc))

    def _geometry_failure(self, reason):
        self._count('fail_open:geometry')
        return SecuringScreenResult(True, 0., 'necessary securing unknown: '+reason)

    def check_placement(self, expander, action):
        if self.bounds_available:
            return super().check_placement(expander, action)
        self._count('placement_calls')
        self._count('fail_open:bounds')
        return SecuringScreenResult(True, 0., self.unavailable_reason)

    def _defer_placement(self, expander, action):
        if any(expander.where.get(cid, (None,))[0] == 'ship'
               for cid in self.instance.discharge_list):
            return 'prefix securing deferred: pending discharge can alter deck cargo'
        remaining = [*self.instance.load_list, *getattr(expander, 'buffer', ())]
        if any(cid != getattr(action, 'box', None)
               and expander.where.get(cid, (None,))[0] != 'ship' for cid in remaining):
            if any(not stack.on_deck and any(cid not in expander.occ for cid in stack.cell_ids)
                   for stack in expander.stacks.values()):
                return 'prefix securing deferred: future hold access can restow deck cargo'
        return None

    def check_state(self, expander, *, prefix=False):
        if self.bounds_available:
            try:
                if prefix:
                    deferred = self._defer_placement(expander, None)
                    if deferred:
                        self._count('prefix_future_repair_exemptions')
                        return SecuringScreenResult(True, 0., deferred)
                return super().check_state(expander, prefix=prefix)
            except _CHECKER_ERRORS as exc:
                return self._exception(exc)
        self._count('fail_open:bounds')
        return SecuringScreenResult(True, 0., self.unavailable_reason)

    def evaluate(self, placements, *, stack_ids=None, _prefix=False):
        if self.bounds_available:
            return super().evaluate(placements, stack_ids=stack_ids, _prefix=_prefix)
        self._count('calls')
        self._count('fail_open:bounds')
        return SecuringScreenResult(True, 0., self.unavailable_reason)
