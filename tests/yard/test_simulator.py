"""Deterministic scheduling, crane replay and queue invariants for synthetic yards."""
from dataclasses import replace

import pytest

from stowage.conventions import make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.moves import GateOut
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState, WorldState
from stowage.model.yard import YardStack, YardState
from stowage.moves.engine import apply_move
from stowage.ports import load_simulator_params
from stowage.yard import NearestStackDigOut, ShipCall, YardCapacityError, sample_ship_calls, simulate_port


def template(n=6, offset=100):
    result = build_instance()
    base = result.containers[2]
    boxes = [base.model_copy(update={"id": make_container_id("QZXU", offset+i),
             "pod": "SGSIN", "category": None, "special_stowage": [], "oog": None}) for i in range(n)]
    result.rotation = Rotation(service="straits", calls=[PortCall(index=i, locode=p)
                                for i, p in enumerate(("THLCH", "SGSIN", "MYPKG", "THLCH"))])
    result.containers = boxes
    result.load_list = sorted(b.id for b in boxes)
    result.yard = YardState(yard_id=result.port.yard.id, stacks=[
        YardStack(key=f"A-{i+1:03d}-01", length_ft=20, boxes=[box.id]) for i,box in enumerate(boxes)])
    return Instance.model_validate(result.model_dump())


def call(t=None, *, name="ship-1", arrival=1000, gates=None):
    t = t or template()
    return ShipCall(name,t,arrival,arrival-100,gates or {cid: i*10 for i,cid in enumerate(t.load_list)})


def test_reproducibility_purity_and_actual_loads():
    c = call()
    before = c.instance.model_dump_json()
    p = load_simulator_params("laem_chabang_like")
    one = simulate_port([c],p,seed=14,noise=.5)
    two = simulate_port([c],p,seed=14,noise=.5)
    assert one == two
    assert c.instance.model_dump_json() == before
    assert sum(m.move.kind == "load" for m in one.moves) == 6
    assert not one.unserved_loads
    assert not one.final_ships[c.id].covers_off
    assert one.snapshots[0].moves_before == sum(m.move.kind == "gate_in" for m in one.moves)
    assert one.snapshots[0].statistics["target_presence_fraction"] == 1
    assert len(one.snapshots[0].yard.stacks) > 0
    assert not one.final_yard.stacks
    # Independently replay every recorded lift from an empty yard.
    state = WorldState(call_index=0,yard=YardState(yard_id=c.instance.port.yard.id),ship=c.instance.onboard)
    for record in one.moves:
        state = apply_move(c.instance,state,record.move)
    assert state.ship == one.final_ships[c.id]
    assert not state.yard.stacks


def test_nearest_digout_replays_relocation_and_never_mutates():
    t = template(3)
    ids = t.load_list
    state = WorldState(call_index=0,yard=YardState(yard_id=t.port.yard.id, stacks=[
        YardStack(key="A-001-01",length_ft=20,boxes=ids)]),ship=t.onboard)
    out = NearestStackDigOut().retrieve(t,state,ids[0])
    assert len(out.moves)==2
    assert len(state.yard.stacks)==1
    assert state.yard.stacks[0].boxes==ids
    assert out.source=="A-001-01-01"
    for move in out.moves:
        state=apply_move(t,state,move)
    assert out.state==state
    apply_move(t,state,GateOut(call_index=0,container_id=ids[0],src=out.source))


def test_full_yard_and_cutoff_preserve_external_queue():
    t=template(2)
    t.port.yard.blocks=t.port.yard.blocks[:1]
    b=t.port.yard.blocks[0]
    b.n_bays=b.n_rows=b.max_tiers=1
    t.port.yard_max_tiers=1
    # Runtime context; initial inventory is created through gate events.
    c=call(t)
    p=load_simulator_params("laem_chabang_like")
    result=simulate_port([c],p,seed=3)
    assert result.queued_gate_events==1
    assert len(result.external_queue)==1
    assert result.external_queue==result.missed_cutoffs==result.unserved_loads
    assert result.snapshots[0].statistics["occupancy_teu_fraction"]==1


