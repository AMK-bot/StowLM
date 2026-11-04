"""Public feasibility API adapter for scalable beam search.

A necessary completion bound can reject an impossible branch without promising
that an integer completion exists. Search therefore keeps alternatives and
backtracks at dead ends (Bertsimas and Tsitsiklis, *Introduction to Linear
Optimization*, chapter1). The released 4.4 engine owns physical/static checks,
capacity matching and the stability/strength LP. This module only forwards its
public methods; no legacy engine or implementation details are inspected.

A legal next action is not a gold-plan certificate. Finalize ballast, run the
full checker and replay the production mask before accepting a gold plan.
Search-only iterators check deadlines between opaque oracle calls. An individual
oracle call remains indivisible. No workers are started here.
"""
from __future__ import annotations

from time import monotonic

from stowage.feasibility import FeasibilityConfig, FeasibilityStats, make_feasibility_engine


class SearchDeadline(BaseException):
    """Caller-owned wall-clock guard; search may return a complete incumbent."""

    def __init__(self, *args, required_data_violations=None, required_data_probes=None):
        super().__init__(*args)
        self.required_data_violations = required_data_violations
        self.required_data_probes = required_data_probes


class RequiredDataUnavailable(Exception):
    """Shared static checker requires metadata absent/invalid at every slot.

    This is a missing-input error, never a certificate of physical infeasibility.
    ``violations`` retains the public checker's structured diagnostics unchanged.
    """

    def __init__(self, violations):
        self.violations = violations
        self.rule_ids = tuple(sorted({v["rule_id"] for entry in violations
                                      for v in entry["violations"]}))
        super().__init__(f"Required static-rule metadata unavailable for {len(violations)} load-list boxes")


_GLOBAL_PARAMETER_MESSAGES = frozenset({
    "expert.e9.over_width_length: oog_clearance must be explicitly supplied",
    "expert.e8.away_from_heat: minimum_clearance must be explicitly supplied",
    "expert.e9.over_width_length: oog_clearance must be finite and nonnegative",
    "expert.e8.away_from_heat: minimum_clearance must be finite and nonnegative",
})


def required_data_violations(instance, *, deadline=None):
    """Find explicit-data requirements failing at EVERY geometric placement.

    The public static checker owns required clearance data and the actual rule.
    The allowlisted E8/E9 clearance-parameter errors are independent of
    placement: after the real checker confirms either once, all other geometric
    probes would repeat the same global parameter lookup. Other missing-data
    messages still require exhaustive geometric probing, avoiding conclusions
    from temporarily absent supports. We never invent clearance values or
    reinterpret a rule failure as physical infeasibility. Custom search-test oracles may skip
    this preflight; native oracle construction always uses it. Each static
    checker call is indivisible, but the deadline is checked between probes.
    Timeout retains confirmed diagnostics without treating an unfinished scan
    as either a clean bill of health or physical infeasibility.
    """
    from stowage.checker.rules import check_static_rules
    from stowage.model import Placement
    from stowage.solver.scalable import _slots
    candidates, unavailable, probes = None, [], 0

    def check_deadline():
        if deadline is not None and monotonic() >= deadline:
            raise SearchDeadline([{"function": "required_data_violations"}],
                                 required_data_violations=unavailable,
                                 required_data_probes=probes)

    load_set = set(instance.load_list)
    for box in instance.containers:
        check_deadline()
        if box.id not in load_set or not (box.oog or box.special_stowage or box.dg):
            continue
        if candidates is None:
            candidates = _slots(instance)
        slots = candidates[box.length_ft]
        missing = []
        for index, slot in enumerate(slots):
            check_deadline()
            ship = instance.onboard.model_copy(deep=True)
            ship.placements = [p for p in ship.placements if p.container_id != box.id]
            ship.placements.append(Placement(slot=slot, container_id=box.id))
            report = check_static_rules(instance, ship, call_index=instance.call_index)
            probes += 1
            required = [v for v in report.violations
                        if v.rule_id == "checker.missing_vessel_data" and box.id in v.containers
                        and ("must be explicitly supplied" in v.message
                             or v.message in _GLOBAL_PARAMETER_MESSAGES)]
            global_missing = [v for v in required if v.message in _GLOBAL_PARAMETER_MESSAGES]
            if global_missing:
                unavailable.append({"container": box.id, "slots_checked": index + 1,
                                    "slots_affected": len(slots), "scope": "instance-wide rule parameter",
                                    "violations": [v.model_dump(mode="json") for v in global_missing]})
                break
            if not required:
                break
            missing = required
        else:
            if slots and missing:
                unavailable.append({"container": box.id, "slots_checked": len(slots),
                                    "violations": [v.model_dump(mode="json") for v in missing]})
    check_deadline()
    return unavailable


