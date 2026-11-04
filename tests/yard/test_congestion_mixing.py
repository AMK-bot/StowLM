"""D72 synthetic congestion deliberately buries pending exports, gated by freeze."""
import pytest

from stowage.conventions import make_container_id
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement, ShipState, WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves import apply_move
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import ShipCall, simulate_port


def mixing_case(*, imported=False, buried=False, direct=False):
    base = build_instance()
    boxes = [base.containers[2].model_copy(update={
        "id": make_container_id("QZXU", 8100 + i), "pod": "SGSIN", "category": None,
        "special_stowage": [], "oog": None, "weight_class": "light",
    }) for i in range(4)]
    target, prior, incoming, background = boxes
    a = base.model_copy(deep=True)
    a.containers = [target]
    a.load_list = [target.id]
    a.discharge_list = []
    a.onboard = ShipState(vessel_id=a.vessel.id)
    b = base.model_copy(deep=True)
    b.containers = [prior, incoming, background]
    b.discharge_list = []
    b.onboard = ShipState(vessel_id=b.vessel.id)
    if imported:
        prior.pod = incoming.pod = background.pod = base.port.locode
        incoming.direct_delivery = direct
        if direct:
            from stowage.model.port import PortRuleRef
            incoming.dg = base.containers[1].dg.model_copy(update={"imdg_class": "1.4S"})
            b.port.port_rules = [PortRuleRef(rule_id="port.dg.direct_delivery", params={"classes": ["1"]})]
            a.port = b.port.model_copy(deep=True)
        b.stage = "B"
        b.load_list = []
        b.discharge_list = [incoming.id]
        b.onboard.placements = [Placement(slot="0010182", container_id=incoming.id)]
        second = ShipCall("b", b, 100, 90, {})
    else:
        b.load_list = [prior.id, incoming.id]
        second = ShipCall("b", b, 2000, 1900, {prior.id: 0, incoming.id: 10})
    calls = [ShipCall("a", a, 1000, 900, {target.id: 0}), second]
    stacks = [YardStack(key="A-001-01", length_ft=20,
                        boxes=[target.id] + ([prior.id] if buried else []))]
    if not buried:
        stacks.append(YardStack(key="A-002-01", length_ft=20, boxes=[prior.id]))
    yard = YardState(yard_id=base.port.yard.id, stacks=stacks)
    return calls, yard, incoming.id, target.id


def stack_for(yard, cid):
    return next(stack for stack in yard.stacks if cid in stack.boxes)


@pytest.mark.parametrize("imported,buried", [(False, False), (True, False), (True, True)])
def test_full_congestion_places_other_boxes_above_pending_exports(imported, buried):
    calls, yard, incoming, target = mixing_case(imported=imported, buried=buried)
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=3,
                               noise=1, congestion_mix_share=1, initial_yard=yard, until_s=101)
    stack = stack_for(result.final_yard, incoming)
    assert target in stack.boxes
    assert stack.boxes.index(incoming) > stack.boxes.index(target)
    assert not result.unserved_discharges
    assert len(stack.boxes) <= 3


def test_zero_congestion_preserves_d25_cluster_preference():
    calls, yard, incoming, target = mixing_case()
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=3,
                               noise=0, initial_yard=yard, until_s=101)
    assert target not in stack_for(result.final_yard, incoming).boxes
    assert stack_for(result.final_yard, incoming).key == "A-002-01"


def test_clustering_error_probability_does_not_force_mixing():
    calls, yard, incoming, target = mixing_case()
    parameters = load_simulator_params("laem_chabang_like")
    parameters = parameters.model_copy(update={"placement": parameters.placement.model_copy(update={
        "cluster_misplacement_probability": 1, "retrieval_order_error_probability": 0,
    })})
    with rule_freeze_v1():
        result = simulate_port(calls, parameters, seed=3, initial_yard=yard, until_s=101)
    assert target not in stack_for(result.final_yard, incoming).boxes
    assert result.provenance.congestion_mixing["configured_share"] == .25


def test_mixing_cannot_put_direct_delivery_import_in_yard():
    calls, yard, incoming, target = mixing_case(imported=True, direct=True)
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=3,
                               noise=1, congestion_mix_share=1, initial_yard=yard, until_s=101)
    assert all(incoming not in stack.boxes for stack in result.final_yard.stacks)
    moves = [record.move for record in result.moves if getattr(record.move, "container_id", None) == incoming]
    assert len(moves) == 1 and moves[0].dst == "truck"


