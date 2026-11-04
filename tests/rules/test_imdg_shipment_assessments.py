"""Declared shipment facts with authoritative scenario scope, never outcomes."""
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from stowage.rules.imdg.scenarios import BindingContext
from stowage.rules.imdg.shipment_assessments import (
    assess_h1 as raw_h1, assess_sw19 as raw_sw19, operational_precautions,
)


def provenance(clause):
    return {'document': 'synthetic-shipment-declaration', 'clause': clause,
            'scenario_id': 'declared-' + clause}


def h1_facts():
    return {'ctu_closed': True, 'h1': {
        'provenance': provenance('7.1.6 H1; 7.3.3.3'),
        'ctu_weathertight': True, 'packing_condition': 'dry'}}


def sw19_facts(condition='intact', purpose='normal', short=None):
    damaged = condition in {'damaged_defective', 'cannot_be_diagnosed'}
    relevant = damaged or purpose in {'disposal', 'recycling'}
    record = {
        'provenance': provenance('7.1.5 SW19; 3.3 SP376; 3.3 SP377'),
        'un_number': '3480', 'battery_condition': condition, 'transport_purpose': purpose,
        'special_provision_applicability': {'376': damaged, '377': relevant and not damaged}}
    if relevant:
        record['transport_in_accordance'] = True
    if short is not None:
        record['voyage'] = {'short_international_voyage': short,
                            'provenance': provenance('1.2.1 short international voyage')}
    return {'sw19': record}


def binding(facts, *, un_number='3480', slot='0010182'):
    """Explicitly register synthetic declarations in this test fixture."""
    context = BindingContext(instance_id='fixture-i', container_id='fixture-box',
                             entry_path='primary', un_number=un_number, packing_group=None,
                             vessel_id='fixture-v', voyage_id='fixture-voyage',
                             call_index=0, slot=slot)
    registry = {}
    def register(value):
        if not isinstance(value, dict):
            return
        source = value.get('provenance')
        if isinstance(source, dict) and all(k in source for k in ('scenario_id', 'document', 'clause')):
            row = asdict(context)
            row.pop('slot')
            registry[source['scenario_id']] = row | {
                'allowed_slots': [slot], 'document': source['document'], 'clause': source['clause']}
        for child in value.values():
            register(child)
    register(facts)
    return {'context': context, 'registry': registry}


def assess_h1(facts, **kwargs):
    default_slot = '0010102' if kwargs.get('on_deck') is False else '0010182'
    return raw_h1(facts, **kwargs, **binding(facts, slot=kwargs.get('slot', default_slot)))


def assess_sw19(facts, *, un_number='3480'):
    return raw_sw19(facts, un_number=un_number, **binding(facts, un_number=un_number))


@pytest.mark.parametrize(('on_deck', 'covered'), [(True, None), (False, True)])
def test_h1_positive_declared_policy(on_deck, covered):
    assert assess_h1(h1_facts(), on_deck=on_deck, covered_under_deck=covered) is True


@pytest.mark.parametrize('field', ['ctu_closed', 'ctu_weathertight', 'packing_condition'])
def test_h1_negative_declaration(field):
    facts = h1_facts()
    (facts if field == 'ctu_closed' else facts['h1'])[field] = 'wet' if field == 'packing_condition' else False
    assert assess_h1(facts, on_deck=True) is False


@pytest.mark.parametrize('field', ['ctu_closed', 'ctu_weathertight', 'packing_condition', 'provenance'])
def test_h1_unknown_missing_declaration(field):
    facts = h1_facts()
    del (facts if field == 'ctu_closed' else facts['h1'])[field]
    assert assess_h1(facts, on_deck=True) is None


@pytest.mark.parametrize(('on_deck', 'covered', 'expected'), [
    (False, None, None), (False, False, False), (None, True, None)])
def test_h1_actual_covered_hold_geometry_required(on_deck, covered, expected):
    assert assess_h1(h1_facts(), on_deck=on_deck, covered_under_deck=covered) is expected


