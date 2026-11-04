"""Periodic service boundaries and explicit, replay-safe transfer limitations."""
from collections import Counter

import numpy as np
import pytest

from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.moves import Discharge
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import Placement, ShipState, WorldState
from stowage.model.yard import YardState
from stowage.moves.engine import IllegalMoveError, apply_move
from stowage.ports import load_simulator_params
from stowage.yard import ShipCall, sample_ship_calls, simulate_port


def _template():
    instance = build_instance()
    instance.rotation = Rotation(service="straits", calls=[
        PortCall(index=i, locode=port)
        for i, port in enumerate(("THLCH", "SGSIN", "MYPKG", "THLCH"))
    ])
    return instance


def _parameters(*, jitter=0, services=None):
    parameters = load_simulator_params("laem_chabang_like")
    services = services or parameters.services
    return parameters.model_copy(update={"services": tuple(
        service.model_copy(update={"arrival_jitter_s": jitter}) for service in services
    )})


def test_weekly_count_includes_preorigin_cycles_for_every_service():
    parameters = _parameters()
    calls, _ = sample_ship_calls(_template(), parameters, days=7, seed=41, volume_scale=.001)
    counts = Counter(call.id.rsplit("-", 1)[0].rstrip("-") for call in calls)
    assert counts == {service.id: service.calls_per_week for service in parameters.services}
    second = parameters.services[1]
    assert any(call.id == f"{second.id}--1" and call.arrival_s == 100800 for call in calls)


def _fixed_jitter(monkeypatch, jitter, offset):
    original = np.random.default_rng

    class FixedJitter:
        def __init__(self, *args, **kwargs):
            self.rng = original(*args, **kwargs)

        def uniform(self, low=0, high=1, *args, **kwargs):
            if low == -jitter and high == jitter:
                return offset
            return self.rng.uniform(low, high, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.rng, name)

    monkeypatch.setattr(np.random, "default_rng", FixedJitter)


@pytest.mark.parametrize("offset,cycle", [(216000, -2), (-216000, 3)])
def test_boundary_jitter_includes_pre_and_post_horizon_nominal_cycles(monkeypatch, offset, cycle):
    # Jitter spans several periods: checking just one adjacent cycle is insufficient.
    parameters = _parameters(jitter=259200, services=_parameters().services[:1])
    _fixed_jitter(monkeypatch, 259200, offset)
    calls, _ = sample_ship_calls(_template(), parameters, days=1, seed=19, volume_scale=.001)
    assert [(call.id, call.arrival_s) for call in calls] == [
        (f"{parameters.services[0].id}-{cycle}", 43200)
    ]


def test_boundary_jitter_keeps_zero_and_excludes_horizon(monkeypatch):
    parameters = _parameters(jitter=86400, services=_parameters().services[:1])
    _fixed_jitter(monkeypatch, 86400, 86400)
    calls, _ = sample_ship_calls(_template(), parameters, days=1, seed=19, volume_scale=.001)
    assert [(call.id, call.arrival_s) for call in calls] == [
        (f"{parameters.services[0].id}--1", 0)
    ]


def test_jittered_service_identity_is_stable_when_horizon_is_extended():
    parameters = _parameters(jitter=10800)
    short, short_report = sample_ship_calls(_template(), parameters, days=7, seed=17, volume_scale=.001)
    again, again_report = sample_ship_calls(_template(), parameters, days=7, seed=17, volume_scale=.001)
    long, _ = sample_ship_calls(_template(), parameters, days=14, seed=17, volume_scale=.001)
    assert (short, short_report) == (again, again_report)
    assert {call.id: call.arrival_s for call in short} == {
        call.id: call.arrival_s for call in long if call.arrival_s < 604800
    }


def test_intermediate_transfer_discharge_requires_an_external_contract_extension():
    # This is a passing limitation regression, not a substitute for connections:
    # onward POD must remain intact, and the engine must reject intermediate discharge.
    template = _template()
    box = template.containers[2].model_copy(update={"pod": "SGSIN", "category": None,
                                                   "special_stowage": [], "oog": None})
    inbound = template.model_copy(update={
        "containers": [box], "load_list": [], "discharge_list": [box.id],
        "onboard": ShipState(vessel_id=template.vessel.id,
                             placements=[Placement(slot="0010182", container_id=box.id)]),
        "yard": YardState(yard_id=template.port.yard.id),
    })
    with pytest.raises(ValueError, match="discharge container POD must be current port"):
        Instance.model_validate(inbound.model_dump())
    state = WorldState(call_index=inbound.call_index, yard=inbound.yard, ship=inbound.onboard)
    with pytest.raises(IllegalMoveError, match="this port as its destination"):
        apply_move(inbound, state, Discharge(call_index=0, container_id=box.id,
                                            src="0010182", dst="A-001-01-01"))
    outbound_instance = template.model_copy(update={
        "containers": [], "load_list": [], "discharge_list": [],
        "onboard": ShipState(vessel_id=template.vessel.id),
        "yard": YardState(yard_id=template.port.yard.id),
    })
    inbound_call = ShipCall("inbound", inbound, 0, 0, {})
    outbound_call = ShipCall("outbound", outbound_instance, 1000, 900, {})
    result = simulate_port([inbound_call, outbound_call], _parameters(), seed=3)
    assert result.unserved_discharges == [box.id]
    assert result.final_ships["inbound"].placements == inbound.onboard.placements
    assert not result.final_ships["outbound"].placements
    assert not result.final_yard.stacks
    assert all(snapshot.statistics["boxes"] == 0 for snapshot in result.snapshots)
    assert not any(record.move.kind in ("gate_in", "discharge", "load") for record in result.moves)
    assert box.pod == "SGSIN"
