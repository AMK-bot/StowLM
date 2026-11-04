"""Real feeder hull/bridge/fixed-cargo canaries, independently handwritten moves.

C2 is intentionally off until the shared linear artifacts are requalified. No
physical checker is mocked, no stale artifact is relabelled, and no engine or
expander chooses the witness placements or their lift order.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.actions.expander import Expander
from stowage.checker.rules import placement_violations
from stowage.checker import check
from stowage.checker.full import report_matches_inputs
from stowage.conventions import make_container_id
from stowage.generate.random_voyage import build_voyage_spec
from stowage.generate.random_yard import _tiny_region
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance, MovePlan, BallastCondition, CheckReport
from stowage.model.fixtures import build_instance
from stowage.model.moves import HatchCoverOff, HatchCoverOn, Load
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.yard import YardState, YardStack
from stowage.moves import IllegalMoveError, apply_move, initial_state
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import departure_tank_masses, empty_background_witness_tank_masses
from stowage.vessel.fleet_slices import recondition_slice, slice_preset


def _case(vessel_name, rung, crane_h):
    parent = load_vessel(vessel_name)
    vessel = _tiny_region(parent) if rung == 'tiny' else slice_preset(parent, rung)
    vessel = recondition_slice(vessel, expected_load_mass_kg=29_000., seed=7)
    port = load_port('laem_chabang_like')
    port.max_crane_height_tiers = crane_h
    port.quay_crane.max_on_deck_tiers = crane_h
    rotation = Rotation(service='feasibility-full-checker', calls=[
        PortCall(locode=port.locode, archetype=port.archetype, index=i) for i in (0, 1)])
    hold = sorted((s for s in vessel.stacks if not s.on_deck), key=lambda s: (s.row,s.bay))
    lower = hold[0]
    assert any(s.bay == lower.bay+2 and s.row == lower.row for s in hold)
    cover = next(c for c in vessel.hatch_covers if lower.id in c.hold_stack_ids)
    deck = next(s for s in vessel.stacks if s.on_deck and s.id in cover.deck_stack_ids)
    slot40 = f'{lower.bay+1:03d}{lower.row:02d}{int(lower.cell_ids[0][-2:]):02d}'
    templates = build_instance().containers
    plain = next(b for b in templates if b.length_ft == 20 and not b.dg and not b.oog)
    boxes = [plain.model_copy(update=dict(id=make_container_id('QZWU',9901+i),
             iso_type='42G1' if length == 40 else '22G1', length_ft=length,
             gross_mass_kg=mass, max_gross_mass_kg=30_480., direct_delivery=False,
             height_m=2.591, special_stowage=[], reefer=False,
             pol=port.locode, pod=port.locode)) for i,(length,mass) in enumerate(((40,20_000.),(20,9_000.)))]
    stacks = [YardStack(key=f'{port.yard.blocks[0].id}-{i*3+1:03d}-01', length_ft=b.length_ft, boxes=[b.id])
              for i,b in enumerate(boxes)]
    instance = Instance(id=f'w2-full-{vessel_name}-{rung}-H{crane_h}', stage='A',
        size_class=rung, seed=7, vessel=vessel, rotation=rotation, port=port, call_index=0,
        yard=YardState(yard_id=port.yard.id, stacks=stacks), containers=boxes,
        load_list=[b.id for b in boxes], onboard=ShipState(vessel_id=vessel.id,
        tank_masses_kg=departure_tank_masses(vessel)),
        voyage=build_voyage_spec(rotation,0,port,seed=7))
    actions = [Place(boxes[0].id,slot40), Place(boxes[1].id,deck.cell_ids[0])]
    moves = [HatchCoverOff(call_index=0,cover_id=cover.id),
             Load(call_index=0,container_id=boxes[0].id,src=stacks[0].key+'-01',dst=slot40),
             HatchCoverOn(call_index=0,cover_id=cover.id),
             Load(call_index=0,container_id=boxes[1].id,src=stacks[1].key+'-01',dst=deck.cell_ids[0])]
    tanks = empty_background_witness_tank_masses(vessel)
    adjustable = {t.id for t in vessel.tanks if t.adjustable}
    plan = MovePlan(id=instance.id+'.manual',instance_id=instance.id,call_index=0,moves=moves,
        ballast=BallastCondition(tank_masses_kg={k:v for k,v in tanks.items() if k in adjustable},source='planner'))
    return instance, actions, plan


@pytest.mark.slow
@pytest.mark.parametrize('enabled',[False,True],ids=['legacy','activated'])
@pytest.mark.parametrize('vessel_name',['feeder_500','feeder_800','feeder_1700'])
@pytest.mark.parametrize('rung',['tiny','small'])
@pytest.mark.parametrize('crane_h',[4,8])
def test_real_feeder_full_checker_agreement(h,vessel_name,rung,crane_h,enabled,record_property,monkeypatch):
    # More wall time on the shared machine; the full physics/tolerances are unchanged.
    monkeypatch.setenv("STOWAGE_STABILITY_TIME_BUDGET_S", "120")
    # Freeze witness construction independently of the screen under test.
    with rule_freeze_v1(enabled=False):
        instance, actions, plan = _case(vessel_name,rung,crane_h)
    with rule_freeze_v1(enabled=enabled):
        assert instance.vessel.hull and instance.vessel.bridge and instance.vessel.fixed_loads
        assert {b.length_ft for b in instance.containers} == {20,40}
        assert len({b.gross_mass_kg for b in instance.containers}) == 2
        report = check(instance,plan)
        assert report.passed, [(v.rule_id,v.message) for v in report.violations]
        assert all(p.ran and not p.error for p in report.parts.values())
        if enabled:
            assert 'in_port' in report.parts, 'activated checker must judge every lift'
        assert report_matches_inputs(report, instance, plan)
        # Optional exact-input evidence for a fresh-process engine replay after
        # feasibility-only edits; the physical checker still runs above.
        if directory := os.environ.get('W2_FEAS_AGREEMENT_ARTIFACT_DIR'):
            output = Path(directory)
            output.mkdir(parents=True, exist_ok=True)
            bound = dict(instance=instance.model_dump_json(), plan=plan.model_dump_json(),
                         report=report.model_dump_json())
            evidence = dict(activation=enabled, payloads=bound,
                sha256={key: hashlib.sha256(value.encode()).hexdigest() for key,value in bound.items()},
                actions=[dict(box=action.box,slot=action.slot) for action in actions])
            (output / f'{instance.id}-{int(enabled)}.json').write_text(json.dumps(evidence))
        counts = _screen_counts(instance, actions, plan)
        record_property('full_agreement',dict(counts))
        print(instance.id,'active' if enabled else 'legacy',dict(counts))


def _screen_counts(instance, actions, plan):
    engine = make_feasibility_engine(instance, get_action_space('boxslot'),
        config=FeasibilityConfig(extra={'global_screen': 0}))
    counts = Counter(accepted_completions=1, static_false_rejects=0, engine_false_rejects=0,
                     full_checker_false_rejects=0, engine_false_accepts=0)
    ex, world = Expander(instance), initial_state(instance)
    for action in actions:
        bi,si = engine.T.box_index[action.box],engine.T.slot_index[action.slot]
        static_allowed = bool(engine.T.static[bi,si])
        counts['static_false_rejects'] += not static_allowed
        start = perf_counter()
        offered = engine.legal_actions()
        counts['prefix_us'] += int((perf_counter()-start)*1e6)
        # Disjoint static/dynamic cuts, and their checker-relative union.
        counts['engine_false_rejects'] += static_allowed and action not in offered
        counts['full_checker_false_rejects'] += action not in offered
        for candidate in offered:
            probe, current = ex.copy(), world
            try:
                for move in probe.step(candidate):
                    before = current
                    current = apply_move(instance,current,move)
                if isinstance(candidate,Place):
                    assert not any(v.severity == 'hard' for v in placement_violations(
                        instance,before.ship,engine._boxes[candidate.box],candidate.slot))
            except (ValueError,IllegalMoveError,AssertionError):
                counts['engine_false_accepts'] += 1
            counts['offered_actions'] += 1
        counts['prefixes'] += 1
        assert action in offered, (counts, engine.dead_end())
        for move in ex.step(action):
            world = apply_move(instance,world,move)
        engine.push(action)
    assert engine.is_complete()
    assert engine.to_plan().moves == plan.moves, 'manual full-checker witness and engine replay differ'
    assert counts['engine_false_accepts'] == 0, counts
    assert counts['static_false_rejects'] == counts['engine_false_rejects'] == counts['full_checker_false_rejects'] == 0
    return counts


def replay_bound_evidence(directory):
    """Replay exact full-checker accepted inputs against a freshly imported engine.

    This is a follow-up to the slow physical qualification, not a substitute for
    that test. Only the engine is re-executed; saved checker verdicts remain bound
    to the serialized instance and handwritten plan, with every payload hashed.
    """
    counts = Counter()
    paths = sorted(Path(directory).glob('*.json'))
    assert len(paths) == 24, f'expected all 24 physical witnesses, found {len(paths)}'
    domains = set()
    for path in paths:
        evidence = json.loads(path.read_text())
        payloads = evidence['payloads']
        assert evidence['sha256'] == {key: hashlib.sha256(value.encode()).hexdigest()
                                     for key,value in payloads.items()}
        instance = Instance.model_validate_json(payloads['instance'])
        plan = MovePlan.model_validate_json(payloads['plan'])
        report = CheckReport.model_validate_json(payloads['report'])
        assert report.passed and report_matches_inputs(report,instance,plan)
        if evidence['activation']:
            assert 'in_port' in report.parts
        domains.add((instance.id,evidence['activation']))
        actions = [Place(**data) for data in evidence['actions']]
        with rule_freeze_v1(enabled=evidence['activation']):
            result = _screen_counts(instance,actions,plan)
            counts.update(result)
            print(path.name,dict(result),flush=True)
    expected = {(f'w2-full-{vessel}-{rung}-H{height}',enabled)
                for vessel in ('feeder_500','feeder_800','feeder_1700')
                for rung in ('tiny','small') for height in (4,8) for enabled in (False,True)}
    assert domains == expected
    return counts


def _feasibility_source_sha256():
    import stowage.feasibility
    source_root = Path(stowage.feasibility.__file__).parent
    source_digest = hashlib.sha256()
    for source in sorted(source_root.rglob('*.py')):
        source_digest.update(str(source.relative_to(source_root)).encode())
        source_digest.update(source.read_bytes())
    return source_digest.hexdigest()


if __name__ == '__main__':
    import sys
    before = _feasibility_source_sha256()
    print('TOTAL',dict(replay_bound_evidence(sys.argv[1])))
    after = _feasibility_source_sha256()
    assert before == after, 'feasibility sources changed during bound replay; rerun when stable'
    print('feasibility_source_sha256',after)