class SolverOracle:
    """Thin protocol wrapper with search-only shortlist/deadline conveniences."""

    def __init__(self, backend, *, mode="native-v2"):
        self.backend = backend
        self.mode = self.adapter_mode = mode
        self.instance, self.space = backend.instance, backend.space
        self._legal_cache = None
        self._legal_index_cache = None
        self._pair_counts = self._hold_counts = None

    @property
    def depth(self):
        return self.backend.depth

    def is_legal(self, action):
        return self.backend.is_legal(action)

    def legal_actions(self):
        return self.backend.legal_actions()

    def legal_candidates(self, preferred_actions):
        """Return only preferred actions belonging to the public legal set."""
        if self.mode.startswith("native-v2"):
            if hasattr(self.backend, "legal_index") and hasattr(self.backend, "index_of"):
                if self._legal_index_cache is None:
                    self._legal_index_cache = frozenset(map(int, self.backend.legal_index()))
                allowed = self._legal_index_cache
                discharges = frozenset(self.backend.legal_discharges())
                def candidates():
                    for action in preferred_actions:
                        index = self.backend.index_of(action)
                        if ((index is not None and index in allowed) or action in discharges):
                            yield action
                return candidates()
            # Compatibility with pre-index public engines and tiny test doubles.
            if self._legal_cache is None:
                self._legal_cache = frozenset(self.backend.legal_actions())
            allowed = self._legal_cache
            return (action for action in preferred_actions if action in allowed)
        # Custom fixture oracles may construct their whole action set slowly;
        # preserve their lazy public membership implementation.
        return (action for action in preferred_actions if self.is_legal(action))

    def iter_legal_actions(self, deadline=None):
        if deadline is not None and monotonic() >= deadline:
            return
        if self.mode.startswith("native-v2") and hasattr(self.backend, "legal_index"):
            for index in self.backend.legal_index():
                if deadline is not None and monotonic() >= deadline:
                    return
                yield self.backend.decode_index(index)
            for action in self.backend.legal_discharges():
                if deadline is not None and monotonic() >= deadline:
                    return
                yield action
            return
        for action in self.legal_actions():
            if deadline is not None and monotonic() >= deadline:
                return
            yield action

    def legal_index(self):
        """Forward the public read-only index array without decoding actions."""
        return self.backend.legal_index()

    def legal_pairs(self):
        """Forward public box/slot index arrays; no action objects are built."""
        return self.backend.legal_pairs()

    def decode_index(self, index):
        return self.backend.decode_index(index)

    def legal_pair_counts(self, boxes):
        """Count exact legal destinations by box using the engine's index view."""
        if self._pair_counts is None:
            self._cache_pair_counts()
        return {box: self._pair_counts.get(box, 0) for box in boxes}

    def legal_hold_counts(self, boxes):
        """Count legal hold destinations, including the engine's compatibility checks."""
        if self._hold_counts is None:
            self._cache_pair_counts()
        return {box: self._hold_counts.get(box, 0) for box in boxes}

    def _cache_pair_counts(self):
        from stowage.conventions import parse_slot
        if hasattr(self.backend, "legal_pairs"):
            import numpy as np
            box_indices, slot_indices = self.backend.legal_pairs()
            ids = self.backend.index_boxes
            holds = np.array([not parse_slot(slot).on_deck for slot in self.backend.index_slots])
            counts = np.bincount(box_indices, minlength=len(ids))
            hold_counts = np.bincount(box_indices[holds[slot_indices]], minlength=len(ids))
            self._pair_counts = dict(zip(ids, map(int, counts)))
            self._hold_counts = dict(zip(ids, map(int, hold_counts)))
        else:
            from stowage.actions.space import Place
            counts, hold_counts = {}, {}
            for action in self.backend.legal_actions():
                if isinstance(action, Place):
                    counts[action.box] = counts.get(action.box, 0) + 1
                    if not parse_slot(action.slot).on_deck:
                        hold_counts[action.box] = hold_counts.get(action.box, 0) + 1
            self._pair_counts, self._hold_counts = counts, hold_counts

    def completion_check(self):
        """Public necessary-condition diagnostics; unavailable custom checks are unknown.

        False sub-checks guide search without certifying an integer completion.
        Missing diagnostics are represented by an empty mapping, never success.
        """
        method = getattr(self.backend, "completion_check", None)
        return dict(method()) if method else {}

    def forces_restow(self, action):
        method = getattr(self.backend, "forces_restow", None)
        return bool(method(action)) if method else False

    def push(self, action):
        self.backend.push(action)
        self._legal_cache = None
        self._legal_index_cache = None
        self._pair_counts = self._hold_counts = None

    def pop(self):
        action = self.backend.pop()
        self._legal_cache = None
        self._legal_index_cache = None
        self._pair_counts = self._hold_counts = None
        return action

    def copy(self):
        copied = SolverOracle(self.backend.copy(), mode=self.mode)
        copied._legal_cache = self._legal_cache  # immutable set, identical public state
        copied._legal_index_cache = self._legal_index_cache
        copied._pair_counts, copied._hold_counts = self._pair_counts, self._hold_counts
        return copied

    def actions(self):
        return self.backend.actions()

    def is_complete(self):
        return self.backend.is_complete()

    def dead_end(self):
        return self.backend.dead_end()

    def stats(self):
        method = getattr(self.backend, "stats", None)
        return method() if method else FeasibilityStats()

    def to_plan(self):
        plan = self.backend.to_plan()
        provenance = plan.provenance.model_copy(deep=True)
        provenance.versions["scalable-oracle"] = self.mode
        return plan.model_copy(update={"provenance": provenance})


def build_engine(instance, space, config=None, *, factory=make_feasibility_engine, deadline=None):
    """Build the released oracle without modifying its configuration or behavior."""
    config = config or FeasibilityConfig()
    if factory is make_feasibility_engine:
        unavailable = required_data_violations(instance, deadline=deadline)
        if unavailable:
            raise RequiredDataUnavailable(unavailable)
    backend = factory(instance, space, config=config)
    mode = "native-v2" if factory is make_feasibility_engine else "custom"
    if not config.extra.get("global_screen", 1):
        mode += "-diagnostic-global-off"
    return SolverOracle(backend, mode=mode)
