"""Explicit legacy-parity and current shipment contracts via public checker APIs.

IMDG DGL column 16 conditions need shipment evidence. Unknown evidence cannot
clear a condition; explicit false/empty declarations cannot inherit permission.
Legacy flag/fallback examples use schema 1.8 explicitly. Current invariant
counterparts use 1.9 with bound shipment and shared vessel declarations.
"""

import pytest
from dataclasses import asdict

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.checker.rules_imdg import GENERAL, MISSING, SHIP, STOWAGE
from stowage.model.cargo import DangerousGoods
from tests.checker.test_rules_fixtures import build_instance
from stowage.model.ship import Placement
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.scenarios import context_for

CLOSED = 'packaged_goods_in_closed_freight_containers'


def _dg(un=1057, pg=None):
    entry = lookup_dgl(un, pg)
    return DangerousGoods(
        un_number=f'{un:04d}', imdg_class=entry.hazard_class, packing_group=pg,
        subsidiary_risks=list(entry.subsidiary_risks),
        stowage_category=entry.stowage_category,
        stowage_codes=list(entry.stowage_codes),
        segregation_codes=list(entry.segregation_codes),
    )


def _case(source='model', un=1057, pg=None):
    instance = build_instance(with_dg_doc=True)
    instance.schema_version = '1.8'
    box = instance.containers[0]
    box.iso_type, box.length_ft, box.height_m = '22G1', 20, 2.591
    box.tare_kg, box.gross_mass_kg = 2200, 12000
    box.reefer, box.reefer_setpoint_c, box.oog = False, None, None
    box.special_stowage, box.pod, box.dg = [], 'THBKK', _dg(un, pg)
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = ['2.1', '3', '6.2', '9']
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = ['2.1', '3', '6.2', '9']
    instance.rule_overrides = {STOWAGE: {'containers': {box.id: {}}}}
    facts = {'packing_form': CLOSED, 'ctu_closed': True, 'clear_living_quarters': True}
    if source == 'model':
        for key, value in facts.items():
            setattr(box.dg, key, value)
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id] = facts
    return instance, box, '0010182'


