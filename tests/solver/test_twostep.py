"""Two-step mechanics and planning adapter checks, not full-checker evidence.

Fixture hull numbers are placeholders. Physical-only test doubles isolate cargo-only
pre-stow and fixed-category terminal search; hull-free adapter tests exercise exact
local rules explicitly. Full-hull experiment runs supply checker integration evidence.
"""

from dataclasses import FrozenInstanceError

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import Provenance, YardStack, YardState
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import twostep
from stowage.versions import ACTION_SPACE_VERSION


def interchangeable_instance():
    instance = build_instance()
    original = instance.containers[2]
    boxes = [original.model_copy(update={"id": b.id, "special_stowage": [], "pod": "THSGZ"})
             for b in instance.containers[1:3]]
    return instance.model_copy(update={
        "containers": boxes, "load_list": [b.id for b in boxes],
        "yard": YardState(yard_id=instance.yard.yard_id,
                          stacks=[YardStack(key="A-001-01", length_ft=20,
                                            boxes=[b.id for b in boxes])]),
    })


class PhysicalOnlyOracle:
    """Test double: real move legality, deliberately no hydrostatics/IMDG claim."""

    def __init__(self, instance, space, *, config=None):
        self.instance = instance
        self.space = space
        self.history = []
        self._physical_cache = {}

    def legal_actions(self):
        # Cache only this double's exact physical replay for the unchanged
        # immutable prefix. Subclasses still apply their own traps/filters.
        key = tuple(self.history)
        if key in self._physical_cache:
            return list(self._physical_cache[key])
        loaded = {a.box for a in self.history if isinstance(a, Place)}
        actions = []
        for box in self.instance.load_list:
            if box in loaded:
                continue
            for cell in self.instance.vessel.cells:
                action = Place(box, cell.id)
                try:
                    plan = self.space.expand(self.instance, [*self.history, action])
                except ValueError:
                    continue
                if apply_plan(self.instance, plan).legal:
                    actions.append(action)
        result = sorted(actions, key=self.space.sort_key)
        self._physical_cache[key] = tuple(result)
        return result

    def is_legal(self, action):
        return action in self.legal_actions()

    def push(self, action):
        if not self.is_legal(action):
            raise ValueError("physical-only test oracle rejected action")
        self.history.append(action)

    def pop(self):
        return self.history.pop()

    def copy(self):
        other = PhysicalOnlyOracle(self.instance, self.space)
        other.history = self.history[:]
        other._physical_cache = self._physical_cache
        return other

    def actions(self):
        return self.history[:]

    def is_complete(self):
        return {a.box for a in self.history} == set(self.instance.load_list)

    def dead_end(self):
        return None

    def to_plan(self):
        return self.space.expand(self.instance, self.history)

    @property
    def depth(self):
        return len(self.history)


@pytest.fixture
def physical_oracle(monkeypatch):
    monkeypatch.setattr(twostep, "make_feasibility_engine", PhysicalOnlyOracle)


@pytest.fixture
def legacy_rules():
    """D25-specific tests retain their explicit pre-activation contract."""
    with rule_freeze_v1(enabled=False):
        yield


def test_categories_use_physical_attributes_not_caller_labels():
    box = interchangeable_instance().containers[0]
    same = box.model_copy(update={"id": interchangeable_instance().containers[1].id,
                                  "category": "untrusted label"})
    assert twostep.category_of(box) == twostep.category_of(same)
    for update in ({"gross_mass_kg": 25000}, {"pod": "THBKK"},
                   {"special_stowage": ["under_deck"]}, {"height_m": 2.896},
                   {"reefer": True}, {"oog": build_instance().containers[3].oog},
                   {"dg": build_instance().containers[1].dg}):
        assert twostep.category_of(box) != twostep.category_of(box.model_copy(update=update))


