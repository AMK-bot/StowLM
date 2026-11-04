"""Activated static screens; synthetic fixtures exercise necessary, not final, verdicts."""
from dataclasses import asdict

import pytest

from stowage.feasibility.tables import build_tables
from stowage.model.cargo import DangerousGoods
from stowage.model.fixtures import build_instance
from stowage.model.port import PortRuleRef
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.scenarios import context_for


def _case(*, dg=False):
    inst = build_instance(with_dg_doc=True)
    box = next(b for b in inst.containers if b.id in inst.load_list and b.length_ft == 20)
    box.gross_mass_kg, box.height_m, box.iso_type = 25000, 2.591, '22G1'
    box.special_stowage, box.reefer, box.oog = [], False, None
    box.reefer_setpoint_c, box.pod, box.dg = None, 'THBKK', None
    inst.rule_overrides['expert.e6.heavy_under_deck'] = {
        'heavy_threshold': 20000, 'allow_on_deck_if_no_compatible_hold_cell': False}
    if dg:
        entry = lookup_dgl(1202, 'III')
        box.dg = DangerousGoods(un_number='1202', packing_group='III', imdg_class=entry.hazard_class,
            stowage_category=entry.stowage_category, stowage_codes=[*entry.stowage_codes, 'SW2'],
            subsidiary_risks=list(entry.subsidiary_risks), packing_form='packaged_goods_in_closed_freight_containers',
            ctu_closed=True)
        box.gross_mass_kg = 12000
        inst.rule_overrides['imdg.stowage.3_2'] = {'placement_geometry': {}}
    return inst, box


def _accepted(tables, box, slot):
    return bool(tables.static[tables.box_index[box.id], tables.slot_index[slot]])


@pytest.mark.parametrize('active', [False, True])
def test_e6_static_is_soft_only_when_activated(active):
    inst, box = _case()
    with rule_freeze_v1(enabled=active):
        tables = build_tables(inst)
    assert _accepted(tables, box, '0010182') is active
    assert tables.extra.get('rule_freeze_v1', False) is active


@pytest.mark.parametrize('active', [False, True])
def test_refused_current_handling_screen(active):
    inst, box = _case(dg=True)
    inst.schema_version = '1.8'
    inst.rule_overrides['imdg.stowage.3_2'] = {'containers': {box.id: {'clear_living_quarters': True}}}
    inst.port.port_rules.append(PortRuleRef(rule_id='port.dg.refused_class', params={'classes': ['3']}))
    with rule_freeze_v1(enabled=active):
        tables = build_tables(inst)
    assert _accepted(tables, box, '0010182') is (not active)


def test_later_refusal_uses_shared_predicate(monkeypatch):
    import stowage.checker.rules as rules
    inst, box = _case(dg=True)
    seen = []
    def refused(rotation, call_index, pod, primary_class, *, current_port):
        seen.append((pod, primary_class))
        return [1]
    monkeypatch.setattr(rules, 'refused_later_handling_calls', refused)
    with rule_freeze_v1(enabled=True):
        tables = build_tables(inst)
    assert (box.pod, box.dg.imdg_class) in seen
    assert not tables.static[tables.box_index[box.id]].any()


def test_upper_tier_unknown_support_geometry_is_retained():
    inst, box = _case(dg=True)
    # No upper support state can establish its actual floor yet.
    with rule_freeze_v1(enabled=True):
        tables = build_tables(inst)
    assert _accepted(tables, box, '0010184')


def _registered_geometry(inst, box, slots):
    from stowage.model.vessel import FixedLoad, FixedLoadSilhouette
    evidence = {'document': 'synthetic table regression', 'clause': 'SW2', 'scenario_id': 'table-design'}
    # The deck stack starts at z=9.0. Lower tier intersects this actual shared
    # deckhouse; the upper tier can be supported high enough to clear it.
    inst.vessel.fixed_loads.append(FixedLoad(x_m=40, y_m=-1.25, z_m=9, mass_kg=0,
        kind='non_cargo', silhouette=FixedLoadSilhouette(
            x_min_m=35, x_max_m=45, y_min_m=-3, y_max_m=0, z_min_m=8, z_max_m=9)))
    near_index = len(inst.vessel.fixed_loads)-1
    # The existing bridge point also requires a corresponding shared solid;
    # it is physically remote from these candidate slots.
    inst.vessel.fixed_loads.append(FixedLoad(x_m=15, y_m=0, z_m=25, mass_kg=0,
        kind='non_cargo', silhouette=FixedLoadSilhouette(
            x_min_m=10, x_max_m=20, y_min_m=-3, y_max_m=3, z_min_m=20, z_max_m=26)))
    cfg = inst.rule_overrides['imdg.stowage.3_2']
    cfg['placement_geometry'] = {'living_quarters_sources': [
        {'id': 'shared-house', 'kind': 'accommodation', 'fixed_load_index': near_index},
        {'id': 'bridge-house', 'kind': 'accommodation', 'fixed_load_index': near_index+1}],
        'living_quarters_map_complete': True, 'living_quarters_map_provenance': evidence}
    ctx = asdict(context_for(inst, box, 'primary', slots[0], inst.call_index))
    ctx.pop('slot')
    cfg['assessment_scenarios'] = {'table-design': ctx | evidence | {'allowed_slots': slots}}


