"""D70 is a separate synthetic demand panel, never a main-set coverage claim."""
from copy import deepcopy
from collections import Counter

import pytest

from stowage.generate import cargo
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1


SUPPORTED = {'2.1', '2.2', '2.3', '3', '4.1', '4.3', '6.1', '8', '9', '1.4S'}


def test_stress_profile_is_labelled_separate_and_raises_supported_classes():
    main = load_param_file('cargo', 'feeder_export')
    before = deepcopy(main)
    with rule_freeze_v1(enabled=True):
        profile = cargo.dg_stress_profile()
        report = cargo.CargoGenerationReport()
        boxes = cargo.generate_load_list('straits', 0, 2000, seed=710, params=profile, report=report)
    assert profile['profile_id'] == 'synthetic_dg_stress_d70'
    assert 'APPROXIMATION' in profile['sampling']['dg_policy']
    assert {row['id'] for row in profile['dg']['classes']} == SUPPORTED
    assert {box.dg.imdg_class for box in boxes} == SUPPORTED
    assert all(box.dg is not None for box in boxes)
    assert report.realised['unsupported_demand'] == 0
    assert load_param_file('cargo', 'feeder_export') == before
    assert main == before


def test_stress_profile_requires_activation_without_touching_default():
    with rule_freeze_v1(enabled=False):
        with pytest.raises(cargo.UnsupportedCargoError, match='DG-stress.*activation'):
            cargo.generate_load_list('straits', 0, 1, seed=710, params='dg_stress')


@pytest.mark.parametrize('pair_id,classes', [('flammable_water_reactive', {'3', '4.3'}),
                                           ('water_reactive_acid', {'4.3', '8'})])
def test_explicit_pair_cohorts_concentrate_real_segregation_candidates(pair_id, classes):
    from stowage.rules.imdg.tables import general_segregation
    with rule_freeze_v1(enabled=True):
        profile = cargo.dg_stress_profile(pair_id=pair_id)
        assert {row['id'] for row in profile['dg']['classes']} == classes
        containing_pair = 0
        for seed in range(15):
            boxes = cargo.generate_load_list('straits', 0, 4, seed=seed, params=profile)
            if {box.dg.imdg_class for box in boxes} == classes:
                containing_pair += 1
        # This is raw candidate-pair presence, NOT final-plan binding/admission.
        assert containing_pair >= 10
    assert general_segregation(*sorted(classes)).level > 0


def test_unknown_stress_pair_is_explicit_error():
    with pytest.raises(ValueError, match='unknown DG-stress pair'):
        cargo.dg_stress_profile(pair_id='not_a_pair')


def test_stress_batteries_include_labelled_minority_recycling_declarations():
    with rule_freeze_v1(enabled=True):
        profile = cargo.dg_stress_profile()
        profile['dg']['classes'] = [row for row in profile['dg']['classes'] if row['id'] == '9']
        report = cargo.CargoGenerationReport()
        boxes = cargo.generate_load_list('straits', 0, 1000, seed=710, params=profile, report=report)
    counts = Counter()
    for box in boxes:
        facts = report.realised['shipment_scenarios'][box.id]['primary']['sw19']
        counts[facts['transport_purpose']] += 1
        if facts['transport_purpose'] == 'recycling':
            assert facts['special_provision_applicability'] == {'376': False, '377': True}
            assert facts['transport_in_accordance'] is True
            assert facts['voyage']['short_international_voyage'] is False
            assert '377' in facts['provenance']['clause']
            assert box.dg.special_provisions is None
            assert box.dg.short_international_voyage is None
    assert 100 <= counts['recycling'] <= 200
    assert counts['normal'] + counts['recycling'] == 1000
    assert report.requested['dg_stress']['battery_recycling_share'] == .15


@pytest.mark.parametrize('pair_id', ['flammable_water_reactive', 'water_reactive_acid'])
def test_generated_pair_cohorts_activate_real_checker_segregation(pair_id):
    from stowage.checker.rules_imdg import SHIP, STOWAGE, imdg_violations
    from stowage.model.ship import Placement
    from tests.checker.test_rules_imdg_assessments import _shipment

    with rule_freeze_v1(enabled=True):
        profile = cargo.dg_stress_profile(pair_id=pair_id)
        for seed in range(15):
            report = cargo.CargoGenerationReport()
            boxes = cargo.generate_load_list('straits', 0, 2, seed=seed, params=profile, report=report)
            if len({box.dg.imdg_class for box in boxes}) == 2:
                break
        else:
            pytest.fail('seeded pair cohort never produced its two classes')
        instance, ship, _, _ = _shipment(1402, 'II')
        instance.containers = boxes
        instance.rule_overrides[STOWAGE] = {}
        classes = [box.dg.imdg_class for box in boxes]
        for stack in instance.vessel.stacks:
            stack.dg_classes_allowed = classes
        for area in instance.vessel.dg_doc_areas:
            area.classes_allowed = classes
        ship.placements = [Placement(slot=slot, container_id=box.id)
                           for box, slot in zip(boxes, ('0010182', '0030182'))]
        # Synthetic design fixture: separate these CTUs in both horizontal axes.
        # This isolates actual IMDG enforcement, not whole-physics admission.
        for cell in instance.vessel.cells:
            if cell.id.startswith('003'):
                cell.tcg_m += 12
        bound = cargo.bind_shipment_scenarios(instance, report)
        assert imdg_violations(bound, ship, call_index=0) == []
        first = next(cell for cell in bound.vessel.cells if cell.id == '0010182')
        for cell in bound.vessel.cells:
            if cell.id.startswith('003'):
                cell.lcg_m, cell.tcg_m = first.lcg_m + 6.1, first.tcg_m
        violations = imdg_violations(bound, ship, call_index=0)
        assert violations and {violation.rule_id for violation in violations} == {SHIP}
        assert any('SG26' in violation.message or 'level 2' in violation.message
                   for violation in violations)