def test_preplan_ignores_actual_yard_and_is_immutable(physical_oracle):
    instance = interchangeable_instance()
    changed = instance.model_copy(update={"yard": instance.yard.model_copy(update={
        "stacks": [instance.yard.stacks[0].model_copy(update={
            "boxes": list(reversed(instance.load_list))})]})})
    first = twostep.preplan(instance, seed=7, time_limit_s=5)
    second = twostep.preplan(changed, seed=7, time_limit_s=5)
    assert first.status == second.status == "ok"
    assert first.slots == second.slots
    assert first.actions == second.actions
    assert len(first.slots) == len(instance.load_list)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        first.slots[0].slot = "0010182"


def test_terminal_chooses_accessible_box_without_changing_slots(physical_oracle):
    instance = interchangeable_instance()
    before = instance.model_dump()
    planner = twostep.TwoStepPlanner()
    pre = twostep.preplan(instance, seed=9, time_limit_s=5)
    result = planner.plan(instance, seed=9, time_limit_s=5)
    assert result.status == "ok", result.message
    assert result.plan.action_space == f"boxslot@{ACTION_SPACE_VERSION}"
    assert result.plan.ballast is None
    actions = get_action_space("boxslot").from_plan(instance, result.plan)
    assert all(isinstance(a, Place) for a in actions)
    assert actions[0].box == instance.load_list[-1]  # accessible yard top first
    assert {a.slot for a in actions} == {s.slot for s in pre.slots}
    categories = {s.slot: s.category for s in pre.slots}
    boxes = {b.id: b for b in instance.containers}
    assert all(twostep.category_of(boxes[a.box]) == categories[a.slot] for a in actions)
    replay = apply_plan(instance, result.plan)
    assert replay.legal and replay.count.relocations == 0
    assert replay.count.loads == len(instance.load_list)
    assert instance.model_dump() == before


def test_protocol_declares_wall_clock_search_nondeterminism(physical_oracle):
    instance = interchangeable_instance()
    planner = twostep.TwoStepPlanner()
    assert planner.name == "baseline.twostep"
    assert planner.sets_ballast is False
    assert planner.deterministic is False
    a = planner.plan(instance, seed=31, time_limit_s=5)
    b = planner.plan(instance, seed=31, time_limit_s=5)
    assert a.status == b.status == "ok"
    assert a.planner == b.planner == "baseline.twostep"
    assert a.metrics["deterministic"] == b.metrics["deterministic"] == 0
    # This small fixture exhausts its search; equality here does not promise
    # determinism when the production search stops at its wall-clock deadline.
    assert a.plan.model_dump() == b.plan.model_dump()


def test_zero_budget_returns_timeout_without_a_plan():
    result = twostep.TwoStepPlanner().plan(interchangeable_instance(), seed=1, time_limit_s=0)
    assert result.status == "timeout"
    assert result.plan is None


def test_positive_budget_expiring_during_carrier_returns_no_partial_plan(monkeypatch, physical_oracle):
    """A clock advance during root setup must not start terminal assignment."""
    ticks = iter((0., 0., 2., 2.))
    monkeypatch.setattr(twostep, "monotonic", lambda: next(ticks))
    result = twostep.TwoStepPlanner().plan(interchangeable_instance(), time_limit_s=2.)
    assert result.status == "timeout"
    assert result.plan is None
    assert result.metrics["preplan_status"] == "timeout"
    assert result.metrics["terminal_status"] == "not_started"


def test_expired_terminal_budget_never_constructs_oracle(monkeypatch, physical_oracle):
    from stowage.solver import twostep_terminal

    instance = interchangeable_instance()
    prestow = twostep.preplan(instance, time_limit_s=5.)
    assert prestow.status == "ok"
    monkeypatch.setattr(twostep_terminal, "monotonic", lambda: 2.)

    def forbidden_oracle(*args, **kwargs):
        raise AssertionError("Expired terminal phase must not construct an oracle")

    plan, metrics, status, _ = twostep_terminal.terminal_plan(
        instance, prestow, deadline=1., seed=0, engine_factory=forbidden_oracle)
    assert status == "timeout" and plan is None
    assert metrics["terminal_cp_solves"] == 0
    assert metrics["fixed_slot_failure"] == 1


