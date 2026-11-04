"""Rule-freeze C1/C2/C4 necessary-screen regressions (synthetic cargo)."""
import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.feasibility._legacy import CompletionCapacity
from stowage.model import Instance, Placement
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


def case(lower=12000., upper=13000., *, onboard=True, oog=None):
    d = build_instance().model_dump()
    base = dict(next(b for b in d['containers'] if b['length_ft'] == 20),
                iso_type='22G1', height_m=2.591, tare_kg=2200., dg=None, oog=None,
                reefer=False, reefer_setpoint_c=None, special_stowage=[], pod='THBKK')
    from stowage.conventions import make_container_id
    boxes = [dict(base, id=make_container_id('QZWU', i + 801), gross_mass_kg=m)
             for i, m in enumerate((lower, upper))]
    boxes[0]['oog'] = oog
    d['containers'] = boxes
    d['load_list'] = [boxes[1]['id']] if onboard else [b['id'] for b in boxes]
    d['onboard']['placements'] = ([dict(slot='0010182', container_id=boxes[0]['id'])] if onboard else [])
    d['yard']['stacks'] = [dict(key=f'A-00{i + 1}-01', length_ft=20, boxes=[b['id']])
                           for i, b in enumerate(boxes) if b['id'] in d['load_list']]
    d['rule_overrides'] = {}
    return Instance.model_validate(d)


def engine(inst, *, screens=True):
    return make_feasibility_engine(inst, get_action_space('boxslot'), config=FeasibilityConfig(
        extra={'global_screen': 0, 'completion_screens': int(screens)}))


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('screens', [False, True])
def test_exact_mass_rejects_heavier_within_same_legacy_class(enabled, screens):
    inst = case()
    a = Place(inst.load_list[0], '0010184')
    with rule_freeze_v1(enabled=enabled):
        assert (a in engine(inst, screens=screens).legal_actions()) is (not enabled)


@pytest.mark.parametrize('tolerance,allowed', [(999., False), (1000., True)])
def test_shared_kg_tolerance(tolerance, allowed):
    inst = case()
    inst.rule_overrides['expert.e2.weight_order'] = {'weight_order_tolerance_kg': tolerance}
    with rule_freeze_v1():
        assert (Place(inst.load_list[0], '0010184') in engine(inst).legal_actions()) is allowed


def test_capacity_caps_use_exact_mass_and_update_after_push():
    inst = case(onboard=False)
    with rule_freeze_v1():
        e = engine(inst)
        e.push(Place(inst.containers[0].id, '0010182'))
        j = e.T.stack_index[next(c.stack_id for c in inst.vessel.cells if c.id == '0010182')]
        assert e._caps(e._cur)[j] == 12000.
        assert e.copy()._caps(e.copy()._cur)[j] == 12000.
        e.pop()
        assert e._caps(e._cur)[j] == float('inf')


@pytest.mark.parametrize('enabled', [False, True])
def test_e6_is_soft_in_engine_and_legacy_capacity(enabled):
    inst = case(lower=25000., upper=15000., onboard=False)
    heavy = inst.containers[0].id
    with rule_freeze_v1(enabled=enabled):
        e = engine(inst)
        assert bool([a for a in e.legal_actions() if isinstance(a, Place) and a.box == heavy
                     and a.slot.endswith('82')]) is enabled
        assert ('0010182' in CompletionCapacity(inst).candidates[heavy]) is enabled


@pytest.mark.parametrize('enabled', [False, True])
def test_legacy_capacity_keeps_eventually_supported_upper_cells(enabled):
    inst = case(lower=13000., upper=12000., onboard=False)
    for box in inst.containers:
        box.special_stowage = ['on_deck']
    for cell in inst.vessel.cells:
        cell.accepts_20 = cell.stack_id == '00101D'
    with rule_freeze_v1(enabled=enabled):
        # Both boxes fit this two-tier column, heavier first. Testing the
        # future upper cell against today's empty support would falsely prune.
        assert CompletionCapacity(inst).violations(inst.onboard, inst.load_list) == ()


