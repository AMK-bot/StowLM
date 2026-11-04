"""Request artifacts cannot masquerade as qualified external canary pairs."""
import json
from copy import deepcopy

import pytest

from stowage.model import Instance
from stowage.rules.imdg.canary_contexts import load_context_catalog, main, materialize_context_pair
from tests.rules.test_imdg_canary_contexts import _input


def test_requests_are_not_placeholder_qualified_external_pairs():
    catalog = load_context_catalog()
    assert catalog['status'] == 'pending_qualification'
    assert catalog['evidence_eligible'] is False
    assert catalog['external_export_contract'] == 'qualified-pair-v1'
    assert set(catalog['requests']) == {'SW1', 'SW2', 'H1', 'SW19', 'SG17', 'SG25_2_1', 'SG25_3'}
    assert 'pairs' not in catalog
    for request in catalog['requests'].values():
        assert 'input' not in request and 'plan' not in request and 'action' not in request
    assert len(catalog['fixture_source_hashes']) == 8
    assert all(len(value) == 64 for value in catalog['fixture_source_hashes'].values())


def test_unknown_csc_rating_cannot_be_filled_to_make_a_fixture_pass():
    instance = _input()
    for box in instance.containers:
        box.max_gross_mass_kg = None
    original = deepcopy(instance.model_dump(mode='json'))
    with pytest.raises(ValueError, match='adequate CSC plate'):
        materialize_context_pair(instance, 'H1')
    assert instance.model_dump(mode='json') == original


def test_protection_flag_is_not_an_authored_assessment():
    instance = _input()
    catalog = load_context_catalog()
    catalog['requests']['H1']['cargo']['subject']['facts']['dry'] = True
    original = deepcopy(instance.model_dump(mode='json'))
    with pytest.raises(ValueError, match='protection/verification flags'):
        materialize_context_pair(instance, 'H1', catalog=catalog)
    assert instance.model_dump(mode='json') == original


def test_a_slice_missing_requested_decision_cells_cannot_invent_them():
    instance = _input()
    catalog = load_context_catalog()
    catalog['requests']['H1']['baseline_request']['placements']['subject'] = '9999982'
    original = deepcopy(instance.model_dump(mode='json'))
    with pytest.raises(ValueError, match='retain every requested'):
        materialize_context_pair(instance, 'H1', catalog=catalog)
    assert instance.model_dump(mode='json') == original


def test_context_json_cli_is_a_roundtrip_not_a_plan_or_qualification(tmp_path):
    instance = _input()
    source, destination = tmp_path/'instance.json', tmp_path/'contexts.json'
    source.write_text(instance.model_dump_json())
    main(['--instance', str(source), '--case', 'H1', '--output', str(destination)])
    result = json.loads(destination.read_text())
    assert result['status'] == 'context_only' and result['evidence_eligible'] is False
    assert result['fixture_source_hashes'] == load_context_catalog()['fixture_source_hashes']
    for member in ('baseline', 'defect'):
        assert isinstance(result[member]['input'], dict)
        Instance.model_validate(result[member]['input'])
        assert 'plan' not in result[member] and 'action' not in result[member]
    assert source.read_text() == instance.model_dump_json()
