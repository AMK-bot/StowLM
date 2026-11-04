"""Native E8 evidence on the approved tiny case, without corpus allowlist reads."""
from dataclasses import replace

from engine.unary import certify
from stowage.io import load_instance
from stowage.feasibility.tables import build_tables
from engine.bounds.exact.test_plain import fixture

TARGET = 'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000040.json'


def test_heat_booking_exhaustive_native_supported_departures():
    instance = load_instance(TARGET)
    result = certify(instance)
    assert result['status'] == 'certified_unary_infeasible'
    assert result['box'] == 'QZXU0000035'
    assert result['rule'] == 'expert.e8.away_from_heat'
    assert result['tier'] == 'T3'
    assert result['supported_stows'] == 192
    assert result['evidence']['rejected_supported_stows'] == 192
    assert not result['full_checker_pass_claim']
    assert 'unpeeled' in result['proof_scope']


def test_unary_does_not_trust_empty_ship_masks_or_partial_enumeration():
    instance = load_instance(TARGET)
    tables = build_tables(instance)
    tables.static[:] = False
    result = certify(instance, tables=tables)
    assert result['status'] == 'certified_unary_infeasible'
    assert result['supported_stows'] == 192
    assert not result['evidence']['legacy_unary_masks_used']
    partial = certify(instance, tables=tables, max_supported_stows=2)
    assert partial['status'] == 'unknown' and not partial['complete']
    assert partial['supported_stows'] == 2


def test_unary_mismatched_tables_and_no_booking_are_unknown():
    instance = load_instance(TARGET)
    tables = build_tables(instance)
    assert certify(instance, tables=replace(tables, instance=instance.model_copy()))['status'] == 'unknown'
    assert certify(fixture())['status'] == 'unknown'
