"""Solver-review reproductions; mask acceptance is not full-physics certification."""
from time import monotonic

import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Discharge, Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver import twostep


def truck_only_instance():
    data = build_instance(with_dg_doc=True).model_dump()
    box = next(box for box in data['containers'] if box['dg'])
    box.update(direct_delivery=True, gross_mass_kg=10000, special_stowage=[], oog=None)
    data.update(containers=[box], load_list=[box['id']], discharge_list=[])
    data['yard']['stacks'] = []
    data['port'].update(equipment=[], port_rules=[dict(
        rule_id='port.dg.direct_delivery', params={'classes': ['3']})])
    data['port']['yard']['blocks'] = []
    data['vessel'].update(hull=None, fixed_loads=[])
    destination = load_port('small_feeder')
    data['rotation']['calls'] = data['rotation']['calls'][:2]
    data['rotation']['calls'][1].update(locode=destination.locode, archetype='small_feeder')
    box['pod'] = destination.locode
    return Instance.model_validate(data)


def test_direct_delivery_without_yard_equipment_keeps_native_truck_action():
    with rule_freeze_v1():
        instance = truck_only_instance()
        config = FeasibilityConfig(extra={'global_screen': 0})
        canonical = twostep._canonical_instance(instance)
        assert canonical.port.equipment == []
        assert canonical.port.yard.blocks == []
        assert canonical.yard.stacks == []
        engine = make_feasibility_engine(canonical, get_action_space(), config=config)
        action = Place(instance.load_list[0], '0010182')
        assert engine.is_legal(action)
        engine.push(action)
        assert engine.is_complete()
        assert engine.to_plan().moves[0].src == 'truck'


def test_truck_only_two_step_planner_replays_without_yard_equipment(record_property):
    """Exercise the planner and observer after the move-owner geometry fix.

    This hull-free routing fixture certifies move/mask replay only; it is not
    a post-requalification full-checker canary.
    """
    from stowage.moves import apply_plan

    with rule_freeze_v1():
        instance = truck_only_instance()
        config = FeasibilityConfig(extra={'global_screen': 0})
        result = twostep.TwoStepPlanner(feasibility_config=config).plan(instance, time_limit_s=5)
        assert result.status == 'ok', result.message
        assert result.plan is not None
        replay = apply_plan(instance, result.plan)
        assert replay.legal, replay.violations
        assert replay.count.loads == 1 and replay.count.relocations == 0
        loads = [move for move in result.plan.moves if move.kind == 'load']
        assert len(loads) == 1 and loads[0].src == 'truck'
        engine = make_feasibility_engine(instance, get_action_space(), config=config)
        for action in get_action_space().from_plan(instance, result.plan):
            engine.push(action)
        assert engine.is_complete()
        record_property('truck_only_replay', {'status': result.status,
            'moves': len(result.plan.moves), 'loads': replay.count.loads,
            'relocations': replay.count.relocations, 'mask_complete': True})


def test_ordinary_unlocated_export_is_rejected_by_instance_validator():
    with rule_freeze_v1():
        data = truck_only_instance().model_dump()
        data['containers'][0].update(dg=None, direct_delivery=False)
        with pytest.raises(ValueError, match='every load_list container must be in the yard'):
            Instance.model_validate(data)


def import_without_yard_equipment(*, direct_delivery):
    data = truck_only_instance().model_dump()
    box = data['containers'][0]
    box.update(pod=data['port']['locode'], direct_delivery=direct_delivery)
    if not direct_delivery:
        box['dg'] = None
    data.update(stage='B', load_list=[], discharge_list=[box['id']])
    data['onboard']['placements'] = [{'container_id': box['id'], 'slot': '0010182'}]
    return Instance.model_validate(data)


@pytest.mark.parametrize('direct_delivery', [False, True])
def test_no_equipment_ordinary_import_refused_but_direct_delivery_uses_truck(direct_delivery):
    with rule_freeze_v1():
        instance = import_without_yard_equipment(direct_delivery=direct_delivery)
        canonical = twostep._canonical_instance(instance)
        assert canonical.port.yard.blocks == []
        assert canonical.yard.stacks == []
        engine = make_feasibility_engine(canonical, get_action_space(),
            config=FeasibilityConfig(extra={'global_screen': 0}))
        action = Discharge(instance.discharge_list[0])
        assert engine.is_legal(action) is direct_delivery
        if direct_delivery:
            engine.push(action)
            assert engine.is_complete()
            assert engine.to_plan().moves[0].dst == 'truck'


def same_class_yard_instance():
    from stowage.actions.samples import tiny_instance
    from stowage.model import YardStack
    instance = tiny_instance(71, n_load=2, n_other=0, p40=0)
    a, b = instance.containers
    boxes = [box.model_copy(update={'gross_mass_kg': mass, 'pod': 'THBKK',
              'dg': None, 'oog': None, 'reefer': False, 'special_stowage': []})
             for box, mass in ((a, 12001), (b, 12000))]
    return instance.model_copy(update={'containers': boxes,
        'vessel': instance.vessel.model_copy(update={'hull': None, 'bridge': None}),
        'yard': instance.yard.model_copy(update={'stacks': [
            YardStack(key='A-001-01', length_ft=20, boxes=[a.id, b.id])]})})


