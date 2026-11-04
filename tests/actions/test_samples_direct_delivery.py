"""The seeded action sampler retains truck exports under the activated contract."""
from __future__ import annotations

import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import random_actions
from stowage.actions.space import Discharge, Place
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, initial_state
from stowage.rules.catalogue import rule_freeze_v1


def _truck_only_instance() -> tuple[Instance, str]:
    payload = build_instance().model_dump(mode="json")
    box = next(box for box in payload["containers"] if box["length_ft"] == 20)
    box["dg"] = dict(un_number="0004", imdg_class="1.4S", stowage_category="01")
    box["direct_delivery"] = True
    payload["containers"] = [box]
    payload["load_list"] = [box["id"]]
    payload["yard"]["stacks"] = []
    payload["port"]["port_rules"] = [
        dict(rule_id="port.dg.direct_delivery", params={"classes": ["1"]}),
    ]
    return Instance.model_validate(payload), box["id"]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("p_relocate", [0.0, 1.0])
def test_sampler_retains_direct_exports_only_when_activated(enabled, p_relocate):
    with rule_freeze_v1(enabled=enabled):
        instance, cid = _truck_only_instance()
        actions = random_actions(instance, 7, p_relocate=p_relocate)
        if not enabled:
            assert actions == []  # Preserve the previous non-yard-load filtering.
            return
        assert len(actions) == 1
        assert isinstance(actions[0], Place)
        assert actions[0].box == cid
        ex = Expander(instance)
        moves = ex.step(actions[0])
        loads = [move for move in moves if move.kind == "load"]
        assert len(loads) == 1
        assert loads[0].src == "truck"
        state = initial_state(instance)
        for move in moves:
            state = apply_move(instance, state, move)
        assert {p.container_id for p in state.ship.placements} == {cid}
        assert not state.yard.stacks


def test_direct_truck_actions_round_trip_with_one_ship_side_lift():
    from stowage.actions import get_action_space
    from stowage.actions.space import Discharge
    from stowage.model.ship import Placement

    with rule_freeze_v1():
        instance, cid = _truck_only_instance()
        space = get_action_space()
        action = Place(cid, '0010182')
        loaded = space.expand(instance, [action])
        assert len(loaded.moves) == 1 and loaded.moves[0].src == 'truck'
        assert space.from_plan(instance, loaded) == [action]
        instance.load_list = []
        instance.discharge_list = [cid]
        instance.containers[0].pod = instance.port.locode
        instance.onboard.placements = [Placement(container_id=cid, slot='0010182')]
        discharged = space.expand(instance, [Discharge(cid)])
        assert len(discharged.moves) == 1 and discharged.moves[0].dst == 'truck'
        assert space.from_plan(instance, discharged) == [Discharge(cid)]
        state = apply_move(instance, initial_state(instance), discharged.moves[0])
        assert state.yard == instance.yard and not state.ship.placements


def test_sampler_direct_import_with_empty_yard():
    """Seed 0 previously sampled a yard target despite an accepted truck discharge."""
    with rule_freeze_v1():
        instance, cid = _truck_only_instance()
        payload = instance.model_dump(mode="json")
        payload["load_list"] = []
        payload["discharge_list"] = [cid]
        payload["containers"][0]["pod"] = payload["port"]["locode"]
        payload["onboard"]["placements"] = [dict(container_id=cid, slot="0010182")]
        instance = Instance.model_validate(payload)

        # Pin that this validated input has a legal one-lift engine completion.
        ex = Expander(instance)
        moves = ex.step(Discharge(cid))
        assert len(moves) == 1 and moves[0].dst == "truck"
        expected = apply_move(instance, initial_state(instance), moves[0])

        actions = random_actions(instance, 0, p_relocate=0)
        assert actions == [Discharge(cid)]
        actual = initial_state(instance)
        for action in actions:
            for move in Expander(instance).step(action):
                actual = apply_move(instance, actual, move)
        assert actual == expected
        assert not actual.ship.placements and not actual.yard.stacks