def test_schedule_samples_typed_parameters_and_reports_conditioning():
    t=template()
    p=load_simulator_params("laem_chabang_like")
    calls,report=sample_ship_calls(t,p,days=7,seed=41,volume_scale=.05)
    again,r2=sample_ship_calls(t,p,days=7,seed=41,volume_scale=.05)
    assert calls==again and report==r2
    assert report.raw_load_boxes>=report.conditioned_load_boxes
    assert report.raw_discharge_boxes>=report.conditioned_discharge_boxes+report.unserved_transfer_arrivals
    assert report.unserved_transfer_arrivals>0
    assert report.conditioning
    assert report.unserved_transfer_demand>0
    assert len({cid for c in calls for cid in c.instance.load_list})==sum(len(c.instance.load_list) for c in calls)
    for c in calls:
        assert all(t<=c.cutoff_s<=c.arrival_s for t in c.gate_in_times.values())
        assert sum(b.length_ft/20 for b in c.instance.containers if b.id in c.instance.load_list)<=t.vessel.nominal_teu


def test_berths_queue_calls_without_overlapping_service():
    a=call(template(2),name="a")
    b=call(template(2,offset=200),name="b")
    p=load_simulator_params("laem_chabang_like").model_copy(update={"berth_count":1})
    r=simulate_port([b,a],p,seed=3)
    assert r.berth_wait_s=={"a":0,"b":a.service_s}
    assert [s.time_s for s in r.snapshots]==[1000,4600]


def test_independent_noise_changes_stack_statistics():
    t=template(6)
    # Equal categories encourage filling existing cluster stacks, while order
    # noise chooses randomly among all stacks matching that cluster.
    p=load_simulator_params("laem_chabang_like")
    low=simulate_port([call(t)],p,seed=32,noise=0)
    high=simulate_port([call(t)],p,seed=32,noise=1)
    assert low.snapshots[0].yard!=high.snapshots[0].yard
    assert low.snapshots[0].statistics["blocked_target_share"]>high.snapshots[0].statistics["blocked_target_share"]


@pytest.mark.parametrize("noise",[-1,1.1,float("nan")])
def test_noise_rejects_invalid_probability(noise):
    with pytest.raises(ValueError,match="noise"):
        simulate_port([call()],load_simulator_params("laem_chabang_like"),seed=1,noise=noise)


def test_gate_schedule_validation():
    c=call()
    with pytest.raises(ValueError,match="cut-off"):
        replace(c,gate_in_times={cid:c.arrival_s for cid in c.instance.load_list})
    with pytest.raises(ValueError,match="exactly"):
        replace(c,gate_in_times={})
    with pytest.raises(ValueError,match="permutation"):
        replace(c,load_order=(c.instance.load_list[0],))


def test_actual_import_discharge_and_noise_independent_gamma_dwell():
    t=template()
    p=load_simulator_params("laem_chabang_like")
    calls,_=sample_ship_calls(t,p,days=7,seed=2,volume_scale=.005)
    low=simulate_port(calls,p,seed=22,noise=0)
    high=simulate_port(calls,p,seed=22,noise=1)
    expected={cid for c in calls for cid in c.instance.discharge_list}
    low_discharge={m.move.container_id:m.time_s for m in low.moves if m.move.kind=="discharge"}
    low_exit={m.move.container_id:m.time_s for m in low.moves if m.move.kind=="gate_out"}
    high_exit={m.move.container_id:m.time_s for m in high.moves if m.move.kind=="gate_out"}
    assert expected==set(low_discharge)==set(low_exit)==set(high_exit)
    assert low_exit==high_exit
    assert all(low_exit[cid]>low_discharge[cid] for cid in expected)
    assert not low.unserved_discharges


def test_mixed_lengths_preserve_intact_pairs_for_later_long_boxes():
    t=template(6)
    base=t.containers[0]
    boxes=[base.model_copy(update={"id":make_container_id("QZXU",300+i),
                                    "length_ft":20 if i<8 else 40,
                                    "iso_type":"22G1" if i<8 else "42G1"}) for i in range(12)]
    # The initial random-yard inventory is discarded by the event calendar; a
    # runtime engine context can register gate arrivals before they are in-yard.
    t=t.model_copy(update={"containers":boxes,"load_list":[b.id for b in boxes]})
    c=call(t)
    r=simulate_port([c],load_simulator_params("laem_chabang_like"),seed=17,noise=0)
    assert not r.unserved_loads
    assert sum(m.move.kind=="load" for m in r.moves)==12
    assert not r.final_ships[c.id].covers_off
