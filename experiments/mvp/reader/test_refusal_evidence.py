"""Synthetic arithmetic and grounding; no model, planning, or checker calls."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.reader import refusal_evidence as module
from experiments.mvp.reader.select_binding import _namespace
from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers, Refusal


def ship(*, dg=False, retained=False):
    cells, stacks = [], []
    for bay in (1, 3):
        ids = [f'{bay:03d}01{tier:02d}' for tier in (2, 4)]
        stacks.append(dict(id=f's{bay}', bay=bay, on_deck=False, cell_ids=ids))
        cells.extend(dict(id=c, stack_id=f's{bay}', accepts_20=True,
                          accepts_40=True, accepts_45=True) for c in ids)
    raw = dict(id='synthetic', vessel=dict(cells=cells, stacks=stacks, hatch_covers=[],
                    exclusion_regions=[], bay_pairs=[dict(even_bay=2)]),
               containers=[dict(id='a', length_ft=20, dg=dict(imdg_class='3', subsidiary_risks=[], additional=[]) if dg else None),
                           dict(id='b', length_ft=40, dg=None)],
               load_list=['a', 'b'], discharge_list=[], onboard=dict(placements=[]),
               rotation=dict(calls=[]), call_index=0)
    if retained:
        raw['load_list'] = ['b']
        raw['onboard']['placements'] = [dict(container_id='a', slot='0010104')]
    instance = _namespace(raw)
    instance.model_dump = lambda **kwargs: deepcopy(raw)
    return instance, raw


def test_close_bay_arithmetic_and_long_box_footprints_without_checker():
    instance, _ = ship()
    proof = module.build_static_proof(instance, CloseBay(type='close_bay', bay=2))
    assert proof['demand_cells'] == proof['demand_teu'] == 3
    assert proof['open_cells'] == 0
    assert proof['nofit_ids'] == ['a', 'b']
    assert proof['load_boxes'] == [dict(container_id='a', length_ft=20, demand_cells=1,
                                       size_compatible_footprints=4, allowed_footprints=0),
                                  dict(container_id='b', length_ft=40, demand_cells=2,
                                       size_compatible_footprints=2, allowed_footprints=0)]
    assert proof['proven_impossible']
    assert proof['planning_attempted'] is False and proof['checker_called'] is False
    assert proof['checker_report'] is None


def test_dg_specific_nofit_does_not_fake_global_capacity_failure():
    instance, _ = ship(dg=True)
    answer = DgKeepAway(type='dg_keep_away', imdg_class='3', min_bays=2,
                       away_from={'kind': 'bay', 'bay': 1})
    proof = module.build_static_proof(instance, answer)
    assert proof['open_cells'] == 4 and proof['demand_cells'] == 3
    assert proof['nofit_ids'] == ['a']
    assert proof['proof_reasons'] == ['no_size_compatible_allowed_footprint']
    evidence = module.build_refusal_evidence(instance, answer)
    proposal = evidence['alternatives'][0]
    assert proposal['constraint']['constraint']['min_bays'] == 1
    assert proposal['relaxation'] and proposal['requires_user_choice'] and not proposal['applied']
    assert proposal['certificate'] == 'static_screen_only' and not proposal['would_work']
    assert 'not proved impossible' in proposal['text'] and 'must plan and check' in proposal['text']


def test_retained_conflict_is_fixed_and_close_bay_remains_loading_only():
    instance, _ = ship(retained=True)
    tier = MaxTiers(type='max_tiers', bay=1, zone='hold', max_tiers=1)
    proof = module.build_static_proof(instance, tier)
    assert proof['retained_conflicts'][0]['container_id'] == 'a'
    assert 'fixed_retained_conflict' in proof['proof_reasons']
    closed = module.build_static_proof(instance, CloseBay(type='close_bay', bay=1))
    assert not closed['retained_conflicts']


def test_tier_alternative_is_nearest_numeric_relaxation_and_never_applied():
    instance, raw = ship()
    before = deepcopy(raw)
    answer = MaxTiers(type='max_tiers', bay=1, zone='hold', max_tiers=0)
    evidence = module.build_refusal_evidence(instance, answer)
    proposal = evidence['alternatives'][0]
    assert proposal['constraint']['constraint']['max_tiers'] == 1
    assert proposal['numeric_distance'] == 1
    assert proposal['relaxation']
    assert evidence['parsed']['constraint']['max_tiers'] == 0
    assert raw == before and not evidence['automatically_applied']
    assert module.build_refusal_evidence(instance, answer) == evidence
    json.dumps(evidence)


def stored(tmp_path, raw):
    source = tmp_path/'instance.json'
    source.write_text(json.dumps(raw))
    path = tmp_path/'plan.json'
    path.write_text(json.dumps(dict(instance_id=raw['id'], passed=True,
        input=str(source), input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        plan=dict(id='synthetic-plan', instance_id=raw['id'], call_index=0, moves=[]))))
    return path


def test_witness_certificate_records_historical_provenance_without_fresh_checker(tmp_path, monkeypatch):
    instance, raw = ship()
    path = stored(tmp_path, raw)
    # Predicate-only witness check is mocked; synthetic empty plans are not
    # represented as real checker certificates by this test.
    monkeypatch.setattr('experiments.mvp.reader.occupancy.OccupancyConstrainedInstance.violations', lambda *args: [])
    answer = MaxTiers(type='max_tiers', bay=1, zone='hold', max_tiers=0)
    evidence = module.build_refusal_evidence(instance, answer, stored_witness=path)
    alternative = evidence['alternatives'][0]
    assert alternative['certificate'] == 'stored_checked_witness'
    assert not alternative['would_work']
    witness = alternative['stored_witness']
    assert witness['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert witness['full_checker_passed'] and not witness['fresh_audit']
    assert len(witness['plan_sha256']) == 64
    assert 'historical witness' in alternative['text']


@pytest.mark.parametrize('mutation', ['unpassed', 'wrong_instance', 'input_changed', 'no_input_hash'])
def test_invalid_stored_witness_cannot_certify_alternative(tmp_path, mutation):
    instance, raw = ship()
    path = stored(tmp_path, raw)
    row = json.loads(path.read_text())
    if mutation == 'unpassed':
        row['passed'] = False
    elif mutation == 'wrong_instance':
        row['instance_id'] = 'different'
    elif mutation == 'input_changed':
        row['input_sha256'] = '0'*64
    else:
        del row['input_sha256']
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='witness|provenance'):
        module.build_refusal_evidence(instance, MaxTiers(type='max_tiers', bay=1, zone='hold', max_tiers=0),
                                     stored_witness=path)


def test_loosening_does_not_invent_the_rule_from_model_rationale():
    instance, _ = ship()
    answer = Refusal(code='loosening', reason='Invented fire regulation 123.')
    evidence = module.build_refusal_evidence(instance, answer)
    assert all('123' not in fact['text'] for fact in evidence['facts'])
    assert 'existing-rule' not in evidence['required_fact_ids']
    ruled = module.build_refusal_evidence(instance, answer,
        rule_details={'name': 'Deck limit', 'description': 'At most two occupied deck tiers.'})
    assert 'Deck limit' in next(f['text'] for f in ruled['facts'] if f['id'] == 'existing-rule')


def test_ambiguous_asks_specific_supplied_question_without_guessing():
    instance, _ = ship()
    evidence = module.build_refusal_evidence(instance, Refusal(code='ambiguous', reason='Missing zone.'),
                                            clarification_question='Do you mean the hold or the deck?')
    assert evidence['clarifications'] == [{'id': 'clarification-1', 'text': 'Do you mean the hold or the deck?'}]
    assert not evidence['alternatives']
    assert 'constraint' not in evidence['parsed']


def test_screen_pass_never_claims_feasible_or_checker_passed():
    instance, _ = ship()
    evidence = module.build_refusal_evidence(instance, MaxTiers(type='max_tiers', bay=1, zone='hold', max_tiers=1))
    assert evidence['status'] == 'screen_passed'
    assert not evidence['alternatives']
    assert not evidence['checker_called'] and evidence['checker_report'] is None
    assert 'full checker are still required' in evidence['facts'][0]['text']