def test_terminal_deadline_during_replay_never_emits_partial_candidate(monkeypatch, physical_oracle):
    from stowage.solver import twostep_terminal

    instance = interchangeable_instance()
    prestow = twostep.preplan(instance, time_limit_s=5.)
    assert prestow.status == "ok"
    # Permit the first CP solve, then expire before its first crane action.
    ticks = iter((0., 0.))
    monkeypatch.setattr(twostep_terminal, "monotonic", lambda: next(ticks, 2.))
    plan, metrics, status, _ = twostep_terminal.terminal_plan(
        instance, prestow, deadline=1., seed=0, engine_factory=PhysicalOnlyOracle)
    assert status == "timeout" and plan is None
    assert metrics["terminal_cp_solves"] == 1
    assert metrics["terminal_expansions"] == metrics["terminal_candidates"] == 0
    assert metrics["fixed_slot_failure"] == 1


def test_missing_shared_oracle_fails_closed(monkeypatch):
    def missing(*args, **kwargs):
        raise NotImplementedError("shared oracle unavailable")

    monkeypatch.setattr(twostep, "make_feasibility_engine", missing)
    result = twostep.TwoStepPlanner().plan(interchangeable_instance(), seed=1, time_limit_s=5)
    assert result.status == "error"
    assert result.plan is None
    assert "oracle" in result.message.lower() or "feasibility" in result.message.lower()


def test_input_provenance_survives(physical_oracle):
    instance = interchangeable_instance().model_copy(update={
        "provenance": Provenance(stubs={"fixture_oracle": "explicit unit test only"})})
    result = twostep.TwoStepPlanner().plan(instance, seed=1, time_limit_s=5)
    assert result.status == "ok"
    assert result.provenance.stubs == instance.provenance.stubs
    assert result.plan.provenance.stubs == instance.provenance.stubs


@pytest.mark.usefixtures("legacy_rules")
def test_d25_categories_derive_classes_from_actual_mass():
    """Booking interchangeability follows D25 VGM classes, never cached labels."""
    from stowage.rules.catalogue import load_catalogue

    box = interchangeable_instance().containers[0]
    classes = load_catalogue().weight_classes
    categories = {}
    for mass, label in ((9999, "L"), (10000, "M"), (19999, "M"), (20000, "H")):
        assert classes.classify(mass) == label
        categories[mass] = twostep.category_of(box.model_copy(update={
            "gross_mass_kg": mass, "weight_class": "untrusted label"}))
    assert categories[9999] != categories[10000]
    assert categories[10000] == categories[19999]
    assert categories[19999] != categories[20000]
    actual = box.model_copy(update={"gross_mass_kg": 15000})
    assert twostep.category_of(actual) == twostep.category_of(
        actual.model_copy(update={"weight_class": "heavy"}))


def test_terminal_dead_end_reports_fixed_slot_failure(monkeypatch):
    class RefuseTerminal(PhysicalOnlyOracle):
        def legal_actions(self):
            if self.instance.yard.yard_id != "twostep-canonical":
                return []
            return super().legal_actions()

        def copy(self):
            other = RefuseTerminal(self.instance, self.space)
            other.history = self.history[:]
            other._physical_cache = self._physical_cache
            return other

    monkeypatch.setattr(twostep, "make_feasibility_engine", RefuseTerminal)
    result = twostep.TwoStepPlanner().plan(interchangeable_instance(), seed=1, time_limit_s=5)
    assert result.status == "dead_end"
    assert result.plan is None
    assert result.metrics["preplan_status"] == "ok"
    assert result.metrics["terminal_status"] == "dead_end"
    assert result.metrics["fixed_slot_failure"] == 1
    assert len(result.metrics["preplan_slots"]) == 2


@pytest.mark.parametrize("width", [0, -1])
def test_invalid_beam_width_rejected(width):
    with pytest.raises(ValueError, match="positive"):
        twostep.TwoStepPlanner(beam_width=width)
    with pytest.raises(ValueError, match="positive"):
        twostep.preplan(interchangeable_instance(), beam_width=width)


