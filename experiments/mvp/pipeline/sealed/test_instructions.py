import copy
import json

import pytest

from .instructions import FixtureParser, RULE_TYPES, instruction_targets, public_facts, select_rule, sentence_for


def facts():
    return dict(call_index=0, rotation=dict(calls=[dict(index=1, locode='SGSIN')]),
        vessel=dict(id='synthetic', cells=[dict(id='0010182'), dict(id='0010184'),
        dict(id='0030182'), dict(id='0030184')],
        stacks=[dict(bay=bay, on_deck=True, cell_ids=[f'{bay:03}0182', f'{bay:03}0184'])
                for bay in (1, 3)], bay_pairs=[], hatch_covers=[dict(id='H1', bays=[1, 3])],
        exclusion_regions=[]), containers=[dict(id='BOX1', length_ft=20, gross_mass_kg=12000,
                                               pod='SGSIN', dg=dict(imdg_class='3'))])


def test_all_four_types_drawn_without_results():
    raw = facts()
    drawn = [select_rule(raw, rank) for rank in range(24)]
    assert [r['constraint']['type'] for r in drawn] == list(RULE_TYPES)*6
    assert drawn[0]['constraint'] == dict(type='close_bay', bay=1)
    assert drawn[1]['constraint'] == dict(type='max_tiers', bay=1, zone='deck', max_tiers=0)
    assert drawn[2]['constraint']['away_from'] == dict(kind='bay', bay=1)
    assert drawn[4]['constraint']['bay'] == 3
    for rule in drawn:
        assert sentence_for(rule)
        from experiments.mvp.reader.schema import context_from_instance, validate_answer
        validate_answer(json.dumps(rule), context_from_instance(raw))


def test_public_facts_ignore_results_yard_and_input_container_order():
    raw = facts()
    raw['containers'].append(dict(id='BOX2', length_ft=40, gross_mass_kg=10000, pod='THLCH', dg=None))
    other = copy.deepcopy(raw)
    other.update(yard={'arbitrary': 'yard'}, passed=True, weighted_lifts=123)
    other['containers'].reverse()
    assert public_facts(raw) == public_facts(other)
    other['containers'][0]['pod'] = 'MYTPP'
    assert public_facts(raw) != public_facts(other)


def test_absent_dg_has_fixed_vacuous_class_without_redraw():
    raw = facts()
    raw['containers'][0]['dg'] = None
    assert select_rule(raw, 2)['constraint']['imdg_class'] == '3'
    with pytest.raises(ValueError):
        select_rule(raw, -1)


def test_fixture_parser_has_explicit_surrogate_label():
    from experiments.mvp.reader.schema import context_from_instance
    raw = facts()
    rule = select_rule(raw, 0)
    answer, provenance = FixtureParser(rule).parse(sentence_for(rule), context_from_instance(raw))
    assert answer.type == 'close_bay'
    assert provenance['fixture_surrogate'] is True
    with pytest.raises(ValueError):
        FixtureParser(rule).parse('Unregistered sentence', context_from_instance(raw))


def test_dg_targets_include_nested_primary_subsidiary_and_additional_only_in_load_list():
    raw = facts()
    raw['load_list'] = ['BOX1', 'BOX2', 'BOX3']
    raw['containers'] = [
        dict(id='BOX1', dg=dict(imdg_class='3', subsidiary_risks=['2.1'])),
        dict(id='BOX2', dg=dict(imdg_class='8', additional=[dict(imdg_class='9', additional=[
            dict(imdg_class='2.2')])])),
        dict(id='BOX3', dg=dict(imdg_class='2.3', subsidiary_risks=['2.1'])),
        dict(id='NOT_LOADING', dg=dict(imdg_class='2.1')),
    ]
    rule = dict(constraint=dict(type='dg_keep_away', imdg_class='2'))
    assert instruction_targets(raw, rule) == dict(instruction_target_count=3, no_matching_cargo=False)
    rule['constraint']['imdg_class'] = '2.1'
    assert instruction_targets(raw, rule)['instruction_target_count'] == 2
    rule['constraint']['imdg_class'] = '5.1'
    assert instruction_targets(raw, rule) == dict(instruction_target_count=0, no_matching_cargo=True)
    assert instruction_targets(raw, {'constraint': {'type': 'close_bay'}}) == dict(
        instruction_target_count=None, no_matching_cargo=None)
