"""Activated C5/C6 probes; original behavior remains selected with the gate off."""
from __future__ import annotations

import pytest

from stowage.actions.samples import tiny_instance
from stowage.actions.space import Discharge, Place
from stowage.model import Placement
from stowage.model.port import PortRuleRef
from stowage.model.yard import YardStack
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1


def _plain(n_load=2, n_other=2):
    inst = tiny_instance(91, n_load=n_load, n_other=n_other, p40=0, max_tiers=2)
    inst.vessel.hull = None
    inst.vessel.fixed_loads = []
    inst.port.max_crane_height_tiers = 2
    for box in inst.containers:
        box.gross_mass_kg = 9000
    inst.yard.stacks = [YardStack(key=f"A-{i + 1:03d}-01", length_ft=20, boxes=[b.id])
                        for i, b in enumerate(inst.containers)]
    return inst


@pytest.mark.parametrize("enabled", [False, True])
def test_engine_uses_shared_crossing_probe(h, enabled):
    inst = _plain(n_load=1)
    a, bottom, top = [b.id for b in inst.containers]
    inst.onboard.placements = [Placement(container_id=bottom, slot="0010282"),
                               Placement(container_id=top, slot="0010284")]
    inst.yard.stacks = [s for s in inst.yard.stacks if s.boxes == [a]]
    with rule_freeze_v1(enabled=enabled):
        eng = h.engine(inst, h.DIAG)
        assert eng.is_legal(Place(a, "0010182")) is (not enabled)


@pytest.mark.parametrize("enabled", [False, True])
def test_engine_screens_self_blocking_with_exhaustive_future_targets(h, enabled):
    inst = _plain(n_load=2, n_other=1)
    a, future, bottom = [b.id for b in inst.containers]
    inst.onboard.placements = [Placement(container_id=bottom, slot="0010282")]
    inst.yard.stacks = [s for s in inst.yard.stacks if bottom not in s.boxes]
    box = next(b for b in inst.containers if b.id == future)
    box.reefer, box.reefer_setpoint_c = True, -18
    box.special_stowage = ["on_deck"]
    for cell in inst.vessel.cells:
        cell.reefer_plug = cell.id.startswith("00101") and cell.id.endswith(("82", "84"))
    with rule_freeze_v1(enabled=enabled):
        eng = h.engine(inst, h.DIAG)
        assert eng.is_legal(Place(a, "0010284")) is (not enabled)


def test_pending_domains_keep_temporarily_unsupported_slots(h):
    from stowage.feasibility._port_screen import pending_target_domains

    inst = _plain()
    with rule_freeze_v1():
        eng = h.engine(inst, h.DIAG)
        domains = pending_target_domains(eng.T, eng._cur)
        assert "0010184" in domains[inst.load_list[0]]


@pytest.mark.parametrize("gate", [False, True])
def test_d64_probes_each_expanded_lift_only_after_checker_gate(h, monkeypatch, gate):
    from types import SimpleNamespace
    from stowage.feasibility import in_port

    inst = _plain(n_load=1, n_other=0)
    calls = []
    monkeypatch.setattr(in_port, "in_port_screen_enabled", lambda: gate)
    def screen(instance, ship):
        calls.append(ship.model_copy(deep=True))
        # Opening the cover is itself a checked state; reject the next cargo lift.
        return SimpleNamespace(status="reject" if ship.placements else "feasible",
                               rule_id="port.stability.cargo_operations")
    monkeypatch.setattr(in_port, "screen_in_port_prefix", screen)
    monkeypatch.setattr(in_port, "InPortPrefixScreen", lambda instance, ship:
                        SimpleNamespace(evaluate=lambda candidate: screen(instance, candidate)))
    with rule_freeze_v1():
        eng = h.engine(inst, h.DIAG)
        action = Place(inst.load_list[0], "0010102")
        assert eng.is_legal(action) is (not gate)
        if gate:
            assert any(s.covers_off and not s.placements for s in calls)
            assert any(s.placements for s in calls)
        else:
            assert calls == []


def test_d64_rejects_initial_prefix_only_with_checker_gate(h, monkeypatch):
    from stowage.feasibility import in_port

    monkeypatch.setattr(in_port, "in_port_screen_enabled", lambda: True)
    monkeypatch.setattr(in_port, "screen_in_port_prefix", lambda *a: in_port.InPortResult("reject", "root fails"))
    with rule_freeze_v1():
        eng = h.engine(_plain(n_load=1, n_other=0), h.DIAG)
        assert eng.legal_actions() == ()
        assert eng.dead_end().rule_ids == ("port.stability.cargo_operations",)


def test_yard_crossing_uses_yard_height_even_below_quay_height(h):
    inst = _plain(n_load=1, n_other=2)
    inst.port.max_crane_height_tiers = 8
    inst.port.quay_crane.max_on_deck_tiers = 8
    a, lower, upper = [b.id for b in inst.containers]
    inst.yard.stacks = [YardStack(key="A-001-02", length_ft=20, boxes=[a]),
                        YardStack(key="A-001-01", length_ft=20, boxes=[lower, upper])]
    with rule_freeze_v1():
        eng = h.engine(inst, h.DIAG)
        action = Place(a, "0010182")
        # A8's expander migration is complete: the full yard row below quay H
        # is cleared with one counted rehandle, and feasibility must offer it.
        from stowage.actions.expander import Expander
        expected = Expander(inst).can_expand(action)
        assert expected
        assert eng.is_legal(action) is expected
        eng.push(action)
        plan = eng.to_plan()
        assert [m.kind for m in plan.moves] == ["relocate", "load"]
        result = apply_plan(inst, plan)
        assert result.legal, result.violations
        assert result.count.relocations == 1


@pytest.mark.parametrize("discharge", [False, True])
def test_direct_delivery_has_no_yard_source_and_completes(h, discharge):
    # Synthetic class-3 truck policy exercises source semantics independently
    # of unsupported explosive service permissions.
    from stowage.model.fixtures import build_instance

    inst = build_instance(with_dg_doc=True)
    inst.vessel.hull = None
    inst.vessel.fixed_loads = []
    box = next(b for b in inst.containers if b.dg)
    box.direct_delivery = True
    inst.port.port_rules = [PortRuleRef(
        rule_id="port.dg.direct_delivery", params={"classes": ["3"]})]
    from stowage.ports import load_port
    destination = load_port("small_feeder")
    inst.rotation.calls = inst.rotation.calls[:2]
    inst.rotation.calls[1].locode = destination.locode
    inst.rotation.calls[1].archetype = "small_feeder"
    box.pod = destination.locode
    inst.containers = [box]
    inst.load_list = [] if discharge else [box.id]
    inst.discharge_list = [box.id] if discharge else []
    inst.yard.stacks = []
    inst.onboard.placements = [Placement(container_id=box.id, slot="0010182")] if discharge else []
    if discharge:
        box.pod = inst.port.locode
    with rule_freeze_v1():
        eng = h.engine(inst, h.DIAG)
        assert not eng.is_complete()
        action = Discharge(box.id) if discharge else Place(box.id, "0010182")
        assert eng.is_legal(action)
        eng.push(action)
        assert eng.is_complete()
        plan = eng.to_plan()
        result = apply_plan(inst, plan)
        assert result.legal, result.violations
        assert result.count.total_lifts_now == 1
        move = plan.moves[0]
        assert (move.dst if discharge else move.src) == "truck"