def test_carrier_backtracks_after_locally_legal_dead_end(monkeypatch):
    """Necessary conditions may admit dead ends; another slot must be tried."""
    instance = interchangeable_instance()
    space = get_action_space("boxslot")
    canonical = twostep._canonical_instance(instance)
    oracle = PhysicalOnlyOracle(canonical, space)
    box = min(canonical.containers, key=lambda b: twostep._cargo_key(instance, b)).id
    first = min((a for a in oracle.legal_actions() if a.box == box),
                key=lambda a: twostep._slot_score(canonical, (), a))

    class DeadFirstSlot(PhysicalOnlyOracle):
        def legal_actions(self):
            if self.history and self.history[0] == first:
                return []
            return super().legal_actions()

        def copy(self):
            other = DeadFirstSlot(self.instance, self.space)
            other.history = self.history[:]
            other._physical_cache = self._physical_cache
            return other

    monkeypatch.setattr(twostep, "make_feasibility_engine", DeadFirstSlot)
    pre = twostep.preplan(instance, time_limit_s=5, beam_width=4)
    assert pre.status == "ok", pre.message
    assert pre.actions[0] != first
    assert len(pre.slots) == len(instance.load_list)


def test_terminal_backtracks_assignment_after_local_dead_end(monkeypatch):
    """A rejected CP order gets a no-good; the fixed categories remain intact."""
    instance = interchangeable_instance()
    top = instance.load_list[-1]

    class DeadTopFirst(PhysicalOnlyOracle):
        def legal_actions(self):
            if (self.instance.yard.yard_id != "twostep-canonical" and self.history
                    and self.history[0].box == top):
                return []
            return super().legal_actions()

        def copy(self):
            other = DeadTopFirst(self.instance, self.space)
            other.history = self.history[:]
            other._physical_cache = self._physical_cache
            return other

    monkeypatch.setattr(twostep, "make_feasibility_engine", DeadTopFirst)
    pre = twostep.preplan(instance, time_limit_s=5)
    result = twostep.TwoStepPlanner().plan(instance, seed=2, time_limit_s=5)
    assert result.status == "ok", result.message
    actions = get_action_space("boxslot").from_plan(instance, result.plan)
    assert actions[0].box != top
    assert {a.slot for a in actions} == {s.slot for s in pre.slots}
    assert result.metrics["terminal_cp_solves"] >= 2
    assert result.metrics["terminal_no_goods"] >= 1
    assert result.metrics["num_workers"] == 1


def test_planning_adapter_enumerates_local_choices_without_completion_witness(monkeypatch):
    """Hull-free fixtures isolate local checks; they make no physics claim."""
    from stowage.checker import full
    from stowage.solver.twostep_adapter import make_planning_engine

    def forbidden_witness(*args, **kwargs):
        raise AssertionError("A local action must not need a checker-passing suffix")

    monkeypatch.setattr(full, "check", forbidden_witness)
    instance = interchangeable_instance()
    instance = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"hull": None})})
    engine = make_planning_engine(instance, get_action_space("boxslot"))
    choices = list(engine.legal_actions())
    assert len(choices) > 1
    assert choices == sorted(choices, key=engine.space.sort_key)
    action = choices[0]
    copied = engine.copy()
    copied.push(action)
    assert copied.depth == 1 and copied.actions() == [action]
    assert engine.depth == 0
    assert copied.pop() == action
    assert list(copied.legal_actions()) == choices


def test_planning_adapter_enforces_booking_and_physical_support():
    from stowage.solver.twostep_adapter import make_planning_engine

    instance = interchangeable_instance()
    special = instance.containers[0].model_copy(update={"special_stowage": ["under_deck"]})
    instance = instance.model_copy(update={
        "containers": [special, instance.containers[1]],
        "vessel": instance.vessel.model_copy(update={"hull": None}),
    })
    engine = make_planning_engine(instance, get_action_space("boxslot"))
    assert not engine.is_legal(Place(special.id, "0010182"))
    assert not engine.is_legal(Place(special.id, "0010104"))
    assert engine.is_legal(Place(special.id, "0010102"))
    before = engine.actions()
    with pytest.raises(ValueError):
        engine.push(Place(special.id, "0010182"))
    assert engine.actions() == before