def test_h1_outcomes_and_shipper_selected_slots_never_participate():
    facts = h1_facts()
    facts['h1'].update(handling_kept_dry=False, voyage_precautions_maintained=False,
                       inspection={'goods_dry': False}, placements={})
    assert assess_h1(facts, on_deck=True) is True
    assert assess_h1({'ctu_closed': True, 'dry': True}, on_deck=True) is None


def test_operational_precautions_are_instructions_outside_verdict():
    assert operational_precautions({}) == ()
    assert operational_precautions({'h1': None})
    facts = h1_facts()
    before = deepcopy(facts)
    assert any('voyage' in text for text in operational_precautions(facts))
    assert facts == before
    assert raw_h1(facts, on_deck=True) is None


def test_sw19_ordinary_needs_no_inspection_or_voyage():
    facts = sw19_facts()
    assert 'transport_in_accordance' not in facts['sw19']
    result = assess_sw19(facts)
    assert result.compliant is True and result.category == 'A'
    facts['sw19']['transport_in_accordance'] = False  # obsolete observation is irrelevant
    assert assess_sw19(facts).compliant is True


def test_sw19_base_category_comes_from_lookup(monkeypatch):
    monkeypatch.setattr('stowage.rules.imdg.shipment_assessments.lookup_dgl',
                        lambda un: SimpleNamespace(source_stowage_codes=('SW19',), stowage_category='B'))
    assert assess_sw19(sw19_facts()).category == 'B'


@pytest.mark.parametrize('un_number', ['3480', '3481'])
def test_sw19_sampled_battery_entries(un_number):
    facts = sw19_facts()
    facts['sw19']['un_number'] = un_number
    assert assess_sw19(facts, un_number=un_number).compliant is True


@pytest.mark.parametrize('un_number', ['3090', '3091', '3551', '3552', '9999', '1333'])
def test_sw19_unsampled_or_non_sw19_un_unknown(un_number):
    facts = sw19_facts()
    facts['sw19']['un_number'] = un_number
    assert assess_sw19(facts, un_number=un_number).compliant is None


def test_sw19_optional_sp377_not_automatically_elected():
    facts = sw19_facts('intact', 'recycling')
    facts['sw19']['special_provision_applicability']['377'] = False
    del facts['sw19']['transport_in_accordance']
    assert assess_sw19(facts).category == 'A'


@pytest.mark.parametrize(('condition', 'purpose'), [
    ('damaged_defective', 'normal'), ('cannot_be_diagnosed', 'normal'),
    ('intact', 'recycling'), ('intact', 'disposal'), ('damaged_defective', 'recycling')])
@pytest.mark.parametrize(('short', 'category'), [(True, 'A'), (False, 'C')])
def test_sw19_declared_exceptional_route_category(condition, purpose, short, category):
    result = assess_sw19(sw19_facts(condition, purpose, short))
    assert result.compliant is True and result.category == category


@pytest.mark.parametrize('missing', ['battery_condition', 'transport_purpose',
                                     'special_provision_applicability', 'transport_in_accordance', 'voyage'])
def test_sw19_incomplete_exceptional_declaration_is_unknown(missing):
    facts = sw19_facts('damaged_defective', short=False)
    del facts['sw19'][missing]
    facts['special_provisions'] = []
    assert assess_sw19(facts).compliant is None


@pytest.mark.parametrize('alter', ['all_candidates', 'damage_hidden', 'un_mismatch', 'noncompliant'])
def test_sw19_adverse_or_contradictory_declarations(alter):
    facts = sw19_facts('damaged_defective', short=False)
    record = facts['sw19']
    if alter == 'all_candidates':
        record['special_provision_applicability'] = {'376': True, '377': True}
    elif alter == 'damage_hidden':
        record['special_provision_applicability']['376'] = False
    elif alter == 'un_mismatch':
        record['un_number'] = '3481'
    else:
        record['transport_in_accordance'] = False
    assert assess_sw19(facts).compliant is False


@pytest.mark.parametrize(('field', 'value'), [
    ('battery_condition', []), ('transport_purpose', {}),
    ('special_provision_applicability', ['376', '377']), ('transport_in_accordance', 1)])