@pytest.mark.parametrize('enabled', [False, True])
def test_tiny_overwidth_kills_adjacent_slots_and_capacity(enabled):
    inst = case(oog={'over_width_port_m': .01}, lower=15000., upper=12000.)
    a = Place(inst.load_list[0], '0010282')
    with rule_freeze_v1(enabled=enabled):
        e = engine(inst)
        assert (a in e.legal_actions()) is (not enabled)
        if enabled:
            j = e.T.stack_index[next(c.stack_id for c in inst.vessel.cells if c.id == '0010282')]
            assert e._capacity_state(e._cur)['usable'][j] == 0


def fixed_lower_case(*, mass=12000., slot='0010182'):
    """Split one synthetic lower support from decision geometry as a fixed box."""
    from stowage.model.vessel import FixedCargoBox, FixedLoad
    inst = case(lower=mass, upper=13000.)
    vessel = inst.vessel
    cell = next(c for c in vessel.cells if c.id == slot)
    stack = next(s for s in vessel.stacks if s.id == cell.stack_id)
    vessel.id, vessel.slice_of, vessel.sources = 'synthetic_feas_fixed', None, []
    vessel.fixed_load_cells = [cell.model_copy(deep=True)]
    vessel.fixed_load_stacks = [stack.model_copy(update={'cell_ids': [slot]})]
    vessel.cells = [c for c in vessel.cells if c.id != slot]
    vessel.stacks = [s.model_copy(update={'cell_ids': [c for c in s.cell_ids if c != slot]})
                     for s in vessel.stacks]
    box = FixedCargoBox(cell_ids=[slot], length_ft=20, height_m=2.591, gross_mass_kg=mass,
        x_m=cell.lcg_m, y_m=cell.tcg_m, z_m=cell.vcg_base_m + 2.591 / 2,
        stack_id=stack.id, tier=int(slot[-2:]))
    vessel.fixed_loads = [FixedLoad(x_m=box.x_m, y_m=box.y_m, z_m=box.z_m,
                                   mass_kg=mass, kind='cargo', cargo_boxes=[box])]
    inst.onboard = inst.onboard.model_copy(update={'vessel_id': vessel.id, 'placements': []})
    return inst


@pytest.mark.parametrize('mass', [12000., 14000.])
def test_fixed_lower_gross_mass_is_a_support(mass):
    inst = fixed_lower_case(mass=mass)
    with rule_freeze_v1():
        e = engine(inst)
        a = Place(inst.load_list[0], '0010184')
        if mass < 13000.:
            assert a not in e.legal_actions()
        j = e.T.stack_index[next(c.stack_id for c in inst.vessel.cells if c.id == '0010184')]
        assert e._caps(e._cur)[j] == mass


def test_oog_candidate_cannot_kill_fixed_neighbour():
    from stowage.model.cargo import OutOfGauge
    inst = fixed_lower_case(slot='0010282')
    inst.containers[1].oog = OutOfGauge(over_width_port_m=.01)
    with rule_freeze_v1():
        assert Place(inst.load_list[0], '0010182') not in engine(inst).legal_actions()


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('override', ['unmatched_e1', 'wrong_mode'])
def test_shared_weight_parameter_contract_is_checked(enabled, override):
    inst = case(lower=13000., upper=12000.)
    if override == 'unmatched_e1':
        inst.rule_overrides['expert.e1.heavy_bottom'] = {'weight_order_tolerance_kg': 1000.}
    else:
        inst.rule_overrides['expert.e2.weight_order'] = {'weight_order_mode': 'weight_class'}
    with rule_freeze_v1(enabled=enabled):
        e = engine(inst)
        assert (Place(inst.load_list[0], '0010184') in e.legal_actions()) is (not enabled)


def test_matching_e1_e2_tolerance_overrides_stay_admissible():
    inst = case()
    for rid in ('expert.e1.heavy_bottom', 'expert.e2.weight_order'):
        inst.rule_overrides[rid] = {'weight_order_tolerance_kg': 1000.}
    with rule_freeze_v1():
        assert Place(inst.load_list[0], '0010184') in engine(inst).legal_actions()


def test_active_oog_without_legacy_clearance_uses_checker_contract():
    from stowage.checker.rules import placement_violations
    inst = case(onboard=False, lower=15000., upper=12000., oog={'over_width_port_m': .01})
    with rule_freeze_v1():
        e = engine(inst)
        box = inst.containers[0]
        a = Place(box.id, '0010182')
        assert not [v for v in placement_violations(inst, inst.onboard, box, a.slot,
                                                   call_index=inst.call_index) if v.severity == 'hard']
        assert a in e.legal_actions()