def test_terminal_respects_distinct_destination_categories(physical_oracle):
    instance = interchangeable_instance()
    boxes = [instance.containers[0].model_copy(update={"pod": "THBKK"}),
             instance.containers[1]]
    instance = instance.model_copy(update={"containers": boxes})
    pre = twostep.preplan(instance, seed=3, time_limit_s=5)
    result = twostep.TwoStepPlanner().plan(instance, seed=3, time_limit_s=5)
    assert result.status == "ok", result.message
    categories = {s.slot: s.category for s in pre.slots}
    by_id = {b.id: b for b in boxes}
    assert len(set(categories.values())) == 2
    actions = get_action_space("boxslot").from_plan(instance, result.plan)
    assert {a.slot for a in actions} == set(categories)
    assert all(twostep.category_of(by_id[a.box]) == categories[a.slot] for a in actions)


def test_adapter_delegates_to_public_factory_and_preserves_configuration(monkeypatch):
    """The adapter must preserve the public v2 engine, rather than fork its rules."""
    import stowage.feasibility as feasibility
    from stowage.solver import twostep_adapter

    calls = []
    sentinel = object()

    def factory(instance, space, *, config=None):
        calls.append((instance, space, config))
        return sentinel

    monkeypatch.setattr(twostep_adapter, "make_feasibility_engine", factory)
    instance = interchangeable_instance()
    space = get_action_space("boxslot")
    config = feasibility.FeasibilityConfig(stability_margin_scale=1.25)
    assert twostep_adapter.make_planning_engine(instance, space, config=config) is sentinel
    assert calls == [(instance, space, config)]
    assert twostep_adapter.make_planning_engine(instance) is sentinel
    assert calls[-1] == (instance, space, None)


def test_terminal_surrogate_distinguishes_good_and_bad_yard_orders(monkeypatch):
    """Boolean literals must select top-first zero cost and bottom-first one cost.

    This also runs the CP model with Python 3.12 bool-inversion deprecations as
    errors: complementing a Python bool gives -1/-2, not a CP Boolean literal.
    """
    import warnings
    # Import generated OR-Tools bindings before checking our model operations:
    # OR-Tools 9.15 itself emits bool-inversion warnings during binding import.
    from stowage.solver import twostep_terminal  # noqa: F401

    instance = interchangeable_instance()
    top = instance.load_list[-1]
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with monkeypatch.context() as patch:
            patch.setattr(twostep, "make_feasibility_engine", PhysicalOnlyOracle)
            good = twostep.TwoStepPlanner().plan(instance, seed=5, time_limit_s=5)
        assert good.status == "ok", good.message
        assert good.metrics["terminal_surrogate"] == 0
        assert apply_plan(instance, good.plan).count.relocations == 0

        class BottomFirstOnly(PhysicalOnlyOracle):
            def legal_actions(self):
                if not self.history and self.instance.yard.yard_id != "twostep-canonical":
                    return [a for a in super().legal_actions() if a.box != top]
                return super().legal_actions()

            def copy(self):
                other = BottomFirstOnly(self.instance, self.space)
                other.history = self.history[:]
                other._physical_cache = self._physical_cache
                return other

        monkeypatch.setattr(twostep, "make_feasibility_engine", BottomFirstOnly)
        bad = twostep.TwoStepPlanner().plan(instance, seed=5, time_limit_s=5)
        assert bad.status == "ok", bad.message
        assert bad.metrics["terminal_surrogate"] == 1
        assert apply_plan(instance, bad.plan).count.relocations == 1