def _check(instance, box, slot, mode, others=()):
    ship = instance.onboard.model_copy(update={'placements': list(others)})
    before = ship.model_dump_json()
    if mode == 'incremental':
        out = placement_violations(instance, ship, box, slot, call_index=0)
    else:
        ship = ship.model_copy(update={'placements': [*others, Placement(slot=slot, container_id=box.id)]})
        out = check_static_rules(instance, ship, call_index=0).violations
    if mode == 'incremental':
        assert ship.model_dump_json() == before
    assert all(v.severity == 'hard' for v in out)
    return out


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
@pytest.mark.parametrize('allowed', [True, False])
def test_shipment_facts_model_and_legacy_paths(mode, source, allowed):
    """Legacy 1.8: historical placement flags grant/deny through either source."""
    instance, box, slot = _case(source)
    if source == 'model':
        box.dg.clear_living_quarters = allowed
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id]['clear_living_quarters'] = allowed
    out = _check(instance, box, slot, mode)
    assert bool(out) is (not allowed)
    if not allowed:
        assert any(v.rule_id == STOWAGE and 'SW2' in v.message for v in out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('model_value', [True, False])
def test_shipment_explicit_boolean_precedes_conflicting_override(mode, model_value):
    """Legacy 1.8: model placement flags precede conflicting override flags."""
    instance, box, slot = _case()
    box.dg.clear_living_quarters = model_value
    instance.rule_overrides[STOWAGE]['containers'][box.id]['clear_living_quarters'] = not model_value
    out = _check(instance, box, slot, mode)
    assert bool(out) is (not model_value)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('field', ['packing_form', 'ctu_closed', 'clear_living_quarters'])
def test_shipment_unknown_without_fallback_fails_closed(mode, field):
    """Legacy 1.8: missing packing, closure or placement flags fail closed."""
    instance, box, slot = _case()
    setattr(box.dg, field, None)
    assert _check(instance, box, slot, mode)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_shipment_packing_and_ctu_model_precedence(mode):
    """Legacy 1.8: known model packing/closure override legacy declarations."""
    instance, box, slot = _case()
    legacy = instance.rule_overrides[STOWAGE]['containers'][box.id]
    legacy.update(packing_form='open_freight_container', ctu_closed=False)
    instance.rule_overrides[SHIP] = {'slots': {slot: {'closed': False}}}
    assert _check(instance, box, slot, mode) == []
    box.dg.ctu_closed = False
    assert any(v.rule_id == STOWAGE and 'closed' in v.message.lower()
               for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
@pytest.mark.parametrize('provisions', [None, [], ['376']])
def test_shipment_sw19_unknown_empty_and_declared_special_provisions(mode, source, provisions):
    """Legacy 1.8: original SP-only SW19 interpretation is preserved exactly."""
    instance, box, slot = _case(source, 3480)
    if source == 'model':
        box.dg.special_provisions = provisions
        box.dg.short_international_voyage = False
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id].update(
            special_provisions=provisions, short_international_voyage=False)
    # Category C under SW19 is forbidden below deck; category A is permitted.
    out = _check(instance, box, '0010102', mode)
    if provisions is None:
        assert any(v.rule_id == MISSING and 'SW19' in v.message for v in out)
    elif provisions:
        assert any(v.rule_id == STOWAGE and 'on-deck' in v.message for v in out)
    else:
        assert out == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_shipment_empty_special_provisions_precede_override(mode):
    """Legacy 1.8: explicit empty model SP list overrides legacy SP376."""
    instance, box, slot = _case(un=3480)
    box.dg.special_provisions = []
    instance.rule_overrides[STOWAGE]['containers'][box.id]['special_provisions'] = ['376']
    assert _check(instance, box, '0010102', mode) == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('codes', [None, [], ['SW23']])
def test_shipment_verified_provision_lists_fail_closed(mode, codes):
    """Legacy 1.8 invariant, with a current counterpart: verification is explicit."""
    instance, box, slot = _case()
    box.dg.stowage_codes.append('SW23')
    box.dg.verified_provisions = codes
    out = _check(instance, box, slot, mode)
    assert bool(out) is (not codes)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_shipment_empty_verification_precedes_legacy_permission(mode):
    """Legacy 1.8 invariant: empty model verification blocks legacy permission."""
    instance, box, slot = _case()
    box.dg.stowage_codes.append('SW23')
    box.dg.verified_provisions = []
    instance.rule_overrides[STOWAGE]['containers'][box.id]['verified_provisions'] = ['SW23']
    assert any(v.rule_id == MISSING and 'SW23' in v.message for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('additional_fact', [None, False, True])
def test_shipment_mixed_load_entry_conditions_do_not_inherit_governing_entry(mode, additional_fact):
    """Legacy 1.8: additional-entry placement flags do not inherit model values."""
    instance, box, slot = _case()
    box.dg.additional = [_dg()]
    box.dg.additional[0].clear_living_quarters = additional_fact
    out = _check(instance, box, slot, mode)
    assert bool(out) is (additional_fact is not True)
    if additional_fact is None:
        assert any(v.rule_id == MISSING and 'SW2' in v.message for v in out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('field,value', [('packing_form', 'open_freight_container'), ('ctu_closed', False)])
def test_shipment_mixed_load_conflicting_shared_facts_fail_closed(mode, field, value):
    """Legacy 1.8 invariant: additional entries cannot contradict shared CTU facts."""
    instance, box, slot = _case()
    box.dg.additional = [_dg(1202, 'III')]
    assert _check(instance, box, slot, mode) == []
    setattr(box.dg.additional[0], field, value)
    assert _check(instance, box, slot, mode)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('codes', [None, [], ['SG50']])
def test_shipment_verified_segregation_codes_model(mode, codes):
    """Legacy 1.8 invariant: SG50 needs explicit entry verification."""
    instance, box, slot = _case(un=2814)
    box.dg.dry = box.dg.handling_compliant = True
    box.dg.verified_segregation_codes = codes
    out = _check(instance, box, slot, mode)
    assert bool(out) is (not codes)
    if not codes:
        assert any(v.rule_id == GENERAL and 'SG50' in v.message for v in out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_shipment_mixed_load_segregation_verification_is_entry_specific(mode):
    """Legacy 1.8 invariant: governing SG50 verification cannot clear another entry."""
    instance, box, slot = _case(un=2814)
    box.dg.dry = box.dg.handling_compliant = True
    box.dg.verified_segregation_codes = ['SG50']
    box.dg.additional = [_dg(2814)]
    additional = box.dg.additional[0]
    additional.clear_living_quarters = additional.dry = additional.handling_compliant = True
    assert any(v.rule_id == GENERAL and 'SG50' in v.message for v in _check(instance, box, slot, mode))
    additional.verified_segregation_codes = ['SG50']
    assert _check(instance, box, slot, mode) == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
def test_shipment_pair_segregation_verification_reads_shipment_fact(mode, source):
    """Legacy 1.8 invariant: pair evaluator uses known model/override SG50 facts."""
    instance, box, slot = _case(source, 2814)
    other = instance.containers[1]
    other_id = other.id
    instance.containers[1] = box.model_copy(deep=True, update={'id': other_id})
    other = instance.containers[1]
    for cargo in (box, other):
        facts = {'dry': True, 'handling_compliant': True,
                 'verified_segregation_codes': ['SG50']}
        if source == 'model':
            for field, value in facts.items():
                setattr(cargo.dg, field, value)
        else:
            instance.rule_overrides[STOWAGE]['containers'][cargo.id] = {
                'packing_form': CLOSED, 'ctu_closed': True,
                'clear_living_quarters': True, **facts,
            }
    others = [Placement(slot='0030182', container_id=other.id)]
    assert _check(instance, box, slot, mode, others) == []
    # An explicit empty model list blocks legacy permission in both the unary
    # DGL guard and the pair segregation evaluator.
    box.dg.verified_segregation_codes = []
    instance.rule_overrides[STOWAGE]['containers'][box.id]['verified_segregation_codes'] = ['SG50']
    assert any(v.rule_id == GENERAL and len(v.containers) == 2 and 'SG50' in v.message
               for v in _check(instance, box, slot, mode, others))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
@pytest.mark.parametrize('code,field', [
    ('SW1', 'protected_from_heat'), ('SW6', 'ventilation_compliant'),
    ('SW11', 'shaded'), ('SW11', 'air_circulation'),
    ('H1', 'dry'), ('H5', 'handling_compliant'),
])
def test_shipment_condition_fields_model_and_fallback(mode, source, code, field):
    """Legacy 1.8: historical field conditions and fallback remain unchanged."""
    instance, box, slot = _case(source)
    box.dg.stowage_codes.append(code)
    facts = {'protected_from_heat': True, 'ventilation_compliant': True,
             'shaded': True, 'air_circulation': True, 'dry': True,
             'handling_compliant': True}
    if source == 'model':
        for key, value in facts.items():
            setattr(box.dg, key, value)
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id].update(facts)
    assert _check(instance, box, '0010102', mode) == []
    setattr(box.dg, field, False)
    assert any(v.rule_id == STOWAGE and code in v.message and field in v.message
               for v in _check(instance, box, '0010102', mode))


def _current_case(source='model', un=1057, pg=None):
    instance, box, slot = _case(source, un, pg)
    instance.schema_version = '1.9'
    # This explicitly declared synthetic deckhouse is the same physical solid
    # read by visibility and IMDG; it contains the fixture's bridge x/eye-z.
    from stowage.model.vessel import FixedLoad, FixedLoadSilhouette
    instance.vessel.fixed_loads.append(FixedLoad(
        x_m=15, y_m=0, z_m=17.5, mass_kg=1, kind='non_cargo',
        silhouette=FixedLoadSilhouette(x_min_m=10, x_max_m=20, y_min_m=-5,
                                       y_max_m=5, z_min_m=9, z_max_m=26)))
    def evidence(suffix, clause):
        return {'document': 'declared-shared-shipment-test-design', 'clause': clause,
                'scenario_id': f'{box.id}-{suffix}'}
    geometry = {
        'heat_map_complete': True,
        'heat_map_provenance': evidence('heat-map', '7.1.2 SW1'),
        'heat_sources': [{'id': source.id, 'liable_to_exceed_55_c': True,
                          'provenance': evidence(f'heat-{source.id}', '7.1.2 SW1')}
                         for source in instance.vessel.heat_sources],
        'living_quarters_map_complete': True,
        'living_quarters_map_provenance': evidence('living-map', '7.1.2 SW2'),
        'living_quarters_sources': [{'id': 'shared-deckhouse', 'kind': 'accommodation',
                                    'fixed_load_index': 0}],
    }
    config = instance.rule_overrides[STOWAGE]
    config['placement_geometry'] = geometry
    facts = config['containers'][box.id]
    codes = set(box.dg.stowage_codes) | set(box.dg.handling_codes)
    if 'H1' in codes:
        facts['h1'] = {'provenance': evidence('h1', '7.1.6 H1; 7.3.3.3'),
                       'ctu_weathertight': True, 'packing_condition': 'dry'}
    if 'SW19' in codes:
        facts['sw19'] = {'provenance': evidence('sw19', '7.1.5 SW19'),
                         'un_number': box.dg.un_number, 'battery_condition': 'intact',
                         'transport_purpose': 'normal',
                         'special_provision_applicability': {'376': False, '377': False}}
    registry = config.setdefault('assessment_scenarios', {})
    context = context_for(instance, box, 'primary', slot, 0)
    def register(value):
        if isinstance(value, (list, tuple)):
            for child in value:
                register(child)
        if not isinstance(value, dict):
            return
        source = value.get('provenance')
        if isinstance(source, dict):
            row = asdict(context)
            row.pop('slot')
            registry[source['scenario_id']] = row | {
                'document': source['document'], 'clause': source['clause'],
                'allowed_slots': ['0010182', '0010102']}
        for key, child in value.items():
            register({'provenance': child} if key.endswith('_provenance') else child)
    register(geometry)
    register(facts)
    return instance, box, slot


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
def test_current_shipment_bound_baseline_is_valid(mode, source):
    """Current 1.9: isolate later invariant checks with legitimate bound SW2."""
    instance, box, slot = _current_case(source)
    assert _check(instance, box, slot, mode) == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
@pytest.mark.parametrize('allowed', [None, True, False])
def test_current_shipment_placement_flags_never_replace_geometry(mode, source, allowed):
    """Current counterpart: known/unknown legacy SW2 flags leave absent geometry unknown."""
    instance, box, slot = _case(source)
    instance.schema_version = '1.9'
    if source == 'model':
        box.dg.clear_living_quarters = allowed
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id]['clear_living_quarters'] = allowed
    assert any(v.rule_id == MISSING and 'SW2' in v.message
               for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('model_value', [True, False])
def test_current_shipment_conflicting_placement_flags_are_both_ignored(mode, model_value):
    """Current counterpart: model True/override False and their inverse fail closed."""
    instance, box, slot = _case()
    instance.schema_version = '1.9'
    box.dg.clear_living_quarters = model_value
    instance.rule_overrides[STOWAGE]['containers'][box.id]['clear_living_quarters'] = not model_value
    assert any(v.rule_id == MISSING and 'SW2' in v.message
               for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('field', ['packing_form', 'ctu_closed'])
def test_current_shipment_unknown_without_fallback_fails_closed(mode, field):
    """Current invariant: valid bound SW2 cannot hide unknown packing/CTU facts."""
    instance, box, slot = _current_case()
    assert _check(instance, box, slot, mode) == []
    setattr(box.dg, field, None)
    assert _check(instance, box, slot, mode)
    assert any(v.rule_id == MISSING and ('packing_form' if field == 'packing_form' else 'CTU') in v.message
               for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_current_shipment_packing_and_ctu_model_precedence(mode):
    """Current invariant: original packing/closure precedence assertions still hold."""
    instance, box, slot = _current_case()
    legacy = instance.rule_overrides[STOWAGE]['containers'][box.id]
    legacy.update(packing_form='open_freight_container', ctu_closed=False)
    instance.rule_overrides[SHIP] = {'slots': {slot: {'closed': False}}}
    assert _check(instance, box, slot, mode) == []
    box.dg.ctu_closed = False
    assert any(v.rule_id == STOWAGE and 'closed' in v.message.lower()
               for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
@pytest.mark.parametrize('provisions', [None, [], ['376']])
def test_current_shipment_sp_only_sw19_is_always_unknown(mode, source, provisions):
    """Current counterpart: absent bound SW19 remains unknown for every old SP list."""
    instance, box, slot = _case(source, 3480)
    instance.schema_version = '1.9'
    if source == 'model':
        box.dg.special_provisions = provisions
        box.dg.short_international_voyage = False
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id].update(
            special_provisions=provisions, short_international_voyage=False)
    assert any(v.rule_id == MISSING and 'SW19' in v.message
               for v in _check(instance, box, '0010102', mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_current_shipment_empty_special_provisions_precede_override(mode):
    """Current invariant: bound ordinary battery declaration preserves actual SP precedence."""
    instance, box, slot = _current_case(un=3480)
    box.dg.special_provisions = []
    instance.rule_overrides[STOWAGE]['containers'][box.id]['special_provisions'] = ['376']
    assert _check(instance, box, '0010102', mode) == []


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('codes', [None, [], ['SW23']])
def test_current_shipment_verified_provision_lists_fail_closed(mode, codes):
    """Current invariant: bound geometry preserves original verification-list assertions."""
    instance, box, slot = _current_case()
    box.dg.stowage_codes.append('SW23')
    box.dg.verified_provisions = codes
    out = _check(instance, box, slot, mode)
    assert bool(out) is (not codes)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_current_shipment_empty_verification_precedes_legacy_permission(mode):
    """Current invariant: explicit empty model verification still blocks override permission."""
    instance, box, slot = _current_case()
    box.dg.stowage_codes.append('SW23')
    box.dg.verified_provisions = []
    instance.rule_overrides[STOWAGE]['containers'][box.id]['verified_provisions'] = ['SW23']
    assert any(v.rule_id == MISSING and 'SW23' in v.message for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('additional_fact', [None, False, True])
def test_current_shipment_additional_entry_never_inherits_bound_assessment(mode, additional_fact):
    """Current counterpart: governing bound SW2 and any additional legacy flag leave entry unknown."""
    instance, box, slot = _current_case()
    assert _check(instance, box, slot, mode) == []
    box.dg.additional = [_dg()]
    box.dg.additional[0].clear_living_quarters = additional_fact
    assert any(v.rule_id == MISSING and 'SW2' in v.message
               for v in _check(instance, box, slot, mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('field,value', [('packing_form', 'open_freight_container'), ('ctu_closed', False)])
def test_current_shipment_mixed_load_conflicting_shared_facts_fail_closed(mode, field, value):
    """Current invariant: original conflict assertions hold with valid primary geometry."""
    instance, box, slot = _current_case()
    box.dg.additional = [_dg(1202, 'III')]
    assert _check(instance, box, slot, mode) == []
    setattr(box.dg.additional[0], field, value)
    assert _check(instance, box, slot, mode)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('codes', [None, [], ['SG50']])
def test_current_shipment_verified_segregation_codes_model(mode, codes):
    """Current invariant: bound declared H1/SW2 preserves exact SG50-list assertions."""
    instance, box, slot = _current_case(un=2814)
    box.dg.handling_compliant = True
    box.dg.verified_segregation_codes = codes
    out = _check(instance, box, slot, mode)
    assert bool(out) is (not codes)
    if not codes:
        assert any(v.rule_id == GENERAL and 'SG50' in v.message for v in out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
def test_current_shipment_mixed_load_segregation_verification_is_entry_specific(mode):
    """Current counterpart: SG50 remains entry-specific; unbound additional H1/SW2 stays unknown."""
    instance, box, slot = _current_case(un=2814)
    box.dg.handling_compliant = True
    box.dg.verified_segregation_codes = ['SG50']
    assert _check(instance, box, slot, mode) == []
    box.dg.additional = [_dg(2814)]
    additional = box.dg.additional[0]
    additional.clear_living_quarters = additional.dry = additional.handling_compliant = True
    assert any(v.rule_id == GENERAL and 'SG50' in v.message for v in _check(instance, box, slot, mode))
    additional.verified_segregation_codes = ['SG50']
    out = _check(instance, box, slot, mode)
    assert not any(v.rule_id == GENERAL and 'SG50' in v.message for v in out)
    assert any(v.rule_id == MISSING and ('SW2' in v.message or 'H1' in v.message) for v in out)


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
def test_current_shipment_pair_segregation_verification_reads_shipment_fact(mode, source):
    """Current counterpart: exact SG50 pair guard remains; second box cannot replay first-box evidence."""
    instance, box, slot = _current_case(source, 2814)
    other_id = instance.containers[1].id
    instance.containers[1] = box.model_copy(deep=True, update={'id': other_id})
    other = instance.containers[1]
    for cargo in (box, other):
        facts = {'handling_compliant': True, 'verified_segregation_codes': ['SG50']}
        if source == 'model':
            for field, value in facts.items():
                setattr(cargo.dg, field, value)
        else:
            instance.rule_overrides[STOWAGE]['containers'].setdefault(cargo.id, {}).update(
                packing_form=CLOSED, ctu_closed=True, clear_living_quarters=True, dry=True, **facts)
    others = [Placement(slot='0030182', container_id=other.id)]
    out = _check(instance, box, slot, mode, others)
    assert not any(v.rule_id == GENERAL and 'SG50' in v.message for v in out)
    if mode == 'incremental':
        # Incremental checks validate the candidate's unary conditions, not
        # the already-placed companion's conditions. Preserve that valid
        # candidate assertion, then explicitly test the unbound companion.
        assert out == []
    else:
        assert any(v.rule_id == MISSING and v.severity == 'hard'
                   and ('SW2' in v.message or 'H1' in v.message) for v in out)
    unbound_out = _check(instance, other, '0030182', mode,
                         [Placement(slot=slot, container_id=box.id)])
    assert any(v.rule_id == MISSING and ('SW2' in v.message or 'H1' in v.message) for v in unbound_out)
    box.dg.verified_segregation_codes = []
    instance.rule_overrides[STOWAGE]['containers'][box.id]['verified_segregation_codes'] = ['SG50']
    assert any(v.rule_id == GENERAL and len(v.containers) == 2 and 'SG50' in v.message
               for v in _check(instance, box, slot, mode, others))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
@pytest.mark.parametrize('code,field', [
    ('SW6', 'ventilation_compliant'), ('SW11', 'shaded'),
    ('SW11', 'air_circulation'), ('H5', 'handling_compliant')])
def test_current_shipment_unmigrated_condition_fields_model_and_fallback(mode, source, code, field):
    """Current invariant: original non-placement-condition assertions hold with bound SW2."""
    instance, box, slot = _current_case(source)
    box.dg.stowage_codes.append(code)
    facts = {'ventilation_compliant': True, 'shaded': True, 'air_circulation': True,
             'handling_compliant': True}
    if source == 'model':
        for key, value in facts.items():
            setattr(box.dg, key, value)
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id].update(facts)
    assert _check(instance, box, '0010102', mode) == []
    setattr(box.dg, field, False)
    assert any(v.rule_id == STOWAGE and code in v.message and field in v.message
               for v in _check(instance, box, '0010102', mode))


@pytest.mark.parametrize('mode', ['full', 'incremental'])
@pytest.mark.parametrize('source', ['model', 'legacy'])
@pytest.mark.parametrize('code,field', [('SW1', 'protected_from_heat'), ('H1', 'dry')])
@pytest.mark.parametrize('value', [True, False])
def test_current_shipment_migrated_condition_flags_cannot_clear(mode, source, code, field, value):
    """Current counterpart: old H1/SW1 flags cannot replace bound declarations or geometry."""
    instance, box, slot = _current_case(source)
    box.dg.stowage_codes.append(code)
    if source == 'model':
        setattr(box.dg, field, value)
    else:
        instance.rule_overrides[STOWAGE]['containers'][box.id][field] = value
    assert any(v.rule_id == MISSING and code in v.message
               for v in _check(instance, box, '0010102', mode))
