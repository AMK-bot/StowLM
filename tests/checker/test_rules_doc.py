"""Synthetic DoC permission tests (SOLAS II-2/19 cargo-area authorization).

A Document of Compliance records which dangerous-goods classes and packing
forms a ship space may carry. Empty records grant no permission. These fixture
premises exercise authorization only; they are not a certified vessel document.

Every original authorization invariant runs for explicitly saved-parity schema
1.8 and current schema 1.9. Current SW2-bearing cases supply a registered vessel
design on shared physical solids, so a missing assessment cannot hide a DoC
failure. Separate current guards exercise rejected legacy clearance flags.
"""

from dataclasses import asdict

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.checker.rules_imdg import MISSING, SHIP, STOWAGE
from stowage.model.cargo import DangerousGoods
from tests.checker.test_rules_fixtures import build_instance
from stowage.model.ship import Placement
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.scenarios import context_for
from stowage.model.vessel import FixedLoad, FixedLoadSilhouette, HeatSource

CLOSED = 'packaged_goods_in_closed_freight_containers'


@pytest.fixture(params=['1.8', '1.9'], ids=['saved-parity-schema-1.8', 'current-invariant-schema-1.9'])
def schema_version(request):
    return request.param


def _dg(un=1202, pg='III'):
    entry = lookup_dgl(un, pg)
    return DangerousGoods(
        un_number=f'{un:04d}', imdg_class=entry.hazard_class,
        subsidiary_risks=list(entry.subsidiary_risks), packing_group=pg,
        stowage_category=entry.stowage_category,
        stowage_codes=list(entry.stowage_codes),
        segregation_codes=list(entry.segregation_codes),
    )


def _case(schema_version='1.8'):
    """Explicit 1.8 saved fixture; callers testing current behavior request 1.9."""
    instance = build_instance(with_dg_doc=True)
    instance.schema_version = schema_version
    box = instance.containers[0]
    box.iso_type, box.length_ft, box.height_m = '22G1', 20, 2.591
    box.gross_mass_kg, box.reefer = 12000, False
    box.reefer_setpoint_c, box.oog = None, None
    box.special_stowage, box.pod, box.dg = [], 'THBKK', _dg()
    instance.rule_overrides = {
        'expert.e6.heavy_under_deck': {'heavy_threshold': 20000},
        'expert.e1.heavy_bottom': {'heavy_threshold': 20000},
        'expert.e2.weight_order': {'tolerance': 1000},
        STOWAGE: {'containers': {box.id: {
            'packing_form': CLOSED, 'ctu_closed': True, 'clear_living_quarters': True,
        }}},
    }
    if schema_version == '1.9':
        del instance.rule_overrides[STOWAGE]['containers'][box.id]['clear_living_quarters']
    return instance, box, '0010182'


def _current_sw2_design(instance, box, slot, *, near=False):
    """Declare a scoped synthetic design from actual shared physical solids.

    The zero-extra-mass deckhouse envelope belongs to the already represented
    lightship; visibility and IMDG read the same non-cargo silhouette. The
    engine-room region uses the actual E8 heat-source catalog. These fixtures
    need only one SW2 entry each and never infer a clearance from a box flag.
    """
    def sw2_entries(entry, path='primary'):
        if 'SW2' in set(lookup_dgl(int(entry.un_number), entry.packing_group).stowage_codes) | set(entry.stowage_codes):
            yield path
        for index, child in enumerate(entry.additional):
            yield from sw2_entries(child, f'{path}.additional.{index}')

    paths = list(sw2_entries(box.dg))
    if not paths:
        return
    assert len(paths) == 1, 'each DoC fixture isolates one SW2-bearing consignment'
    bounds = dict(x_min_m=10, x_max_m=50 if near else 20,
                  y_min_m=-4, y_max_m=4, z_min_m=9, z_max_m=26)
    index = len(instance.vessel.fixed_loads)
    instance.vessel.fixed_loads.append(FixedLoad(
        x_m=(bounds['x_min_m'] + bounds['x_max_m']) / 2, y_m=0, z_m=17.5,
        mass_kg=0, kind='non_cargo', silhouette=FixedLoadSilhouette(**bounds)))
    instance.vessel.heat_sources.append(HeatSource(
        id='ER-DOC', kind='engine_room', x_min_m=10, x_max_m=20,
        y_min_m=-4, y_max_m=4, z_min_m=0, z_max_m=9))
    evidence = {'document': 'synthetic DoC fixture vessel design', 'clause': 'IMDG 42-24 7.1.2 SW2',
                'scenario_id': f'doc-design-{paths[0]}'}
    config = instance.rule_overrides[STOWAGE]
    config['placement_geometry'] = {
        'living_quarters_sources': [{'id': 'DOC-DECKHOUSE', 'kind': 'accommodation', 'fixed_load_index': index}],
        'living_quarters_map_complete': True, 'living_quarters_map_provenance': evidence,
    }
    context = context_for(instance, box, paths[0], slot, 0)
    assert context is not None
    record = asdict(context)
    record.pop('slot')
    config['assessment_scenarios'] = {
        evidence['scenario_id']: record | {'document': evidence['document'], 'clause': evidence['clause'],
                                            'allowed_slots': [slot]},
    }