@pytest.mark.usefixtures("legacy_rules")
def test_carrier_prefers_public_no_restow_actions(monkeypatch):
    """A good geometric score must not hide a forced hatch/deck restow."""
    instance = interchangeable_instance()
    canonical = twostep._canonical_instance(instance)
    space = get_action_space("boxslot")
    oracle = PhysicalOnlyOracle(canonical, space)
    box = min(canonical.containers, key=lambda b: twostep._cargo_key(instance, b)).id
    tempting = min((a for a in oracle.legal_actions() if a.box == box),
                   key=lambda a: twostep._slot_score(canonical, (), a))
    calls = []

    class RestowAwareOracle(PhysicalOnlyOracle):
        def forces_restow(self, action):
            calls.append(action)
            return action == tempting

        def copy(self):
            other = RestowAwareOracle(self.instance, self.space)
            other.history = self.history[:]
            other._physical_cache = self._physical_cache
            return other

    monkeypatch.setattr(twostep, "make_feasibility_engine", RestowAwareOracle)
    pre = twostep.preplan(instance, time_limit_s=5)
    assert pre.status == "ok", pre.message
    assert tempting in calls
    assert pre.actions[0] != tempting


@pytest.mark.usefixtures("legacy_rules")
def test_d25_category_respects_instance_mass_boundary_overrides():
    instance = interchangeable_instance().model_copy(update={"rule_overrides": {
        "expert.e2.weight_order": {"medium_min_kg": 12000, "heavy_min_kg": 24000}}})
    box = instance.containers[0]
    low = box.model_copy(update={"gross_mass_kg": 11000})
    medium = box.model_copy(update={"gross_mass_kg": 12000})
    assert twostep.category_of(low) == twostep.category_of(medium)
    assert twostep.category_of(low, instance) != twostep.category_of(medium, instance)
    assert twostep.category_of(medium, instance) == twostep.category_of(
        box.model_copy(update={"gross_mass_kg": 23999}), instance)
    assert twostep.category_of(medium, instance) != twostep.category_of(
        box.model_copy(update={"gross_mass_kg": 24000}), instance)


def test_public_model_unavailability_is_error_not_infeasibility(monkeypatch):
    from stowage.feasibility import DeadEnd

    class Unavailable(PhysicalOnlyOracle):
        def legal_actions(self):
            return []

        def dead_end(self):
            return DeadEnd(0, "calibration found no equilibria", ("checker.missing_vessel_data",))

    monkeypatch.setattr(twostep, "make_feasibility_engine", Unavailable)
    result = twostep.TwoStepPlanner().plan(interchangeable_instance(), time_limit_s=5)
    assert result.status == "error"
    assert result.plan is None
    assert "calibration found no equilibria" in result.message
    assert result.metrics["terminal_status"] == "not_started"


@pytest.mark.usefixtures("legacy_rules")
def test_carrier_fills_heavy_hold_before_lighter_later_destination(physical_oracle):
    """E6 departure feasibility outranks furthest-POD ordering across classes."""
    from stowage.conventions import parse_slot

    instance = interchangeable_instance()
    heavy = instance.containers[0].model_copy(update={
        "gross_mass_kg": 25000, "pod": "THBKK"})
    light = instance.containers[1].model_copy(update={
        "gross_mass_kg": 9000, "pod": "THSGZ"})
    instance = instance.model_copy(update={"containers": [heavy, light]})
    pre = twostep.preplan(instance, time_limit_s=5)
    assert pre.status == "ok", pre.message
    assert pre.actions[0].box == heavy.id
    assert not parse_slot(pre.actions[0].slot).on_deck
    assert {action.box for action in pre.actions} == set(instance.load_list)
    assert len(pre.actions) == len(instance.load_list)


