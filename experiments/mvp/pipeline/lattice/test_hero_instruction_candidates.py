"""Recommendation eligibility and authored wording targets, without native work."""
from copy import deepcopy
import json

import pytest

from experiments.mvp.pipeline.lattice import hero_instruction_candidates as film


def record(identifier='tiers', forced=2, placement='new'):
    return dict(id=identifier, key=identifier, canonical_rules=[dict(schema_version='stowlm.reader/2',
        constraint=dict(type='max_tiers',bay=3,zone='hold',max_tiers=1))],
        final_placement_sha256=placement, full_plan_sha256='plan-'+identifier,
        move_content_sha256='moves-'+identifier, recorded_gate_bound=True,recorded_checker_pass=True,
        recorded_all_frame_pass=True, recorded_frames=12,
        frame_provenance=dict(label='crane sequence not repaired',base_sequence=dict(label='crane sequence repaired')),
        displacement=dict(forced_count=forced,forced_ids=['a','b'],moved_count=8,additional_moved_count=6,
            rules=[dict(target_stacks=[dict(stack_id='00301H',baseline_count=3,plan_count=1,cap=1)])]))


def analysis(rows):
    baseline=dict(id='base',canonical_rules=[],final_placement_sha256='base')
    groups={}
    for row in [baseline]+rows:
        groups.setdefault(row['final_placement_sha256'],[]).append(dict(id=row['id'],key=row.get('key')))
    return dict(computed_utc='recorded',heroes=dict(hero=dict(records=[baseline]+rows,
        groups=dict(final_placement_sha256=groups),counts=dict(instructions=len(rows)))))


@pytest.fixture(autouse=True)
def no_fixture_or_native_reads(monkeypatch):
    monkeypatch.setattr(film,'_base_documents',lambda:dict(hero=dict(boxes=87,lifts=66)))
    monkeypatch.setattr(film,'_pending_backup',lambda base:pytest.fail('Primary selection needs no pending replay'))


def test_most_forced_eligible_choice_and_tier_alternative():
    close=record('closure',forced=36,placement='closure-placement')
    close['canonical_rules'][0]['constraint']=dict(type='close_bay',bay=9)
    result=film.build_candidates(analysis([record(),close]))['heroes']['hero']
    assert result['recommendation']['id']=='closure'
    assert result['stack_focused_alternative']['id']=='tiers'
    assert result['recommendation']['forced_boxes']==36
    assert result['recommendation']['additional_changed_boxes']==6
    assert result['recommendation']['frame_provenance']['base_sequence']['label']=='crane sequence repaired'


@pytest.mark.parametrize('failure',['forced_one','missing_forced','unbound','checker_failed','incomplete_frames','no_shrink','same_as_base','shared_placement'])
def test_ineligible_records_are_never_recommended(failure):
    item=record()
    if failure=='forced_one': item['displacement']['forced_count']=1
    elif failure=='missing_forced': item['displacement']['forced_count']=None
    elif failure=='unbound': item['recorded_gate_bound']=False
    elif failure=='checker_failed': item['recorded_checker_pass']=False
    elif failure=='incomplete_frames': item['recorded_all_frame_pass']=False
    elif failure=='no_shrink': item['displacement']['rules'][0]['target_stacks'][0]['plan_count']=3
    elif failure=='same_as_base': item['final_placement_sha256']='base'
    rows=[item]
    if failure=='shared_placement': rows.append(record('shared',placement='new'))
    result=film.build_candidates(analysis(rows))['heroes']['hero']
    assert result['recommendation'] is None


def test_stack_observation_is_not_all_attributed_to_cap():
    item=record()
    item['displacement']['rules'][0]['target_stacks']=[dict(stack_id='00301H',baseline_count=4,plan_count=2,cap=3)]
    stack=film.build_candidates(analysis([item]))['heroes']['hero']['recommendation']['target_stack_shrinks'][0]
    assert stack['observed_tier_reduction']==2
    assert stack['tier_reduction_required_by_cap_at_least']==1


def test_backup_pending_then_refreshes_to_new_checked_plan(monkeypatch):
    monkeypatch.setattr(film,'_base_documents',lambda:dict(hero=dict(boxes=163,lifts=115)))
    pending=dict(id='backup-max-04',forced_boxes=2,eligible=False,
        spoken_variants=film.spoken_variants(record()['canonical_rules']))
    monkeypatch.setattr(film,'_pending_backup',lambda base:deepcopy(pending))
    first=film.build_candidates(analysis([]))
    assert first['heroes']['hero']['recommendation'] is None
    assert first['heroes']['hero']['pending_candidate']['eligible'] is False
    text=film.render_candidates_table(first)
    assert 'Pending: backup-max-04' in text and 'after-state and gates remain pending' in text
    for variant in pending['spoken_variants']:
        assert variant['utterances'][0] in text
    second=film.build_candidates(analysis([record('new-backup')]))['heroes']['hero']
    assert second['recommendation']['id']=='new-backup' and second['pending_candidate'] is None


@pytest.mark.parametrize('bodies',[
    [dict(type='close_bay',bay=9)],
    [dict(type='max_tiers',bay=3,zone='hold',max_tiers=1)],
    [dict(type='max_tiers',bay=None,zone='deck',max_tiers=0)],
    [dict(type='dg_keep_away',imdg_class='2.2',min_bays=1,away_from=dict(kind='accommodation'))],
    [dict(type='dg_keep_away',imdg_class='3',min_bays=2,away_from=dict(kind='bay',bay=11))],
    [dict(type='one_port_per_hatch')],
    [dict(type='close_bay',bay=9),dict(type='max_tiers',bay=3,zone='hold',max_tiers=1)],
])
def test_three_authored_wordings_share_exact_valid_targets_without_model_claim(bodies):
    from experiments.mvp.reader.schema import validate_answer,answer_envelope
    rules=[dict(schema_version='stowlm.reader/2',constraint=body) for body in bodies]
    context=dict(bays=[3,9,11],max_tiers=dict(hold=3,deck=5),has_accommodation=True,hatch_covers=['cover'])
    variants=film.spoken_variants(rules)
    assert len(variants)==3
    assert len({tuple(v['utterances']) for v in variants})==3
    for variant in variants:
        assert variant['canonical_rules']==sorted(rules,key=film.canonical_json)
        assert len(variant['utterances'])==len(rules)
        assert variant['reader_model_tested'] is False and variant['confirmation_required'] is True
        for envelope in variant['canonical_rules']:
            assert answer_envelope(validate_answer(json.dumps(envelope),context))==envelope


def test_write_and_render_preserve_canonical_choice_and_provenance(tmp_path):
    destination=tmp_path/'recommendations.json'
    result=film.write_candidates(analysis([record()]),destination)
    assert json.loads(destination.read_text())==result
    rendered=film.render_candidates_table(result)
    assert '### Film instruction candidates' in rendered
    assert 'crane sequence not repaired' in rendered and 'baseline: crane sequence repaired' in rendered
    assert 'reader-model accuracy is unmeasured' in rendered
    assert 'not a minimal-change certificate' in rendered