def _out(instance, box, slot, mode, *, prepare_current=True):
    if prepare_current and instance.schema_version == '1.9':
        _current_sw2_design(instance, box, slot)
    partial = instance.onboard.model_copy(update={'placements': []})
    before = partial.model_dump_json()
    if mode == 'incremental':
        violations = placement_violations(instance, partial, box, slot, call_index=0)
    else:
        ship = partial.model_copy(update={'placements': [Placement(slot=slot, container_id=box.id)]})
        violations = check_static_rules(instance, ship, call_index=0).violations
    assert partial.model_dump_json() == before
    assert all(v.severity == 'hard' for v in violations)
    if prepare_current and instance.schema_version == '1.9':
        assert not any('SW2' in v.message for v in violations), 'current DoC invariants have an assessed safe placement'
    return violations


def _doc_violations(violations):
    return [v for v in violations if v.rule_id == STOWAGE and 'Compliance' in v.message]


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('allowed', [True, False], ids=['permitted', 'denied'])
def test_doc_class_permission(mode, allowed, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    if not allowed:
        for area in instance.vessel.dg_doc_areas:
            area.classes_allowed = ['9']
    out = _out(instance, box, slot, mode)
    if allowed:
        assert out == []
    else:
        assert _doc_violations(out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('packing', [CLOSED, 'portable_tank', 'open_freight_container'])
def test_doc_packing_form_permission(mode, packing, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    instance.rule_overrides[STOWAGE]['containers'][box.id]['packing_form'] = packing
    out = _out(instance, box, slot, mode)
    assert bool(_doc_violations(out)) is (packing != CLOSED)
    if packing == CLOSED:
        assert out == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('missing', ['all_areas', 'classes', 'packing', 'mapping', 'unknown_area', 'membership'])
def test_doc_missing_or_empty_permission_fails_closed(mode, missing, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    stack = next(s for s in instance.vessel.stacks if s.id == '00101D')
    area = next(a for a in instance.vessel.dg_doc_areas if a.id == stack.dg_doc_area_id)
    if missing == 'all_areas':
        instance.vessel.dg_doc_areas = []
    elif missing == 'classes':
        area.classes_allowed = []
    elif missing == 'packing':
        area.packing_forms_allowed = []
    elif missing == 'mapping':
        stack.dg_doc_area_id = None
    elif missing == 'unknown_area':
        stack.dg_doc_area_id = 'UNRECORDED'
    else:
        area.stack_ids.remove(stack.id)
    out = _out(instance, box, slot, mode)
    assert any(v.rule_id in {STOWAGE, MISSING} for v in out)
    assert _doc_violations(out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_doc_missing_shipment_packing_fact_fails_closed(mode, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    del instance.rule_overrides[STOWAGE]['containers'][box.id]['packing_form']
    assert any(v.rule_id == MISSING and 'packing' in v.message.lower()
               for v in _out(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('open_source', ['iso', 'slot_override'])
def test_doc_closed_packing_claim_rejects_open_container(mode, open_source, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    if open_source == 'iso':
        box.iso_type = '22U1'
        box.dg.ctu_closed = False
    else:
        del instance.rule_overrides[STOWAGE]['containers'][box.id]['ctu_closed']
        instance.rule_overrides[SHIP] = {'slots': {slot: {'closed': False}}}
    out = _out(instance, box, slot, mode)
    assert any(v.rule_id in {STOWAGE, MISSING} and 'closed' in v.message.lower() for v in out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('allowed', [True, False], ids=['permitted', 'denied'])
def test_doc_subsidiary_risk_permission(mode, allowed, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    box.dg = _dg(1005, None)
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = ['2.3', '8'] if allowed else ['2.3']
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = ['2.3', '8']
    out = _out(instance, box, slot, mode)
    assert bool(_doc_violations(out)) is (not allowed)
    if allowed:
        assert out == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('allowed', [True, False], ids=['permitted', 'denied'])
def test_doc_additional_dg_entry_permission(mode, allowed, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    box.dg.additional = [_dg(1057, None)]
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = ['3', '2.1'] if allowed else ['3']
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = ['3', '2.1']
    out = _out(instance, box, slot, mode)
    assert bool(_doc_violations(out)) is (not allowed)
    if allowed:
        assert out == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('denied_support', [None, '00101D', '00301D'])
def test_doc_forty_foot_requires_both_support_permissions(mode, denied_support, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, _ = _case(schema_version)
    box.iso_type, box.length_ft = '42G1', 40
    if denied_support:
        stack = next(s for s in instance.vessel.stacks if s.id == denied_support)
        area = next(a for a in instance.vessel.dg_doc_areas if a.id == stack.dg_doc_area_id)
        restricted = area.model_copy(update={'id': 'RESTRICTED', 'stack_ids': [stack.id], 'classes_allowed': ['9']})
        area.stack_ids.remove(stack.id)
        stack.dg_doc_area_id = restricted.id
        instance.vessel.dg_doc_areas.append(restricted)
    out = _out(instance, box, '0020182', mode)
    assert bool(_doc_violations(out)) is (denied_support is not None)
    if denied_support is None:
        assert out == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('packing,iso', [
    ('portable_tank', '22T1'), ('tank_container', '22T1'),
    ('open_freight_container', '22U1'), ('bulk', '22B1'),
])
@pytest.mark.parametrize('allowed', [True, False], ids=['permitted', 'denied'])
def test_doc_explicit_nonclosed_packing_forms(mode, packing, iso, allowed, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    box.iso_type = iso
    instance.rule_overrides[STOWAGE]['containers'][box.id]['packing_form'] = packing
    if allowed:
        for area in instance.vessel.dg_doc_areas:
            area.packing_forms_allowed = [packing]
    out = _out(instance, box, slot, mode)
    assert bool(_doc_violations(out)) is (not allowed)
    if allowed:
        assert out == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_doc_structured_permission_does_not_need_legacy_class_list(mode, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = None
    assert _out(instance, box, slot, mode) == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('allowed', [True, False], ids=['permitted', 'denied'])
def test_doc_division_permission_is_exact(mode, allowed, schema_version):
    """Authorization invariant: saved-parity 1.8 and current-behavior 1.9."""
    instance, box, slot = _case(schema_version)
    box.dg = _dg(1057, None)
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = ['2.1']
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = ['2.1'] if allowed else ['2']
    out = _out(instance, box, slot, mode)
    assert bool(_doc_violations(out)) is (not allowed)
    if allowed:
        assert out == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('design', ['missing', 'near', 'far'], ids=[
    'current-schema-1.9-legacy-true-missing-design',
    'current-schema-1.9-legacy-true-beside-accommodation',
    'current-schema-1.9-registered-safe-design-control',
])
def test_current_legacy_clearance_flag_cannot_replace_safe_sw2_placement(mode, design):
    """Current-only 1.9 guard: legacy True grants nothing; safe bound design does."""
    instance, box, slot = _case('1.9')
    box.dg = _dg(1057, None)
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = ['2.1']
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = ['2.1']
    instance.rule_overrides[STOWAGE]['containers'][box.id]['clear_living_quarters'] = True
    _current_sw2_design(instance, box, slot, near=design != 'far')
    if design == 'missing':
        del instance.rule_overrides[STOWAGE]['placement_geometry']
        del instance.rule_overrides[STOWAGE]['assessment_scenarios']
    out = _out(instance, box, slot, mode, prepare_current=False)
    assert not _doc_violations(out)
    if design == 'far':
        assert out == []
    else:
        assert any(v.rule_id == (MISSING if design == 'missing' else STOWAGE) and 'SW2' in v.message for v in out)
