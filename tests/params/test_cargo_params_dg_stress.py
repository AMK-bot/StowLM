"""The D70 stress profile is separate, documented synthetic demand."""
from copy import deepcopy
import math

from stowage.generate.cargo import dg_stress_profile
from stowage.params import load_param_file
from stowage.rules.imdg.dgl import entries_for_un
from stowage.rules.imdg.labels import hazard_division, parse_hazard_label
from tests.params.test_cargo_params import check_provenance


def test_stress_profile_numeric_sources_and_dgl_entries_are_complete():
    profile = load_param_file('cargo', 'dg_stress')
    check_provenance(profile)
    assert profile['requires_rule_freeze_v1'] is True
    assert 'separate' in profile['panel_policy']
    assert math.isclose(sum(row['share']['value'] for row in profile['dg']['classes']), 1)
    for row in profile['dg']['classes']:
        label = parse_hazard_label(row['id'])
        assert label is not None
        assert math.isclose(sum(un['share']['value'] for un in row['un_numbers']), 1)
        for un in row['un_numbers']:
            assert any(entry.hazard_class == hazard_division(row['id'])
                       and (label.compatibility_group is None or entry.compatibility_group == label.compatibility_group)
                       for entry in entries_for_un(int(un['un_number'])))
            assert 'IMO' in un['classification_source_document']
            assert un['classification_source_locator']
    equipment = {row['id'] for row in profile['equipment']}
    assert set(profile['dg']['eligible_equipment_ids']) <= equipment
    assert set(profile['oog']['eligible_equipment_ids']) <= equipment


def test_pair_profile_copies_are_independent_and_equal_share():
    initial = deepcopy(load_param_file('cargo', 'dg_stress'))
    pair = dg_stress_profile(pair_id='flammable_water_reactive')
    assert {row['id'] for row in pair['dg']['classes']} == {'3','4.3'}
    assert [row['share']['value'] for row in pair['dg']['classes']] == [.5,.5]
    pair['equipment'].clear()
    assert dg_stress_profile() == initial
    assert load_param_file('cargo', 'dg_stress') == initial
