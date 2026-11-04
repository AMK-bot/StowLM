"""Rule coverage never substitutes container presence for observed constraints."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


@pytest.fixture
def binding():
    path = Path(__file__).with_name('rule_binding.py')
    spec = importlib.util.spec_from_file_location('_test_gold_rule_binding', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def box(identity, mass=12000., *, direct=False, dg=None):
    return NS(id=identity, gross_mass_kg=mass, direct_delivery=direct, dg=dg)


def instance(boxes, *, placements=()):
    return NS(containers=boxes, load_list=[b.id for b in boxes], discharge_list=[],
              onboard=NS(placements=list(placements)), vessel=NS(fixed_loads=[]))


def placement(identity, slot):
    return NS(container_id=identity, slot=slot)


def replay(placements=(), *, status='applicable', crossing=0, legal=True):
    observation = NS(source='migrated_move_engine', status=status,
                     ship_lifts_at_or_above_h=crossing, yard_lifts_at_or_above_h=0,
                     rejected_crossing_lifts=0, yard_h_blocking_rehandles=0,
                     model_dump=lambda: dict(source='migrated_move_engine', status=status,
                         ship_lifts_at_or_above_h=crossing, yard_lifts_at_or_above_h=0,
                         rejected_crossing_lifts=0, yard_h_blocking_rehandles=0))
    return NS(legal=legal, d62_crossing=observation,
              final_state=NS(ship=NS(placements=list(placements))))


def test_timeout_keeps_input_delivery_and_unknown_plan_buckets(binding):
    observed = binding.observe(instance([box('direct', direct=True)]), None)
    assert observed['direct_delivery_present']['status'] == 'present'
    assert observed['direct_delivery_present']['count'] == 1
    assert observed['crossing_used']['status'] == 'unknown'
    assert observed['kg_tight_stacks']['status'] == 'unknown'
    assert observed['dg_segregation_binding']['status'] == 'unknown'


def test_crossing_is_measured_not_instance_labels(binding, monkeypatch):
    subject = instance([box('a')])
    subject.d62_bucket = True
    monkeypatch.setattr(binding, '_replay', lambda *args: replay(crossing=0))
    assert binding.observe(subject, NS(moves=[]))['crossing_used']['status'] == 'absent'
    monkeypatch.setattr(binding, '_replay', lambda *args: replay(crossing=1))
    assert binding.observe(subject, NS(moves=[]))['crossing_used']['status'] == 'present'


def test_switch_off_observations_are_unknown(binding, monkeypatch):
    monkeypatch.setattr(binding, '_replay', lambda *args: replay(status='not_applicable'))
    assert binding.observe(instance([]), NS(moves=[]))['crossing_used']['status'] == 'unknown'


def test_adjacent_mass_threshold_is_inclusive_and_physical(binding, monkeypatch):
    subject = instance([box('low', 13000.), box('high', 12000.), box('unrelated', 13000.)])
    monkeypatch.setattr(binding, '_replay', lambda *args: replay([
        placement('low', '0010182'), placement('high', '0010184'), placement('unrelated', '0030186')]))
    value = binding.observe(subject, NS(moves=[]))['kg_tight_stacks']
    assert value['status'] == 'present' and value['count'] == 1
    subject.containers[0].gross_mass_kg = 13000.01
    assert binding.observe(subject, NS(moves=[]))['kg_tight_stacks']['status'] == 'absent'


def test_failed_replay_never_claims_final_kg_or_dg_absence(binding, monkeypatch):
    monkeypatch.setattr(binding, '_replay', lambda *args: replay(legal=False))
    value = binding.observe(instance([box('a')]), NS(moves=[]))
    assert value['kg_tight_stacks']['status'] == 'unknown'
    assert value['dg_segregation_binding']['status'] == 'unknown'


def test_dg_presence_alone_is_not_a_segregation_witness(binding, monkeypatch):
    dg = NS(imdg_class='3', subsidiary_risks=[], segregation_codes=[], limited_quantity=False,
            un_number='1203', packing_group='II')
    monkeypatch.setattr(binding, '_replay', lambda *args: replay([placement('one', '0010182')]))
    result = binding.observe(instance([box('one', dg=dg)]), NS(moves=[]))
    assert result['dg_segregation_binding']['status'] == 'absent'


def test_bucket_summary_counts_rejection_and_timeout_and_diagnostic(binding):
    present = {key: {'status': 'present', 'count': 1} for key in binding.BUCKETS}
    unknown = {key: {'status': 'unknown', 'count': None} for key in binding.BUCKETS}
    rows = [dict(spec_key='good', accepted=True, rule_binding=present),
            dict(spec_key='rejected', accepted=False, rule_binding=present),
            dict(spec_key='timeout', accepted=False, status='timeout', rule_binding=unknown),
            dict(spec_key='diagnostic', accepted=True, diagnostic=True, rule_binding=present),
            dict(spec_key='ineligible', accepted=True, evidence_eligible=False, rule_binding=present)]
    result = binding.summarize(rows, published_spec_keys={'good', 'diagnostic', 'ineligible'})
    for bucket in result.values():
        assert bucket['attempted'] == 5
        assert bucket['present_attempted'] == 4
        assert bucket['unknown_attempted'] == 1
        assert bucket['published'] == 1
        assert bucket['yield_per_present_attempt'] == .25
        assert bucket['yield_per_all_attempts'] == .2


def test_final_writer_inventory_controls_publication_denominator(binding):
    rows = [dict(spec_key='lost', accepted=True,
                 rule_binding={key: {'status': 'present', 'count': 1} for key in binding.BUCKETS})]
    result = binding.summarize(rows, published_spec_keys=set())
    assert all(bucket['published'] == 0 for bucket in result.values())


def test_numeric_dg_separation_obligation_has_actual_pair_witness(binding, monkeypatch):
    def hazard(cls):
        return NS(imdg_class=cls, subsidiary_risks=[], segregation_codes=[],
                  limited_quantity=False, un_number='1203', packing_group='II')
    subject = instance([box('flammable', dg=hazard('3')), box('oxidizer', dg=hazard('5.1'))])
    monkeypatch.setattr(binding, '_replay', lambda *args: replay([
        placement('flammable', '0010182'), placement('oxidizer', '0090182')]))
    observed = binding.observe(subject, NS(moves=[]))['dg_segregation_binding']
    assert observed['status'] == 'present'
    assert observed['pairs'] == [{'containers': ['flammable', 'oxidizer'], 'level': 2}]
    assert 'not minimum-distance slack' in observed['definition']


def test_dg_x_cell_is_unknown_instead_of_assumed_non_binding(binding, monkeypatch):
    dg = NS(imdg_class='3', subsidiary_risks=[], segregation_codes=[], limited_quantity=False,
            un_number='1203', packing_group='II')
    subject = instance([box('a', dg=dg), box('b', dg=dg)])
    monkeypatch.setattr(binding, '_replay', lambda *args: replay([
        placement('a', '0010182'), placement('b', '0090182')]))
    assert binding.observe(subject, NS(moves=[]))['dg_segregation_binding']['status'] == 'unknown'


def test_admitted_envelope_observation_controls_publication_numerator(binding):
    present = {key: {'status': 'present', 'count': 1} for key in binding.BUCKETS}
    absent = {key: {'status': 'absent', 'count': 0} for key in binding.BUCKETS}
    rows = [dict(spec_key='one', accepted=True, rule_binding=present)]
    result = binding.summarize(rows, published_spec_keys={'one'},
                               published_observations={'one': absent})
    assert all(value['published'] == 0 for value in result.values())
    assert all(value['present_attempted'] == 1 for value in result.values())


def test_publication_is_once_per_spec_while_attempts_remain_complete(binding):
    present = {key: {'status': 'present', 'count': 1} for key in binding.BUCKETS}
    rows = [dict(spec_key='same', accepted=True, rule_binding=present),
            dict(spec_key='same', accepted=True, rule_binding=present)]
    result = binding.summarize(rows, published_spec_keys={'same'},
                               published_observations={'same': present})
    assert all(value['published'] == 1 for value in result.values())
    assert all(value['attempted'] == 2 and value['present_attempted'] == 2 for value in result.values())
    assert all(value['yield_per_present_attempt'] == .5 for value in result.values())


def test_campaign_summary_reports_timeout_bucket_denominator(binding):
    """Exercise the consumer, so a disconnected observation helper cannot pass."""
    path = Path(__file__).with_name('gold.py')
    spec = importlib.util.spec_from_file_location('_test_bucket_campaign', path)
    campaign = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(campaign)
    present = {key: {'status': 'present', 'count': 1} for key in binding.BUCKETS}
    rows = [dict(spec_key='accepted', rung='tiny', accepted=True, status='feasible',
                 rule_binding=present),
            dict(spec_key='timeout', rung='tiny', accepted=False, status='timeout')]
    summary = campaign.summarize(rows, dict(epoch_id='e', diagnostic=False))
    for value in summary['rule_binding_yield'].values():
        assert value['attempted'] == 2
        assert value['published'] == 1
        assert value['unknown_attempted'] == 1
        assert value['yield_per_all_attempts'] == .5