def test_crossing_blocked_mixed_stack_falls_back_to_legal_unmixed_storage():
    calls, yard, incoming, target = mixing_case(imported=True)
    # A8: row 1 at its yard limit screens the export stack in row 2.
    yard.stacks[0].key = "A-001-02"
    prior = yard.stacks[1].boxes[0]
    extras = [calls[1].instance.containers[-1]]
    for serial in (8200, 8201):
        extras.append(extras[0].model_copy(update={"id": make_container_id("QZXU", serial)}))
    calls[1].instance.containers.extend(extras[1:])
    yard.stacks.append(YardStack(key="A-001-01", length_ft=20, boxes=[b.id for b in extras]))
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=3,
                               noise=1, congestion_mix_share=1, initial_yard=yard, until_s=101)
        assert target not in stack_for(result.final_yard, incoming).boxes
        assert len(stack_for(result.final_yard, incoming).boxes) <= 3
        # Engine replay independently verifies every recorded placement leg.
        registry = [box for call in calls for box in call.instance.containers]
        context = calls[1].instance.model_copy(update={"containers": registry})
        state = WorldState(call_index=context.call_index, yard=yard, ship=context.onboard)
        for record in result.moves:
            state = apply_move(context, state, record.move)
    assert not result.unserved_discharges


@pytest.mark.parametrize("crossing_blocked", [False, True])
def test_serialized_achieved_share_with_populated_eligibility(crossing_blocked):
    """Count actual mixing, including selected boxes that require legal fallback."""
    from dataclasses import asdict

    from pydantic_core import to_jsonable_python
    from stowage.yard import simulator

    calls, yard, incoming, target = mixing_case(imported=True)
    if crossing_blocked:
        yard.stacks[0].key = "A-001-02"
        extras = [calls[1].instance.containers[-1]]
        extras.extend(extras[0].model_copy(update={"id": make_container_id("QZXU", serial)})
                      for serial in (8200, 8201))
        calls[1].instance.containers.extend(extras[1:])
        yard.stacks.append(YardStack(key="A-001-01", length_ft=20,
                                    boxes=[box.id for box in extras]))
    with rule_freeze_v1():
        result = simulator.simulate_port(
            calls, load_simulator_params("laem_chabang_like"), seed=3,
            noise=1, congestion_mix_share=1, initial_yard=yard, until_s=101,
        )
    policy = to_jsonable_python(asdict(result))["provenance"]["congestion_mixing"]
    assert policy["eligible_boxes"] == policy["mixing_selected_boxes"] == 1
    assert policy["mixed_boxes"] == (0 if crossing_blocked else 1)
    assert policy["achieved_share"] == policy["mixed_boxes"] / policy["eligible_boxes"]
    assert (target in stack_for(result.final_yard, incoming).boxes) is not crossing_blocked
    assert not result.unserved_discharges


def test_off_keeps_legacy_dispersal_instead_of_forced_mixing():
    calls, yard, incoming, target = mixing_case()
    with rule_freeze_v1(enabled=False):
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=3,
                               noise=1, congestion_mix_share=1, initial_yard=yard, until_s=101)
    assert target not in stack_for(result.final_yard, incoming).boxes


def test_import_can_bury_its_own_call_export_and_forces_a_counted_rehandle():
    calls, yard, incoming, target = mixing_case(imported=True)
    a, b = calls
    instance = a.instance.model_copy(update={
        "containers": a.instance.containers + b.instance.containers,
        "discharge_list": b.instance.discharge_list,
        "onboard": b.instance.onboard, "stage": "B",
    })
    own = ShipCall("own", instance, 100, 90, {target: 0})
    with rule_freeze_v1():
        result = simulate_port([own], load_simulator_params("laem_chabang_like"), seed=3,
                               noise=1, congestion_mix_share=1, initial_yard=yard)
    assert target in stack_for(result.snapshots[0].yard, incoming).boxes
    assert sum(record.move.kind == "relocate" for record in result.moves) == 1
    assert not result.unserved_loads
    assert not result.unserved_discharges


@pytest.mark.parametrize("restriction", ["full", "reefer", "dg"])
def test_mixing_never_overrides_storage_restrictions(restriction):
    calls, yard, incoming, target = mixing_case()
    second = calls[1].instance
    box = next(b for b in second.containers if b.id == incoming)
    if restriction == "full":
        extra = second.containers[-1]
        another = extra.model_copy(update={"id": make_container_id("QZXU", 8300)})
        second.containers.append(another)
        yard.stacks[0].boxes.extend([extra.id, another.id])
    elif restriction == "reefer":
        box.reefer = True
        yard.stacks[0].key = "A-001-02"  # Only row 1 is powered.
    else:
        box.dg = build_instance().containers[1].dg
        # Only B is zoned for DG; the pending export is in A.
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=3,
                               noise=1, congestion_mix_share=1, initial_yard=yard, until_s=101)
    stack = stack_for(result.final_yard, incoming)
    assert target not in stack.boxes
    assert len(stack.boxes) <= 3
    if restriction == "reefer":
        assert stack.key.endswith("-01")
    elif restriction == "dg":
        assert stack.key.startswith("B-")
    assert not result.external_queue


