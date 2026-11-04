import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from llm.exit import sizing as module


def settings(**extra):
    return dict(tiny=4,small=8,k=6,population=2,workers=4,steps=100,dpo=True,
        target_generation_minutes=75,sizing_safety_factor=1.,**extra)

def observation(**extra):
    values=dict(maps=12,workers=4,k=6,population_groups=3,model_batch_calls=36,
        decode_wall_s=600,pipeline_wall_s=700,map_cpu_worker_s=2400,dataset_s=120,
        training_reverification_s=60,sft_steps=100,sft_child_s=[200,210],dpo_completed=True,
        dpo_reference_s=50,dpo_update_s=100,audit_s=600,gold_control_trained=True,gold_control_s=800,
        baseline_audit_s=900)
    values.update(extra);return {'generation':1,'instance_count':12,'sizing_observation':values}

def test_parallel_envelopes_not_added():
    r=module.recommend(observation(),settings())
    assert r['per_map_s']['overlapped_decode_verify']==pytest.approx(700/12)
    assert r['pipeline']['verification_worker_per_map_s']==50
    assert r['pipeline']['decode_per_map_s']==50
    assert r['teacher_probe']['extra_added_s']==0

def test_teacher_worker_cost_already_in_total():
    r=module.recommend(observation(map_cpu_worker_s=4800),settings())
    assert r['per_map_s']['overlapped_decode_verify']==100

def test_future_population_batches_scale_decode():
    r=module.recommend(observation(population_groups=1,model_batch_calls=12,decode_wall_s=600),settings())
    assert r['pipeline']['decode_scaling_factor']==3
    assert r['pipeline']['decode_per_map_s']==150

def test_population_already_present_not_tripled():
    assert module.recommend(observation(),settings())['pipeline']['decode_scaling_factor']==1

def test_baseline_is_startup_only():
    a=module.recommend(observation(baseline_audit_s=0),settings())
    b=module.recommend(observation(baseline_audit_s=9999),settings())
    assert a['projected_generation_s']==b['projected_generation_s']
    assert b['baseline_startup_s']==9999

def test_failed_dpo_time_never_counts_as_complete():
    r=module.recommend({'generation':0,'instance_count':12,'timings':{'child_1_dpo_s':147}},settings())
    assert r['dpo_timing_status']=='unmeasured'
    assert r['fixed_s']['dpo_reference_and_update']==900
    assert 'DPO_full_update_unmeasured' in r['warnings']

def test_control_actually_adds_training():
    r=module.recommend(observation(gold_control_trained=False,gold_control_s=600),settings())
    assert r['fixed_s']['gold_control']==600+210+120

def test_short_budget_stays_honest_at_minimum_maps():
    args=settings();args['target_generation_minutes']=1
    r=module.recommend(observation(),args)
    assert r['maps']==6 and r['tiny']>=2 and r['small']>=2
    assert not r['target_projected_met']

def test_inputs_not_mutated_and_namespace_accepted():
    args=SimpleNamespace(**settings());summary=observation()
    before=repr(summary);module.recommend(summary,args)
    assert repr(summary)==before and args.small==8

def test_missing_worker_s_uses_one_pipeline_envelope():
    o=observation();del o['sizing_observation']['map_cpu_worker_s']
    r=module.recommend(o,settings());assert r['pipeline']['verification_worker_per_map_s']==pytest.approx(700/12)
    assert 'map_cpu_worker_totals_unmeasured' in r['warnings']

def test_no_audit_when_not_due():
    r=module.recommend(observation(),settings(sizing_include_audit=False,sizing_include_control=False))
    assert r['fixed_s']['audit']==r['fixed_s']['gold_control']==0

@pytest.mark.parametrize('bad',[float('nan'),-1,True])
def test_bad_measurements_fail_closed(bad):
    with pytest.raises(ValueError):module.recommend(observation(map_cpu_worker_s=bad),settings())