def test_d73_terminal_substitutes_same_class_top_box_and_saves_rehandle():
    from stowage.moves import apply_plan
    from stowage.solver.twostep_terminal import terminal_plan
    with rule_freeze_v1():
        instance = same_class_yard_instance()
        a, b = instance.load_list
        boxes = {box.id: box for box in instance.containers}
        # Carrier uses its own cargo order; terminal must choose the top box for
        # the first far-row slot despite the two boxes' distinct exact masses.
        pre = twostep.PreStowPlan(slots=tuple(
            twostep.SlotCategory(slot, twostep.category_of(boxes[cid], instance), cid)
            for cid, slot in ((a, '0010182'), (b, '0010282'))))
        config = FeasibilityConfig(extra={'global_screen': 0})
        plan, metrics, status, message = terminal_plan(instance, pre,
            deadline=monotonic()+3, seed=0, engine_factory=make_feasibility_engine,
            feasibility_config=config)
        assert status == 'ok', message
        actions = get_action_space().from_plan(instance, plan)
        assert actions[0] == Place(b, '0010182')
        replay = apply_plan(instance, plan)
        assert replay.legal and replay.count.relocations == 0
        carrier_plan = get_action_space().expand(instance, [Place(a, '0010182'), Place(b, '0010282')])
        assert apply_plan(instance, carrier_plan).count.relocations > replay.count.relocations


def test_d73_bounded_slot_relocation_requires_mask_approval():
    from stowage.solver.twostep_terminal import terminal_plan
    with rule_freeze_v1():
        instance = same_class_yard_instance()
        cid = instance.load_list[-1]
        box = instance.containers[-1]
        instance = instance.model_copy(update={'containers': [box], 'load_list': [cid],
            'yard': instance.yard.model_copy(update={'stacks': [instance.yard.stacks[0].model_copy(
                update={'boxes': [cid]})]})})
        pre = twostep.PreStowPlan(slots=(twostep.SlotCategory(
            '0010182', twostep.category_of(box, instance), cid),))
        config = FeasibilityConfig(extra={'global_screen': 0})
        checked = []
        def factory(instance, space, **kwargs):
            native = make_feasibility_engine(instance, space, **kwargs)
            original_legal = native.is_legal
            def is_legal(action):
                checked.append(action)
                # Controlled additional carrier constraint: the original slot
                # is unavailable. Every alternative still passes the real mask.
                return action.slot != '0010182' and original_legal(action)
            native.is_legal = is_legal
            return native
        plan, metrics, status, message = terminal_plan(instance, pre,
            deadline=monotonic()+3, seed=0, engine_factory=factory, feasibility_config=config)
        assert status == 'ok', message
        actions = get_action_space().from_plan(instance, plan)
        assert len(actions) == 1 and actions[0].slot != '0010182'
        assert actions[0] in checked
        assert metrics['carrier_approval_adjustments'] == 1
        assert metrics['carrier_approval_adjustment_limit'] == 4


def test_d73_same_booked_category_does_not_relax_native_kg_order():
    with rule_freeze_v1():
        instance = same_class_yard_instance()
        heavier, lighter = instance.containers
        assert twostep.category_of(heavier, instance) == twostep.category_of(lighter, instance)
        engine = make_feasibility_engine(instance, get_action_space(),
            config=FeasibilityConfig(extra={'global_screen': 0}))
        bottom = Place(lighter.id, '0010182')
        assert engine.is_legal(bottom)
        engine.push(bottom)
        assert not engine.is_legal(Place(heavier.id, '0010184'))


def test_d73_relocation_refused_by_mask_never_counts_as_carrier_approval():
    from stowage.solver.twostep_terminal import terminal_plan
    with rule_freeze_v1():
        instance = same_class_yard_instance()
        cid = instance.load_list[-1]
        box = instance.containers[-1]
        instance = instance.model_copy(update={'containers': [box], 'load_list': [cid],
            'yard': instance.yard.model_copy(update={'stacks': [instance.yard.stacks[0].model_copy(
                update={'boxes': [cid]})]})})
        pre = twostep.PreStowPlan(slots=(twostep.SlotCategory(
            '0010182', twostep.category_of(box, instance), cid),))
        class RefusingOracle:
            def __init__(self, *args, **kwargs):
                pass
            def is_legal(self, action):
                return False
            def legal_actions(self):
                return []
        plan, metrics, status, _ = terminal_plan(instance, pre,
            deadline=monotonic()+1, seed=0, engine_factory=RefusingOracle)
        assert plan is None and status == 'dead_end'
        assert metrics['carrier_approval_adjustments'] == 0
        assert metrics['terminal_candidates'] == 0


def test_d73_booking_category_preserves_iso_type_with_either_freeze_setting():
    instance = same_class_yard_instance()
    box = instance.containers[0]
    other_type = box.model_copy(update={'iso_type': '22G0'})
    with rule_freeze_v1(enabled=False):
        assert twostep.category_of(box, instance) != twostep.category_of(other_type, instance)
    with rule_freeze_v1():
        assert twostep.category_of(box, instance) != twostep.category_of(other_type, instance)