def test_slot_cache_distinguishes_actual_envelope_and_optimistic_supports():
    inst, box = _case(dg=True)
    slots = ['0010182', '0010184']
    _registered_geometry(inst, box, slots)
    with rule_freeze_v1(enabled=True):
        tables = build_tables(inst)
    assert not _accepted(tables, box, slots[0])
    assert _accepted(tables, box, slots[1])


def test_vertical_screen_uses_actual_candidate_height():
    inst, box = _case(dg=True)
    slots = ['0010182', '0010184']
    _registered_geometry(inst, box, slots)
    box.height_m = 3.8  # SYNTHETIC tall candidate cannot fit above the 3 m exclusion.
    with rule_freeze_v1(enabled=True):
        tables = build_tables(inst)
    assert not _accepted(tables, box, slots[1])


def test_other_slots_cannot_reuse_registered_clearance():
    inst, box = _case(dg=True)
    _registered_geometry(inst, box, ['0010182'])
    with rule_freeze_v1(enabled=True):
        tables = build_tables(inst)
    # The unsafe registered slot must not poison other physical footprints;
    # unknown registry scope remains eligible for an actual supported check.
    assert not _accepted(tables, box, '0010182')
    assert _accepted(tables, box, '0010282')


@pytest.mark.parametrize('known_safe', [False, True])
def test_actual_engine_resolves_static_unknown_supported_geometry(h, known_safe):
    from stowage.actions.space import Place
    from stowage.checker.rules import placement_violations
    from stowage.model import Placement
    from stowage.model.yard import YardStack

    inst, box = _case(dg=True)
    support = next(b for b in inst.containers if b.id != box.id and b.length_ft == 20)
    support.dg, support.oog, support.special_stowage = None, None, []
    support.reefer, support.reefer_setpoint_c = False, None
    support.gross_mass_kg, support.height_m, support.pod = 15000, 3.2, 'THBKK'
    inst.containers, inst.load_list = [box, support], [box.id]
    inst.onboard.placements = [Placement(container_id=support.id, slot='0010182')]
    inst.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[box.id])]
    inst.vessel.fixed_loads = []
    if known_safe:
        _registered_geometry(inst, box, ['0010184'])
    with rule_freeze_v1(enabled=True):
        eng = h.engine(inst, h.DIAG)
        assert _accepted(eng.T, box, '0010184')
        actual = placement_violations(inst, inst.onboard, box, '0010184')
        assert any(v.severity == 'hard' for v in actual) is (not known_safe)
        assert eng.is_legal(Place(box.id, '0010184')) is known_safe


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('source', ['yard', 'quay_buffer', 'truck'])
def test_empty_decision_geometry_has_empty_eligibility(active, source):
    """C5 needs no dummy destination when the vessel has no decision slots."""
    from stowage.model import Instance

    inst, box = _case()
    data = inst.model_dump()
    data['vessel'].update(cells=[], stacks=[], hatch_covers=[], dg_doc_areas=[])
    data['containers'] = [box.model_dump()]
    data['onboard']['placements'] = []
    data['discharge_list'] = []
    data['load_list'] = [box.id]
    data['yard'].update(stacks=[], quay_buffer=[])
    if source == 'yard':
        data['yard']['stacks'] = [{'key': 'A-001-01', 'length_ft': 20, 'boxes': [box.id]}]
    elif source == 'quay_buffer':
        data['yard']['quay_buffer'] = [box.id]
    else:
        data['containers'][0]['direct_delivery'] = True
    # Validate independently of activation: the truck case exercises a prepared
    # transport marker without needing an unrelated port class policy fixture.
    with rule_freeze_v1(enabled=False):
        inst = Instance.model_validate(data)
    with rule_freeze_v1(enabled=active):
        tables = build_tables(inst)
    assert tables.slot_ids == []
    assert tables.box_ids == [box.id]
    assert tables.static.shape == (1, 0)


@pytest.mark.parametrize('opt_in', [False, True])
def test_archived_perf_collection_requires_explicit_opt_in(h, opt_in):
    """Archived provenance must not import into default current-source tests."""
    from pathlib import Path
    from types import SimpleNamespace

    config = SimpleNamespace(getoption=lambda name: opt_in)
    for name in ('witness', 'dg', 'equivalence'):
        path = Path(__file__).with_name(f'test_perf_{name}_1028.py')
        assert h.pytest_ignore_collect(path, config) is (None if opt_in else True)
    assert h.pytest_ignore_collect(Path(__file__), config) is None