@pytest.mark.usefixtures("legacy_rules")
def test_carrier_heavy_hold_priority_precedes_restow_tiebreak(monkeypatch):
    """A deck restow saving cannot displace a compatible class-H hold booking."""
    from stowage.conventions import parse_slot

    instance = interchangeable_instance()
    instance = instance.model_copy(update={"containers": [
        box.model_copy(update={"gross_mass_kg": 25000})
        for box in instance.containers]})

    class TemptingDeck(PhysicalOnlyOracle):
        def forces_restow(self, action):
            return not parse_slot(action.slot).on_deck

        def copy(self):
            other = TemptingDeck(self.instance, self.space)
            other.history = self.history[:]
            other._physical_cache = self._physical_cache
            return other

    monkeypatch.setattr(twostep, "make_feasibility_engine", TemptingDeck)
    pre = twostep.preplan(instance, time_limit_s=5)
    assert pre.status == "ok", pre.message
    assert all(not parse_slot(action.slot).on_deck for action in pre.actions)
    assert len({action.box for action in pre.actions}) == len(instance.load_list)
    assert len({action.slot for action in pre.actions}) == len(instance.load_list)
    assert {slot.slot for slot in pre.slots} == {action.slot for action in pre.actions}


def test_carrier_heavy_on_deck_special_is_not_blocked_by_empty_hold(monkeypatch):
    """E6 considers compatible hold cells; an on-deck booking has none."""
    from stowage.conventions import parse_slot

    instance = interchangeable_instance()
    heavy = instance.containers[0].model_copy(update={
        "gross_mass_kg": 25000, "special_stowage": ["on_deck"]})
    instance = instance.model_copy(update={
        "containers": [heavy], "load_list": [heavy.id],
        "yard": instance.yard.model_copy(update={"stacks": [
            instance.yard.stacks[0].model_copy(update={"boxes": [heavy.id]})]})})

    class BookingOracle(PhysicalOnlyOracle):
        def legal_actions(self):
            return [action for action in super().legal_actions()
                    if parse_slot(action.slot).on_deck]

        def copy(self):
            other = BookingOracle(self.instance, self.space)
            other.history = self.history[:]
            other._physical_cache = self._physical_cache
            return other

    monkeypatch.setattr(twostep, "make_feasibility_engine", BookingOracle)
    pre = twostep.preplan(instance, time_limit_s=5)
    assert pre.status == "ok", pre.message
    assert len(pre.actions) == len(pre.slots) == 1
    assert pre.actions[0].box == heavy.id
    assert parse_slot(pre.actions[0].slot).on_deck
    assert pre.slots[0].category == twostep.category_of(heavy, instance)


def test_carrier_uses_public_index_without_materializing_legal_actions(monkeypatch):
    """v2.3 indices preserve the protocol ordering and fixed category pre-stow."""
    import numpy as np

    instance = interchangeable_instance()
    monkeypatch.setattr(twostep, "make_feasibility_engine", PhysicalOnlyOracle)
    expected = twostep.preplan(instance, time_limit_s=5)
    assert expected.status == "ok", expected.message

    class IndexedOracle(PhysicalOnlyOracle):
        def __init__(self, instance, space, *, config=None):
            super().__init__(instance, space, config=config)
            self.index_boxes = tuple(instance.load_list)
            self.index_slots = tuple(cell.id for cell in instance.vessel.cells)
            self.n_slots = len(self.index_slots)

        def legal_actions(self):
            raise AssertionError("The indexed pre-stow must not enumerate action objects")

        def legal_index(self):
            actions = PhysicalOnlyOracle.legal_actions(self)
            return np.array(sorted(self.index_boxes.index(action.box) * self.n_slots
                                   + self.index_slots.index(action.slot)
                                   for action in actions), dtype=np.int64)

        def decode_index(self, value):
            box, slot = divmod(value, self.n_slots)
            return Place(self.index_boxes[box], self.index_slots[slot])

        def legal_discharges(self):
            return []

        def is_legal(self, action):
            return action in PhysicalOnlyOracle.legal_actions(self)

        def copy(self):
            other = IndexedOracle(self.instance, self.space)
            other.history = self.history[:]
            other._physical_cache = self._physical_cache
            return other

    monkeypatch.setattr(twostep, "make_feasibility_engine", IndexedOracle)
    actual = twostep.preplan(instance, time_limit_s=5)
    assert actual.status == "ok", actual.message
    assert actual.actions == expected.actions
    assert actual.slots == expected.slots
