"""Owner-supplied DG contexts are diagnostic inputs, never qualified canaries."""
from copy import deepcopy
from functools import lru_cache

import pytest

from stowage.checker.rules_imdg import MISSING, STOWAGE, imdg_violations
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement, ShipState
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.canary_contexts import load_context_catalog, materialize_context_pair
from stowage.vessel.fleet import load_vessel


@lru_cache(maxsize=4)
def _fleet(name, full):
    return load_vessel(name, with_hull=full)


def _input(name='feeder_500', *, full=False):
    instance = build_instance(with_dg_doc=True)
    instance.schema_version = '1.9'
    instance.vessel = _fleet(name, full)
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    for box in instance.containers:
        if box.length_ft == 20 and not box.reefer:
            # Authored synthetic fixture plate, supplied BEFORE materialization;
            # unknown input ratings must remain unusable for these 12t bookings.
            box.max_gross_mass_kg = 30500
    return instance


def test_context_catalog_is_explicitly_unqualified():
    catalog = load_context_catalog()
    assert catalog['contract'] == 'dg-context-request-v1'
    assert catalog['status'] == 'pending_qualification'
    assert catalog['evidence_eligible'] is False
    assert {'H1', 'SW19', 'SW1', 'SW2', 'SG17', 'SG25_2_1', 'SG25_3'} <= catalog['requests'].keys()


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
@pytest.mark.parametrize('canary_id', ['H1', 'SW19'])
def test_materialized_dg_context_is_a_pair_of_instance_json_and_placements(canary_id, name):
    # Numerical qualification of canonical hulls/background/liquids belongs to
    # the freeze owner; H1/SW19 context isolation needs geometry only.
    instance = _input(name)
    original = deepcopy(instance.model_dump(mode='json'))
    pair = materialize_context_pair(instance, canary_id)
    assert pair['target_provision'] == canary_id
    assert pair['expected_rule'] == STOWAGE
    assert pair['status'] == 'context_only'
    assert 'plan' not in pair and 'action' not in pair
    for key in ('baseline', 'defect'):
        member = pair[key]
        assert isinstance(member['input'], dict)
        materialized = Instance.model_validate(member['input'])
        assert materialized.vessel.model_dump(mode='json') == original['vessel']
        assert member['placements']
        assert all(isinstance(item, dict) for item in member['placements'])
        assert 'plan' not in member and 'action' not in member
        ship = ShipState(vessel_id=materialized.vessel.id,
                         placements=[Placement.model_validate(item) for item in member['placements']])
        for enabled in (False, True):
            with rule_freeze_v1(enabled=enabled):
                violations = imdg_violations(materialized, ship, call_index=materialized.call_index)
            if key == 'baseline':
                assert violations == []
            else:
                assert violations and {v.rule_id for v in violations} == {STOWAGE}
                assert all(canary_id in v.message or canary_id == 'SW19' and 'category C' in v.message
                           for v in violations)
    assert instance.model_dump(mode='json') == original


@pytest.mark.parametrize('canary_id', ['H1', 'SW19'])
def test_h1_and_sw19_pairs_change_declared_facts_at_the_same_placement(canary_id):
    from stowage.checker.rules_imdg import _facts
    from stowage.checker.slots import parse_slot
    from stowage.rules.imdg.scenarios import context_for, provenance_matches

    instance = _input()
    pair = materialize_context_pair(instance, canary_id)
    assert pair['baseline']['placements'] == pair['defect']['placements']
    declarations = []
    for key in ('baseline', 'defect'):
        member = pair[key]
        current = Instance.model_validate(member['input'])
        assert len(member['placements']) == 1
        placement = Placement.model_validate(member['placements'][0])
        box = next(b for b in current.containers if b.id == placement.container_id)
        assert box.length_ft == 20
        assert box.dg.un_number == ('1333' if canary_id == 'H1' else '3480')
        assert parse_slot(placement.slot).on_deck is (canary_id == 'H1')
        facts = _facts(current, box)
        if canary_id == 'SW19':
            # An under-deck defect must still have its same shipment evidence
            # registered for retained deck alternatives. This supplies scope,
            # never permission: the placement checker still assesses the slot.
            alternative = next(c.id for c in current.vessel.cells
                               if c.accepts_20 and parse_slot(c.id).on_deck)
            context = context_for(current, box, 'primary', alternative)
            registry = current.rule_overrides[STOWAGE]['assessment_scenarios']
            assert provenance_matches(facts['sw19']['provenance'], context, registry)
            assert provenance_matches(facts['sw19']['voyage']['provenance'], context, registry)
        assert not facts.get('verified_provisions')
        assert not facts.get('verified_segregation_codes')
        for outcome in ('dry', 'protected_from_heat', 'clear_living_quarters',
                        'ventilation_compliant', 'shaded', 'air_circulation',
                        'handling_compliant', 'subsidiary_hazards_verified'):
            assert facts.get(outcome) is not True
        declarations.append(facts)
    baseline, defect = declarations
    if canary_id == 'H1':
        assert baseline['h1']['packing_condition'] == 'dry'
        assert defect['h1']['packing_condition'] == 'wet'
        assert baseline['h1']['ctu_weathertight'] is defect['h1']['ctu_weathertight'] is True
    else:
        for index, facts in enumerate(declarations):
            record = facts['sw19']
            assert record['battery_condition'] == 'intact'
            assert record['transport_purpose'] == ('normal' if index == 0 else 'recycling')
            assert record['special_provision_applicability'] == {'376': False, '377': index == 1}
            if index == 1:
                assert record['transport_in_accordance'] is True
            if facts.get('special_provisions') is not None:
                assert set(facts['special_provisions']) & {'376', '377'} == (set() if index == 0 else {'377'})
            if facts.get('short_international_voyage') is not None:
                assert facts['short_international_voyage'] is record['voyage']['short_international_voyage']
        assert baseline['sw19']['voyage']['short_international_voyage'] is False
        assert defect['sw19']['voyage']['short_international_voyage'] is False