def test_sw19_malformed_exceptional_declaration_unknown(field, value):
    facts = sw19_facts('damaged_defective', short=False)
    facts['sw19'][field] = value
    assert assess_sw19(facts).compliant is None


@pytest.mark.parametrize(('condition', 'purpose', 'actual', 'expected'), [
    ('intact', 'normal', ['376'], False), ('intact', 'normal', ['377'], False),
    ('damaged_defective', 'normal', [], False), ('intact', 'recycling', ['376'], False),
    ('intact', 'normal', [], True), ('damaged_defective', 'normal', ['376', '230'], True),
    ('intact', 'recycling', ['377'], True), ('intact', 'normal', None, True)])
def test_sw19_existing_actual_shipment_sps_consistent(condition, purpose, actual, expected):
    facts = sw19_facts(condition, purpose, short=False)
    facts['special_provisions'] = actual
    assert assess_sw19(facts).compliant is expected


@pytest.mark.parametrize('short', [False, True])
def test_sw19_existing_voyage_classification_consistent(short):
    facts = sw19_facts('damaged_defective', short=short)
    facts['short_international_voyage'] = not short
    assert assess_sw19(facts).compliant is False
    facts['short_international_voyage'] = short
    assert assess_sw19(facts).compliant is True


@pytest.mark.parametrize(('field', 'value'), [
    ('special_provisions', '376'), ('special_provisions', [None]),
    ('short_international_voyage', 'yes')])
def test_sw19_malformed_existing_facts_unknown(field, value):
    facts = sw19_facts('damaged_defective', short=False)
    facts[field] = value
    assert assess_sw19(facts).compliant is None


@pytest.mark.parametrize('kind', ['h1', 'sw19'])
def test_unbound_and_unregistered_provenance_stays_unknown(kind):
    facts = h1_facts() if kind == 'h1' else sw19_facts()
    fn = (lambda **scope: raw_h1(facts, on_deck=True, **scope)) if kind == 'h1' else (
        lambda **scope: raw_sw19(facts, un_number='3480', **scope).compliant)
    assert fn() is None
    scope = binding(facts)
    facts[kind]['provenance']['scenario_id'] = 'unregistered'
    assert fn(**scope) is None


@pytest.mark.parametrize('kind', ['h1', 'sw19'])
@pytest.mark.parametrize('field', ['instance_id', 'container_id', 'entry_path', 'vessel_id', 'voyage_id', 'slot'])
def test_foreign_scenario_replay_unknown(kind, field):
    facts = h1_facts() if kind == 'h1' else sw19_facts()
    scope = binding(facts)
    scope['context'] = replace(scope['context'], **{field: 'foreign'})
    result = (raw_h1(facts, on_deck=True, **scope) if kind == 'h1'
              else raw_sw19(facts, un_number='3480', **scope).compliant)
    assert result is None


def test_foreign_voyage_evidence_cannot_reduce_exceptional_category():
    facts = sw19_facts('damaged_defective', short=True)
    scope = binding(facts)
    sid = facts['sw19']['voyage']['provenance']['scenario_id']
    scope['registry'][sid]['voyage_id'] = 'other-voyage'
    assert raw_sw19(facts, un_number='3480', **scope).compliant is None


def test_h1_slot_argument_must_match_authorized_context():
    facts = h1_facts()
    assert raw_h1(facts, on_deck=True, slot='0030182', **binding(facts)) is None


def test_h1_deck_status_must_match_bound_actual_slot():
    facts = h1_facts()
    assert raw_h1(facts, on_deck=True, **binding(facts, slot='0010102')) is None


def test_sw19_argument_must_match_bound_entry_un():
    facts = sw19_facts()
    facts['sw19']['un_number'] = '3481'
    assert raw_sw19(facts, un_number='3481', **binding(facts, un_number='3480')).compliant is None


def test_assessors_do_not_modify_facts():
    facts = h1_facts() | sw19_facts()
    before = deepcopy(facts)
    assess_h1(facts, on_deck=True)
    assess_sw19(facts)
    assert facts == before
