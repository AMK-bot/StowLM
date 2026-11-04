"""Extract exact recorded facts without a planner, checker, or native physics."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from experiments.mvp.pipeline.lattice import film_close_bay_facts as facts
from experiments.mvp.pipeline.lattice import film_analysis
from experiments.mvp.pipeline.lattice.store import entry_key


@pytest.fixture
def recorded(tmp_path,monkeypatch):
    primary=facts.HERE/'store'
    hero=facts.HERO_SHA256
    keys=[entry_key(hero,[]),entry_key(hero,facts.RULES)]
    rows=[json.loads((primary/(key+'.json')).read_text()) for key in keys]
    # Keep the real instance, public plans and checker counts; compact the
    # irrelevant reports/curves. No verdict is produced by this fixture.
    for row in rows:
        gate=row['gate']
        gate['checker']={name:deepcopy(gate['checker'][name]) for name in ('passed','count')}
        if row['canonical_rules']:
            for frame in gate['gauges']['physics']['frames']:
                for key in ('bm_nm','sf_n','gz'):
                    frame[key]=[]
        else:
            gate['gauges'].pop('physics',None)
        row.pop('binding_evidence',None)
        row['binding_evidence']=dict(status='no_checked_plan',recovered_incumbent=True)
    frozen=deepcopy(rows[0])
    directory=tmp_path/'store';directory.mkdir()
    def snapshot(values=rows):
        catalogue={}
        for row in values:
            key=entry_key(row['instance_sha256'],row['canonical_rules'])
            raw=(canonical_json(row)+'\n').encode()
            (directory/(key+'.json')).write_bytes(raw)
            catalogue[key]=hashlib.sha256(raw).hexdigest()
        (directory/'catalogue.json').write_text(canonical_json(catalogue)+'\n')
        return film_analysis.authenticated_snapshot(directory)
    monkeypatch.setattr(facts,'HERE',tmp_path)
    monkeypatch.setattr(film_analysis,'HERE',tmp_path)
    return dict(rows=rows,frozen=frozen,snapshot=snapshot,hero=hero,directory=directory)


def build(recorded):
    return facts.build_close_bay_facts(recorded['snapshot'](),recorded['hero'],
                                      frozen_base_record=recorded['frozen'])


def test_exact_counts_signed_peak_and_selected_vs_base_provenance(recorded,monkeypatch):
    # A native call would be a test failure; public replay remains real.
    from stowage import checker
    monkeypatch.setattr(checker,'check',lambda *args,**kwargs:pytest.fail('No checker rerun'))
    result=build(recorded)
    assert result['boxes']['count']==87
    assert result['pointwise_forced']['count']==36
    assert len(result['pointwise_forced']['container_ids'])==36
    assert result['changed_final_positions']['count']==84
    assert result['changed_final_positions']['additional_count']==48
    assert result['extra_lifts']['baseline']==66 and result['extra_lifts']['selected']==82
    assert result['extra_lifts']['delta']==16
    frames=result['native_frames']
    assert frames['n_frames']==146 and frames['all_frames_passed']
    assert frames['peak_abs_heel_deg']==pytest.approx(1.6379253206268005)
    assert frames['peak_signed_heel_deg']==pytest.approx(-1.6379253206268005)
    assert frames['peak_frame']==109
    assert result['producer']['recorded_plan_producer'] is None
    assert result['producer']['recorded_producer_label'] is None
    assert result['producer']['original_binding_plan_matches_selected'] is True
    assert result['sequence_provenance']['repaired'] is False
    assert result['sequence_provenance']['base_sequence']['repaired'] is True
    assert 'Frontier Engine 0.13.0' in result['producer']['derived_display_label']
    assert not result['native_compute_performed'] and not result['native_gates_repeated']
    assert 'engine_wall_s' not in result
    selected=result['sources']['selected']
    assert selected['raw_sha256']==hashlib.sha256(Path(selected['absolute_path']).read_bytes()).hexdigest()
    assert selected['catalogue_sha256']==hashlib.sha256((recorded['directory']/'catalogue.json').read_bytes()).hexdigest()


@pytest.mark.parametrize('bad',['instance','plan','rules','gate_instance','gate_plan','audit_rules','frame_flag','frames_missing','duplicate_step','nonfinite_heel','lift_count','lift_delta','native_original'])
def test_stale_bindings_or_malformed_saved_frames_never_produce_facts(recorded,bad):
    row=recorded['rows'][1]
    if bad=='instance':row['instance']['id']='changed'
    elif bad=='plan':row['plan']['id']='changed'
    elif bad=='rules':row['canonical_rules'][0]['constraint']['bay']=11
    elif bad=='gate_instance':row['gate']['instance_sha256']='0'*64
    elif bad=='gate_plan':row['gate']['plan_sha256']='0'*64
    elif bad=='audit_rules':row['gate']['rules'][0]['envelope']['constraint']['bay']=11
    elif bad=='frame_flag':row['gate']['gauges']['physics']['frames'][5]['strength_passed']=False
    elif bad=='frames_missing':row['gate']['gauges']['physics']['frames'].pop()
    elif bad=='duplicate_step':row['gate']['gauges']['physics']['frames'][5]['step']=4
    elif bad=='nonfinite_heel':row['gate']['gauges']['physics']['frames'][5]['heel_deg']=True
    elif bad=='lift_count':row['gate']['checker']['count']['weighted_nonproductive']=81
    elif bad=='lift_delta':row['lift_delta']=15
    elif bad=='native_original':row['original_binding_plan']['id']='different-original'
    with pytest.raises(ValueError):build(recorded)


def test_source_bytes_or_in_memory_snapshot_mutation_are_rejected(recorded):
    snapshot=recorded['snapshot']()
    selected=next(s for s in snapshot['rows'] if s['record']['canonical_rules'])
    selected['record']['computed_utc']='forged-memory-value'
    with pytest.raises(ValueError,match='snapshot record'):
        facts.build_close_bay_facts(snapshot,recorded['hero'],frozen_base_record=recorded['frozen'])
    snapshot=recorded['snapshot']()
    selected=next(s for s in snapshot['rows'] if s['record']['canonical_rules'])
    Path(selected['path']).write_text('{}')
    with pytest.raises(ValueError,match='entry bytes changed'):
        facts.build_close_bay_facts(snapshot,recorded['hero'],frozen_base_record=recorded['frozen'])


def test_only_the_frozen_baseline_is_compared(recorded):
    frozen=deepcopy(recorded['frozen']);frozen['plan']['id']='another-baseline'
    with pytest.raises(ValueError,match='frozen baseline'):
        facts.build_close_bay_facts(recorded['snapshot'](),recorded['hero'],frozen_base_record=frozen)


def test_exact_canonical_entry_required_not_a_similar_sentence(recorded):
    recorded['rows'][1]['sentences']=['Nothing goes into bay nine on this call.']
    assert build(recorded)['canonical_key']==entry_key(recorded['hero'],facts.RULES)
    snapshot=recorded['snapshot']([recorded['rows'][0]])
    with pytest.raises(ValueError,match='missing'):
        facts.build_close_bay_facts(snapshot,recorded['hero'],frozen_base_record=recorded['frozen'])
