"""Auditor regressions run without solver/checker imports or saved plans."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import (equal_evidence, hard_evidence, physical_projection,
                   validate_row_binding, validate_selected)


def graph_fixture():
    # Hold target under A; deck long bridge on A/B; upper box on far half B.
    # A second hold target opens B later. Ports are revisited to test first-next.
    cells = [dict(id=c, stack_id=s) for c, s in (
        ('0010102', 'HA'), ('0030102', 'HB'), ('0010182', 'DA'),
        ('0030182', 'DB'), ('0030184', 'DB'))]
    stacks = [dict(id=s, on_deck=deck, hatch_cover_id=h) for s, deck, h in (
        ('HA', False, 'A'), ('HB', False, 'B'), ('DA', True, 'A'), ('DB', True, 'B'))]
    covers = [dict(id='A', deck_stack_ids=['DA']), dict(id='B', deck_stack_ids=['DB'])]
    instance = dict(call_index=0, rotation=dict(calls=[dict(locode=p) for p in ('LOAD', 'P1', 'P2', 'P1', 'LAST')]),
                    vessel=dict(cells=cells, stacks=stacks, hatch_covers=covers),
                    objective=dict(future_restow_lifts_per_box=3),
                    containers=[dict(id=b, pod=p) for b, p in (('hold1', 'P1'), ('hold2', 'P2'),
                                                              ('long', 'LAST'), ('upper', 'LAST'))])
    placements = [dict(container_id=b, slot=q) for b, q in (
        ('hold1', '0010102'), ('hold2', '0030102'), ('long', '0020182'), ('upper', '0030184'))]
    return instance, placements


def test_physical_audit_counts_cross_cover_bridge_once_and_first_matching_call():
    instance, placements = graph_fixture()
    result = physical_projection(instance, placements)
    assert result['future'][0]['covers'] == ['A']
    assert result['future'][0]['blockers'] == ['long', 'upper']
    assert result['future'][1]['covers'] == ['B']
    assert result['future'][1]['blockers'] == ['long', 'upper']
    assert result['future'][2]['targets'] == []  # repeated P1 has no cargo left
    assert result['future'][2]['restow_lifts'] == 0
    assert result['restow_later'] == 12
    assert result['current_hatch_lower_bound'] == 4
    assert result['hatch_later'] == 4


def test_physical_audit_refuses_overlapping_selected_long_and_short_boxes():
    instance, placements = graph_fixture()
    placements.append(dict(container_id='duplicate-half', slot='0010182'))
    with pytest.raises(AssertionError, match='overlapping'):
        physical_projection(instance, placements)


def test_hard_evidence_keeps_measured_limits_multiplicity_and_all_raw_fields():
    violations = [dict(rule_id='R', severity='hard', measured=2., limit=1., message='one'),
                  dict(rule_id='R', severity='hard', measured=3., limit=1., message='two'),
                  dict(rule_id='S', severity='soft', measured=4.)]
    left = hard_evidence(dict(violations=violations))
    right = hard_evidence(dict(violations=list(reversed(violations))))
    equal_evidence(left, right)
    right[0]['limit'] += .1
    with pytest.raises(AssertionError, match='limit'):
        equal_evidence(left, right)
    assert len(left) == 2


def test_binding_check_does_not_waive_hash_or_cold_mismatch():
    entry = dict(instance_id='map', path='local/path', sha256='abc', size='medium', ports=3,
                 vessel='ship', fill_pct=60, development=True)
    binding = dict(implementation_sha256='code', native_sha256='native', params_sha256='params')
    config = dict(binding, input=entry['path'], size=entry['size'], ports=3, vessel='ship',
                  fill_pct=60, development=True)
    row = dict(complete=True, instance_id='map', input='local/path', input_sha256='abc',
               arm='port_lp', cold=True, config=config, run_key='key')
    driver = NS(run_key=lambda c, h: 'key')
    validate_row_binding(row, entry, 'port_lp', binding, driver)
    row['config'] = dict(config, native_sha256='changed')
    with pytest.raises(AssertionError, match='native_sha256'):
        validate_row_binding(row, entry, 'port_lp', binding, driver)
    row['config'], row['cold'] = config, False
    with pytest.raises(AssertionError, match='cold'):
        validate_row_binding(row, entry, 'port_lp', binding, driver)


def test_all_aboard_comes_from_actual_final_ship_not_saved_assignment_claim():
    row = dict(report={})
    instance = NS(load_list=['A', 'B'])
    report = NS(model_dump=lambda **_: dict(shut_out=[], count={}))
    final = NS(model_dump=lambda **_: dict(placements=[dict(container_id='A', slot='0010182')]))
    replay = NS(legal=True, final_state=NS(ship=final))
    with pytest.raises(AssertionError, match='every expected'):
        validate_selected(row, instance, None, replay, report, None, None, False)