@pytest.mark.parametrize('canary_id', ['H1', 'SW19'])
def test_context_without_registered_evidence_is_unknown_not_a_certified_defect(canary_id):
    instance = _input()
    member = materialize_context_pair(instance, canary_id)['baseline']
    current = Instance.model_validate(member['input'])
    current.rule_overrides[STOWAGE]['assessment_scenarios'] = {}
    ship = ShipState(vessel_id=current.vessel.id,
        placements=[Placement.model_validate(item) for item in member['placements']])
    out = imdg_violations(current, ship, call_index=current.call_index)
    assert any(v.rule_id == MISSING and canary_id in v.message for v in out)
    assert not any(v.rule_id == STOWAGE for v in out)


@pytest.mark.parametrize('canary_id', ['SW1', 'SW2', 'SG17', 'SG25_2_1', 'SG25_3'])
def test_all_remaining_authored_contexts_isolate_exact_imdg_reason(canary_id):
    from stowage.checker.rules_imdg import imdg_placement_violations
    from stowage.rules.imdg.placement_assessments import covered_under_deck
    from stowage.rules.imdg.scenarios import context_for, provenance_matches

    instance = _input(full=True)
    original = deepcopy(instance.model_dump(mode='json'))
    pair = materialize_context_pair(instance, canary_id)
    assert pair['status'] == 'context_only' and pair['evidence_eligible'] is False
    assert pair['target_provision'] == ('SG25' if canary_id.startswith('SG25') else canary_id)
    assert 'plan' not in pair and 'action' not in pair
    for key in ('baseline', 'defect'):
        member = pair[key]
        current = Instance.model_validate(member['input'])
        for preserved in ('vessel', 'port', 'rotation', 'voyage'):
            assert current.model_dump(mode='json')[preserved] == original[preserved]
        assert current.onboard.tank_masses_kg == instance.onboard.tank_masses_kg
        assert current.onboard.covers_off == instance.onboard.covers_off
        placements = [Placement.model_validate(item) for item in member['placements']]
        final = current.onboard.model_copy(update={'placements': placements})
        candidate = next(box for box in current.containers if box.id == member['candidate_container_id'])
        assert current.load_list == [candidate.id]
        assert candidate.id not in {p.container_id for p in current.onboard.placements}
        if canary_id.startswith('SG'):
            anchor_id = pair['role_container_ids']['anchor']
            assert [p.container_id for p in current.onboard.placements] == [anchor_id]
            assert anchor_id not in current.load_list
            if canary_id.startswith('SG25'):
                anchor_slot = current.onboard.placements[0].slot
                assert covered_under_deck(current.vessel, anchor_slot, current.onboard.covers_off) is True
            scenario = current.vessel.imdg_placement_geometry.living_quarters_map_provenance
            registry = current.rule_overrides[STOWAGE]['assessment_scenarios']
            assert isinstance(registry[scenario.scenario_id], list)
            for placement in placements:
                box = next(b for b in current.containers if b.id == placement.container_id)
                context = context_for(current, box, 'primary', placement.slot)
                assert provenance_matches(scenario, context, registry)
            assert {row['container_id'] for row in registry[scenario.scenario_id]} >= {p.container_id for p in placements}
        full = imdg_violations(current, final, call_index=current.call_index)
        incremental = imdg_placement_violations(current, current.onboard, candidate,
                                                member['candidate_slot'], call_index=current.call_index)
        def signature(violation):
            # Incremental evaluation starts with the candidate while full
            # evaluation sorts placements; participant order is immaterial.
            return (violation.rule_id, violation.severity, violation.call_index,
                    violation.message, tuple(sorted(zip(violation.containers, violation.slots))))
        assert sorted(map(signature, incremental)) == sorted(map(signature, full))
        if key == 'baseline':
            assert full == []
        else:
            assert full and {v.rule_id for v in full} == {pair['expected_rule']}
            assert sorted(v.message for v in full) == sorted(pair['expected_messages'])
            assert not any('SG26' in v.message for v in full)
    assert instance.model_dump(mode='json') == original
