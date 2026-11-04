"""Admission and canary targets follow the emitted catalogue identifiers."""
import importlib.util
from pathlib import Path
import tomllib

from eval.snapshot_io import read_snapshot

HERE=Path(__file__).parent


def required_ids():
    return read_snapshot(HERE/'REQUIRED_RULES.json').json()['required_rule_ids']


def test_required_rule_registry_uses_all_four_canonical_e3_ids_without_duplicates():
    ids=required_ids()
    assert not any(rule.startswith('port.crane.') for rule in ids)
    assert {'port.e3.quay_crossing','port.e3.yard_crossing',
            'port.e3.quay_height','port.e3.yard_height'}<=set(ids)
    assert len(ids)==len(set(ids))


def test_all_canary_primary_and_mask_targets_are_required_catalogue_rules():
    spec=importlib.util.spec_from_file_location('required_canary_registry',HERE/'qualified_canary_cases.py')
    registry=importlib.util.module_from_spec(spec);spec.loader.exec_module(registry)
    ids=set(required_ids())
    for case in registry.CASES.values():
        assert {case['expected_rule'],*case['expected_rules'],*case['expected_mask_reasons']}<=ids


def test_required_registry_has_no_removed_or_unknown_catalogue_ids():
    data=read_snapshot(HERE.parents[1]/'src/stowage/rules/catalogue.toml')
    present={rule['id'] for rule in tomllib.loads(data.data.decode())['rules']}
    assert set(required_ids())<=present
