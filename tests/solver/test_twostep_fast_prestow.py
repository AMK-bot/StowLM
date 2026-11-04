"""Scoped carrier mechanics; synthetic fixture is not hydrostatic evidence."""
from __future__ import annotations

import pytest

from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id, parse_slot
from stowage.feasibility import FeasibilityConfig
from stowage.feasibility._engine import IncrementalEngine
from stowage.model import YardStack, YardState
from stowage.model.fixtures import build_instance
from stowage.solver import twostep


@pytest.fixture(autouse=True)
def bounded_greedy_only(monkeypatch):
    """Isolate fallback mechanics from the separately tested constructor."""
    from stowage.solver import twostep_construct
    monkeypatch.setattr(twostep_construct, "construct_preplan", lambda *a, **kw: (
        twostep.PreStowPlan(status="dead_end", message="mock primary search failure"),
        {"constructor_status": "dead_end"}))


def small_cargo(count=8):
    instance = build_instance()
    prototype = instance.containers[2]
    boxes = [prototype.model_copy(update={
        "id": make_container_id(SYNTHETIC_OWNER_CODES[0], 800 + i),
        "special_stowage": [], "pod": "THSGZ",
    }) for i in range(count)]
    return instance.model_copy(update={
        "size_class": "small", "containers": boxes,
        "vessel": instance.vessel.model_copy(update={"hull": None}),
        "load_list": [b.id for b in boxes],
        "yard": YardState(yard_id=instance.yard.yard_id, stacks=[
            YardStack(key=f"A-{i % 6 + 1:03d}-{i // 6 + 1:02d}",
                      length_ft=20, boxes=[b.id]) for i, b in enumerate(boxes)]),
    })


def test_fast_prestow_never_builds_whole_manifest_legal_set(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("fast carrier must query cached per-box domains")
    monkeypatch.setattr(IncrementalEngine, "legal_actions", forbidden)
    monkeypatch.setattr(IncrementalEngine, "legal_index", forbidden)
    instance = small_cargo()
    result = twostep.preplan(instance, time_limit_s=5.,
        feasibility_config=FeasibilityConfig(extra={"global_screen": 0}))
    assert result.status == "ok", result.message
    assert {s.representative_box for s in result.slots} == set(instance.load_list)
    assert len(result.actions) == 8


def test_fast_prestow_does_not_observe_actual_yard():
    instance = small_cargo()
    changed = instance.model_copy(update={"yard": instance.yard.model_copy(update={
        "stacks": [s.model_copy(update={"boxes": instance.yard.stacks[-1-i].boxes})
                   for i, s in enumerate(instance.yard.stacks)],
    })})
    before = instance.model_dump()
    config = FeasibilityConfig(extra={"global_screen": 0})
    a = twostep.preplan(instance, time_limit_s=5., feasibility_config=config)
    b = twostep.preplan(changed, time_limit_s=5., feasibility_config=config)
    assert a.status == b.status == "ok"
    assert a.slots == b.slots and a.actions == b.actions
    assert instance.model_dump() == before


@pytest.mark.parametrize("rung", ["tiny", "small", "medium", "large", "full"])
def test_fast_dispatch_preserves_tiny_and_out_of_scope_rungs(rung):
    instance = small_cargo().model_copy(update={"size_class": rung})
    assert twostep._use_fast_prestow(instance) == (rung in {"small", "medium"})


def test_fast_deadline_returns_no_partial_cargo():
    result = twostep.preplan(small_cargo(), time_limit_s=0.)
    assert result.status == "timeout"
    assert result.slots == result.actions == ()


def test_deck_only_heavy_bookings_wait_for_medium_hold_work():
    instance = small_cargo()
    instance = instance.model_copy(update={"containers": [
        b.model_copy(update={"gross_mass_kg": 28000., "special_stowage": ["on_deck"]})
        if i < 2 else b for i, b in enumerate(instance.containers)]})
    result = twostep.preplan(instance, time_limit_s=5.,
        feasibility_config=FeasibilityConfig(extra={"global_screen": 0}))
    assert result.status == "ok", result.message
    deck = [i for i, a in enumerate(result.actions) if parse_slot(a.slot).on_deck]
    hold = [i for i, a in enumerate(result.actions) if not parse_slot(a.slot).on_deck]
    assert hold and deck and max(hold) < min(deck)
    assert len({a.box for a in result.actions}) == len(result.actions) == 8


def test_full_holds_skip_empty_domains_for_remaining_manifest(monkeypatch):
    from stowage.solver.twostep_adapter import _ScopedPlanningEngine
    original = _ScopedPlanningEngine.legal_slots_for_box
    queries = []
    def counted(self, cid):
        if not self.has_open_hold_slots():
            queries.append((self.depth, cid))
        return original(self, cid)
    monkeypatch.setattr(_ScopedPlanningEngine, "legal_slots_for_box", counted)
    result = twostep.preplan(small_cargo(12), time_limit_s=5.,
        feasibility_config=FeasibilityConfig(extra={"global_screen": 0}))
    assert result.status == "ok", result.message
    # Four deck loads remain once the eight hold cells are filled. Each prefix
    # should query its chosen box, rather than every unplaced box for hold.
    assert queries and len(queries) == len({depth for depth, _ in queries})