def test_zero_noise_queues_other_call_export_instead_of_using_spare_export_tiers():
    calls, yard, incoming, target = mixing_case()
    for call in calls:
        call.instance.port.yard.blocks = call.instance.port.yard.blocks[:1]
        call.instance.port.yard.blocks[0].n_bays = call.instance.port.yard.blocks[0].n_rows = 1
    yard.stacks = yard.stacks[:1]
    # Existing own-call prior inventory would otherwise request another gate-in;
    # register it as background instead, leaving one incoming export event.
    second = calls[1]
    second.instance.load_list = [incoming]
    calls[1] = ShipCall(second.id, second.instance, 2000, 1900, {incoming: 10})
    with rule_freeze_v1():
        result = simulate_port(calls, load_simulator_params("laem_chabang_like"), seed=3,
                               noise=0, initial_yard=yard, until_s=101)
    assert incoming in result.external_queue
    assert stack_for(result.final_yard, target).boxes == [target]


def test_zero_noise_keeps_own_call_import_separate_even_when_call_prefix_matches():
    calls, yard, incoming, target = mixing_case(imported=True)
    a, b = calls
    instance = a.instance.model_copy(update={
        "containers": a.instance.containers + b.instance.containers,
        "discharge_list": b.instance.discharge_list,
        "onboard": b.instance.onboard, "stage": "B",
    })
    own = ShipCall("own", instance, 100, 90, {target: 0})
    yard.stacks = yard.stacks[:1]
    with rule_freeze_v1():
        result = simulate_port([own], load_simulator_params("laem_chabang_like"), seed=3,
                               noise=0, initial_yard=yard)
    assert target not in stack_for(result.snapshots[0].yard, incoming).boxes
    assert not result.unserved_discharges
    assert not any(record.move.kind == "relocate" for record in result.moves)


def test_exports_waiting_for_a_berth_remain_mixing_targets_after_scheduled_arrival():
    calls, yard, incoming, target = mixing_case()
    a, b = calls
    empty = a.instance.model_copy(update={"containers": [], "load_list": [], "discharge_list": []})
    occupying = ShipCall("occupying", empty, 100, 90, {}, service_s=2000)
    b = ShipCall(b.id, b.instance, 3000, 2900,
                 {cid: 1100 if cid == incoming else 0 for cid in b.instance.load_list})
    parameters = load_simulator_params("laem_chabang_like").model_copy(update={"berth_count": 1})
    with rule_freeze_v1():
        result = simulate_port([occupying, a, b], parameters, seed=3, noise=1, congestion_mix_share=1,
                               initial_yard=yard, until_s=1101)
    assert result.berth_wait_s["a"] == 1100
    assert target in stack_for(result.final_yard, incoming).boxes


def test_queued_box_keeps_its_first_mixing_selection_on_retry(monkeypatch):
    import stowage.yard.simulator as simulator

    calls, yard, incoming, target = mixing_case()
    a, b = calls
    prior, _, background = b.instance.containers
    top = prior.model_copy(update={"id": make_container_id("QZXU", 8400)})
    b.instance.load_list = [incoming]
    b = ShipCall("b", b.instance, 2000, 1900, {incoming: 10})
    c_instance = a.instance.model_copy(update={"containers": [top], "load_list": [top.id]})
    release = ShipCall("release", c_instance, 100, 90, {top.id: 0})
    yard.stacks = [YardStack(key="A-001-02", length_ft=20, boxes=[target]),
                   YardStack(key="A-001-01", length_ft=20, boxes=[prior.id, background.id, top.id])]
    original = simulator.destinations

    def restricted(context, world, cid, **kwargs):
        options = original(context, world, cid, **kwargs)
        return [dst for dst in options if dst.startswith("A-001-02-")] if cid == incoming else options

    monkeypatch.setattr(simulator, "destinations", restricted)
    original_random = simulator.Random
    draws = []

    class MixingCoins:
        def random(self):
            draws.append(True)
            return 0 if len(draws) == 1 else 1

    def independent_random(seed):
        return MixingCoins() if seed == "1:congestion-mixing:independent-v2" else original_random(seed)

    monkeypatch.setattr(simulator, "Random", independent_random)
    with rule_freeze_v1():
        # Initial mixing coin selects the box. A second coin would reject it,
        # but the retry must retain the first selection and consume no new coin.
        # First A8 crossing fails; loading the top near-row box permits retry.
        result = simulate_port([a, b, release], load_simulator_params("laem_chabang_like"),
                               seed=1, noise=.5, initial_yard=yard, until_s=101)
    assert result.queued_gate_events == 1
    assert incoming not in result.external_queue
    assert target in stack_for(result.final_yard, incoming).boxes
    counters = result.__dict__["_congestion_mixing"]
    assert counters["eligible_boxes"] == counters["mixing_selected_boxes"] == counters["mixed_boxes"] == 1
    assert counters["eligible_placements"] == counters["mixing_attempts"] == 2
    assert len(draws) == 1
