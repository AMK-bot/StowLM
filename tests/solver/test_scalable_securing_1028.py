"""Real checker geometry evidence; no hydrostatic/securing-load certification."""
from types import SimpleNamespace

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker.lashing import _load_graph
from stowage.model import Placement, ShipState, YardStack, YardState
from stowage.model.fixtures import build_instance
from stowage.solver.securing_geometry import securing_geometry_violation, securing_state_violations


def securing_instance(*, deck=False, unequal=False):
    instance = build_instance()
    boxes = [b.model_copy(update={"dg": None, "oog": None, "special_stowage": [],
                                  "reefer": False, "reefer_setpoint_c": None,
                                  "height_m": 2.591, "gross_mass_kg": 9000., "pod": "THSGZ"})
             for b in instance.containers[:3]]
    assert [b.length_ft for b in boxes] == [40, 20, 20]
    if unequal:
        boxes[2].height_m = 2.896
    tier = "82" if deck else "02"
    instance = instance.model_copy(update={
        "containers": boxes, "load_list": [boxes[0].id], "discharge_list": [],
        "onboard": ShipState(vessel_id=instance.vessel.id, placements=[Placement(container_id=b.id, slot=f"{bay}01{tier}")
                                         for b, bay in zip(boxes[1:], ("001", "003"))]),
        "yard": YardState(yard_id=instance.yard.yard_id,
                          stacks=[YardStack(key="A-001-01", length_ft=40, boxes=[boxes[0].id])])})
    instance.vessel.fixed_loads = []
    return instance


def completed_ship(expander):
    return ShipState(vessel_id=expander.instance.vessel.id, placements=[Placement(container_id=cid, slot=slot)
        for cid, (location, slot) in expander.where.items() if location == "ship"])


def test_mixed_hold_screen_matches_actual_securing_graph():
    instance = securing_instance()
    ex = Expander(instance)
    action = Place(instance.load_list[0], "0020104")
    assert "mixed 20/long hold" in securing_geometry_violation(instance, ex, action)
    ex.step(action)
    assert any("mixed 20/long hold" in reason for reason in securing_state_violations(instance, ex))
    with pytest.raises(ValueError, match="mixed 20/long hold"):
        _load_graph(instance, completed_ship(ex), SimpleNamespace(at=lambda *args: (0., 0., 0.)))


def test_public_local_mask_admits_mixed_hold_that_securing_screen_rejects():
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine

    instance = securing_instance()
    # This test isolates native C1/local placement from qualification of the
    # fixture's placeholder hull. It establishes no production C2 certificate.
    instance.vessel.hull = None
    action = Place(instance.load_list[0], "0020104")
    engine = make_feasibility_engine(instance, get_action_space())
    assert engine.is_legal(action)
    assert securing_geometry_violation(instance, Expander(instance), action) is not None


@pytest.mark.parametrize("unequal", [False, True])
def test_final_deck_support_height_matches_actual_checker_geometry(unequal):
    instance = securing_instance(deck=True, unequal=unequal)
    ex = Expander(instance)
    action = Place(instance.load_list[0], "0020184")
    # Later hold access can restow these supports, so prefix pruning defers.
    assert securing_geometry_violation(instance, ex, action) is None
    reason = securing_geometry_violation(instance, ex, action, include_existing=True)
    assert bool(reason) is unequal
    ex.step(action)
    if unequal:
        assert any("equal actual height" in r for r in securing_state_violations(instance, ex))
        with pytest.raises(ValueError, match="equal actual height"):
            _load_graph(instance, completed_ship(ex), SimpleNamespace(at=lambda *args: (0., 0., 0.)))
    else:
        assert securing_state_violations(instance, ex) == ()
        assert len(_load_graph(instance, completed_ship(ex), SimpleNamespace(at=lambda *args: (0., 0., 0.)))) == 3


def test_pending_discharge_defers_prefix_rejection():
    instance = securing_instance()
    instance.discharge_list = [instance.containers[1].id]
    ex = Expander(instance)
    action = Place(instance.load_list[0], "0020104")
    assert securing_geometry_violation(instance, ex, action) is None
    assert "mixed 20/long hold" in securing_geometry_violation(instance, ex, action, include_existing=True)


def test_geometry_terminal_guard_rejects_existing_twenty_over_long():
    instance = securing_instance(deck=True)
    long, twenty = instance.containers[:2]
    instance.onboard = ShipState(vessel_id=instance.vessel.id, placements=[Placement(container_id=long.id, slot="0020182"),
                                              Placement(container_id=twenty.id, slot="0010184")])
    ex = Expander(instance)
    assert any("20-foot box over" in r for r in securing_state_violations(instance, ex))
    with pytest.raises(ValueError, match="20-foot box over"):
        _load_graph(instance, completed_ship(ex), SimpleNamespace(at=lambda *args: (0., 0., 0.)))


def test_scalable_skips_checker_unsupported_hold_but_retains_valid_deck(monkeypatch):
    from stowage.actions import get_action_space
    from stowage.solver import scalable
    from tests.solver.test_scalable import PhysicalOracle

    class RestrictedOracle(PhysicalOracle):
        """Real move legality, only two destinations; no hydrostatics claim."""
        def is_legal(self, action):
            return action.slot in {"0020104", "0020182"} and super().is_legal(action)

        def copy(self):
            copied = super().copy()
            copied.__class__ = type(self)
            return copied

    monkeypatch.setattr(scalable, "make_feasibility_engine", RestrictedOracle)
    instance = securing_instance()
    result = scalable.ScalableSolver(scalable.ScalableConfig(
        beam_width=1, branch_factor=2, max_neighborhoods=0)).plan(instance, time_limit_s=2.)
    assert result.status == "ok", result.message
    actions = get_action_space().from_plan(instance, result.plan)
    assert actions == [Place(instance.load_list[0], "0020182")]
    assert result.metrics["securing_geometry_rejections"] >= 1


def test_twostep_carrier_backtracks_from_checker_unsupported_hold(monkeypatch):
    from stowage.solver import twostep
    from tests.solver.test_scalable import PhysicalOracle

    class RestrictedOracle(PhysicalOracle):
        """Real move legality, only two destinations; no hydrostatics claim."""
        def is_legal(self, action):
            return action.slot in {"0020104", "0020182"} and super().is_legal(action)

        def copy(self):
            copied = super().copy()
            copied.__class__ = type(self)
            return copied

    monkeypatch.setattr(twostep, "make_feasibility_engine", RestrictedOracle)
    instance = securing_instance()
    result = twostep.preplan(instance, time_limit_s=2.)
    assert result.status == "ok", result.message
    assert result.actions == (Place(instance.load_list[0], "0020182"),)


def test_scalable_does_not_report_unsupported_only_completion_ok(monkeypatch):
    from stowage.solver import scalable
    from tests.solver.test_scalable import PhysicalOracle

    class UnsupportedOnlyOracle(PhysicalOracle):
        def is_legal(self, action):
            return action.slot == "0020104" and super().is_legal(action)

        def copy(self):
            copied = super().copy()
            copied.__class__ = type(self)
            return copied

    monkeypatch.setattr(scalable, "make_feasibility_engine", UnsupportedOnlyOracle)
    result = scalable.ScalableSolver(scalable.ScalableConfig(
        max_neighborhoods=0, max_restarts=0)).plan(securing_instance(), time_limit_s=2.)
    assert result.status == "dead_end"
    assert result.metrics["securing_geometry_rejections"] >= 1
