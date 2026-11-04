"""Truck-only replay must not require nonexistent yard crossing geometry."""
from __future__ import annotations

from hashlib import sha256

import pytest

from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Discharge, Load
from stowage.moves import apply_move, apply_plan, count_moves, initial_state
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1


def truck_only_instance(*, importing=False, ship_height=None, ship_blocked=False):
    # Same inputs as the solver owner's truck_only_instance reproduction; this
    # fixture certifies physical replay only, with no full-hull physics claim.
    data = build_instance(with_dg_doc=True).model_dump()
    box = next(box for box in data['containers'] if box['dg'])
    destination = load_port('small_feeder')
    box.update(direct_delivery=True, gross_mass_kg=10000, special_stowage=[], oog=None,
               pod=data['port']['locode'] if importing else destination.locode)
    data.update(containers=[box], load_list=[] if importing else [box['id']],
                discharge_list=[box['id']] if importing else [], stage='B' if importing else 'A')
    data['yard']['stacks'] = []
    data['port'].update(equipment=[], max_crane_height_tiers=ship_height, port_rules=[dict(
        rule_id='port.dg.direct_delivery', params={'classes': ['3']})])
    data['port']['yard']['blocks'] = []
    if ship_height is not None:
        data['port']['quay_crane']['max_on_deck_tiers'] = ship_height
    data['vessel'].update(hull=None, fixed_loads=[])
    data['rotation']['calls'] = data['rotation']['calls'][:2]
    data['rotation']['calls'][1].update(locode=destination.locode, archetype='small_feeder')
    data['onboard']['placements'] = [dict(container_id=box['id'], slot='0010182')] if importing else []
    if ship_blocked:
        for index, tier in enumerate((82, 84), 900):
            blocker = dict(box, id=make_container_id(SYNTHETIC_OWNER_CODES[0], index), pod=destination.locode)
            data['containers'].append(blocker)
            data['onboard']['placements'].append(dict(container_id=blocker['id'], slot=f'00102{tier}'))
    return Instance.model_validate(data), box['id']


def truck_plan(instance, cid, *, importing=False):
    move = (Discharge(call_index=0, container_id=cid, src='0010182', dst='truck') if importing else
            Load(call_index=0, container_id=cid, src='truck', dst='0010182'))
    return MovePlan(id='truck.only.replay', instance_id=instance.id, call_index=0, moves=[move])


@pytest.mark.parametrize('importing', [False, True])
def test_truck_transfer_without_yard_or_known_ship_crossing_height_replays(importing):
    with rule_freeze_v1():
        instance, cid = truck_only_instance(importing=importing)
        plan = truck_plan(instance, cid, importing=importing)
        before = instance.model_dump_json(), plan.model_dump_json()
        expected = apply_move(instance, initial_state(instance), plan.moves[0])
        result = apply_plan(instance, plan)
        assert result.legal, result.violations
        assert result.final_state == expected
        assert result.count == count_moves(instance, plan)
        assert result.count.total_lifts_now == 1
        assert result.count.relocations == 0
        assert result.d62_crossing.status == 'not_applicable'
        assert result.d62_crossing.ship_lifts_at_or_above_h == 0
        assert result.d62_crossing.yard_lifts_at_or_above_h == 0
        assert result.d62_crossing.rejected_crossing_lifts == 0
        assert (instance.model_dump_json(), plan.model_dump_json()) == before


def test_complete_native_twostep_truck_only_plan_replays():
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.twostep import TwoStepPlanner

    with rule_freeze_v1():
        instance, _ = truck_only_instance()
        planner = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen': 0}))
        result = planner.plan(instance, seed=0, time_limit_s=10)
        assert result.status == 'ok', result.message
        replay = apply_plan(instance, result.plan)
        assert replay.legal, replay.violations
        assert replay.count.loads == replay.count.total_lifts_now == 1
        assert next(move for move in result.plan.moves if move.kind == 'load').src == 'truck'


@pytest.mark.parametrize('importing', [False, True])
def test_truck_endpoints_still_obey_known_ship_crossing_height(importing):
    with rule_freeze_v1():
        instance, cid = truck_only_instance(importing=importing, ship_height=2, ship_blocked=True)
        result = apply_plan(instance, truck_plan(instance, cid, importing=importing))
        assert not result.legal
        assert result.violations[0].rule_id == 'port.e3.quay_crossing'
        assert result.d62_crossing.ship_lifts_at_or_above_h == 1
        assert result.d62_crossing.yard_lifts_at_or_above_h == 0


@pytest.mark.parametrize('importing,expected_bytes', [
    (False, 'b00ec6d9735cd3dfd8292d343b704287e724f1bc55a47d5f1917d24abb25d60d'),
    (True, 'ddabb7d805a6f03ae11f64594a06fc8936c61002d7e3330dac8dfca020e7bce3'),
])
def test_switch_off_result_bytes_match_before_fix(importing, expected_bytes):
    # SHA256 of the full off-switch result JSON captured before the repair;
    # this intentionally retains the legacy rejection of truck endpoints.
    with rule_freeze_v1():
        instance, cid = truck_only_instance(importing=importing)
        plan = truck_plan(instance, cid, importing=importing)
    with rule_freeze_v1(enabled=False):
        result = apply_plan(instance, plan)
    assert sha256(result.model_dump_json().encode()).hexdigest() == expected_bytes
