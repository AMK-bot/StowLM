"""Class-1 divisions and compatibility groups have distinct purposes."""

import pytest

from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.labels import hazard_division, parse_hazard_label


@pytest.mark.parametrize('label,group', [('1.4S', 'S'), ('1.4G', 'G')])
def test_class_one_division_matches_dgl_without_losing_compatibility_group(label, group):
    parsed = parse_hazard_label(label)
    assert parsed.division == '1.4'
    assert parsed.compatibility_group == group
    assert parsed.qualified_label == label
    assert hazard_division(label) == lookup_dgl(336).hazard_class


def test_wrong_class_one_division_still_mismatches_dgl():
    assert hazard_division('1.3G') != lookup_dgl(336).hazard_class


@pytest.mark.parametrize('label', [None, '', 'unresolved', '1', '1.7S', '1.4Z', '1.4s', ' 1.4S', '1.4S '])
def test_unknown_or_malformed_label_cannot_match_dgl(label):
    assert parse_hazard_label(label) is None
    assert hazard_division(label) is None
    assert hazard_division(label) != lookup_dgl(336).hazard_class


@pytest.mark.parametrize('label', ['1.4', '2.1', '2.2', '2.3', '3', '4.1', '4.2', '4.3', '5.1', '5.2', '6.1', '6.2', '7', '8', '9'])
def test_unqualified_divisions_are_preserved(label):
    parsed = parse_hazard_label(label)
    assert parsed.division == label
    assert parsed.compatibility_group is None
    assert parsed.qualified_label == label


def test_compatibility_qualified_labels_remain_distinct_for_doc_and_explosives():
    permitted = {'1.4S'}
    assert parse_hazard_label('1.4S').qualified_label in permitted
    assert parse_hazard_label('1.4G').qualified_label not in permitted
