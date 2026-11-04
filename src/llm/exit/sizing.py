"""D137 pure, conservative capacity projection; never reserves resources.

APPROXIMATION: map-linear extrapolation with fixed update and audit costs. A
projection is not a measured generation duration; missing complete DPO timings
remain explicitly unmeasured. Worker CPU totals already include concurrent
teacher, candidate checks and greedy-fitness checks: never add their envelopes.
"""
from __future__ import annotations
import math
from collections.abc import Mapping


def _config(args):
    return dict(args) if isinstance(args,Mapping) else vars(args)


def _positive(value,name,*,zero=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<(0 if zero else 1e-12):
        raise ValueError(f'{name} must be finite and {"nonnegative" if zero else "positive"}')
    return float(value)


def _number(value,default=0.):
    return _positive(default if value is None else value,'phase seconds',zero=True)


def _integer(value,name,minimum=1):
    if type(value) is not int or value<minimum:raise ValueError(f'{name} must be integer >= {minimum}')
    return value


def recommend(summary,args):
    """Return proposed sizes and auditable phase costs; do not mutate args.

    Optional summary['sizing_observation'] fields:
      maps, workers, k, model_batch_calls, population_groups, decode_wall_s,
      map_cpu_worker_s (SUM over teacher + map-selection + all-greedy jobs),
      pipeline_wall_s, dataset_s, training_reverification_s, sft_steps,
      sft_child_s, dpo_completed, dpo_reference_s, dpo_update_s, audit_s,
      gold_control_trained, gold_control_s, baseline_audit_s.
    CPU map worker seconds are summed worker elapsed durations, not CPU time;
    model decode and CPU verification overlap through max(), never addition.
    """
    settings=_config(args);obs=dict(summary.get('sizing_observation',{}));timing=summary.get('timings',{})
    warnings=[]
    workers=_integer(settings.get('workers',4),'workers')
    population=_integer(settings.get('population',2),'population')
    steps=_integer(settings.get('steps',100),'steps')
    k=_integer(settings.get('k',6),'k',population)
    current_maps=_integer(settings.get('tiny',4)+settings.get('small',8),'maps',6)
    maps=_integer(obs.get('maps',summary.get('instance_count',current_maps)),'observed maps')
    target=_positive(settings.get('target_generation_minutes',75),'target minutes')*60
    measured_decode=_number(obs.get('decode_wall_s',timing.get('decode_s')))
    measured_pipeline=_number(obs.get('pipeline_wall_s',timing.get('decode_verify_pipeline_s')))
    observed_workers=_integer(obs.get('workers',workers),'observed workers')
    groups=obs.get('population_groups')
    if groups is None:
        groups=1 if summary.get('generation')==0 else population+1
        warnings.append('observed_decode_groups_inferred')
    groups=_integer(groups,'observed population groups')
    calls=obs.get('model_batch_calls')
    observed_batch_per_map=_positive(calls/maps if calls is not None else groups,'observed batches/map')
    observed_k=_integer(obs.get('k',k),'observed k',population)
    # All future children plus the elite greedy have one prompt-prefilled batch
    # per adapter/map. More samples within B may cost less than linear; linear
    # scaling below deliberately does not claim that extra batching speedup.
    batch_size=_integer(settings.get('batch_size',8),'batch_size')
    child_allocations=[k//population+(i<k%population) for i in range(population)]
    future_batches=1+sum(math.ceil(count/batch_size) for count in child_allocations)
    decode_factor=max(future_batches/observed_batch_per_map,(k+1)/(observed_k+(1 if groups>1 else 0)))
    decode_per_map=measured_decode/maps*decode_factor
    cpu_total=obs.get('map_cpu_worker_s')
    if cpu_total is not None:
        cpu_per_map=_number(cpu_total)/maps/workers
        cpu_evidence='summed teacher/selection/greedy worker elapsed seconds; each job once'
    else:
        # The observed pipeline is already an overlap envelope. Scaling its
        # worker component is conservative, without adding teacher a second time.
        cpu_per_map=measured_pipeline/maps*max(1.,observed_workers/workers)
        cpu_evidence='pipeline envelope fallback; parallel worker job totals unavailable'
        warnings.append('map_cpu_worker_totals_unmeasured')
    measured_floor=measured_pipeline/maps
    pipeline_per_map=max(decode_per_map,cpu_per_map,measured_floor)
    dataset=_number(obs.get('dataset_s',timing.get('dataset_s')))/maps
    reverify=_number(obs.get('training_reverification_s',timing.get('training_reverification_s')))/maps
    sft_observed=obs.get('sft_child_s')
    if sft_observed is None:
        sft_observed=[value for key,value in timing.items() if key.startswith('child_') and key.endswith('_train_s')]
    if isinstance(sft_observed,(int,float)):sft_observed=[sft_observed]
    observed_steps=_integer(obs.get('sft_steps',steps),'observed sft steps')
    if sft_observed:
        per_sft=max(_number(value) for value in sft_observed)*steps/observed_steps
    else:
        per_sft=_number(settings.get('unmeasured_sft_child_s',450.))
        warnings.append('sft_duration_unmeasured')
    sft=per_sft*population
    dpo_enabled=settings.get('dpo',True)
    dpo_complete=bool(obs.get('dpo_completed',False))
    dpo_reference=_number(obs.get('dpo_reference_s'))
    dpo_update=_number(obs.get('dpo_update_s'))
    if dpo_enabled and not dpo_complete:
        # A failed first checkpoint is not a complete update timing.
        dpo=max(_number(settings.get('unmeasured_dpo_s',900.)),dpo_reference+dpo_update)
        warnings.append('DPO_full_update_unmeasured')
    else:dpo=(dpo_reference+dpo_update) if dpo_enabled else 0.
    audit_due=bool(settings.get('sizing_include_audit',True))
    audit=_number(obs.get('audit_s',timing.get('audit_s',settings.get('unmeasured_audit_s',750.)))) if audit_due else 0.
    control_due=bool(settings.get('sizing_include_control',audit_due))
    observed_control=obs.get('gold_control_s',timing.get('gold_control_s'))
    control=0.
    if control_due:
        if obs.get('gold_control_trained') is True:control=_number(observed_control)
        else:
            # Existing zero-phase "control" only measures its audit. Add a real
            # SFT phase and dataset cost; don't call it measured control training.
            control=max(audit,_number(observed_control))+per_sft+dataset*maps
            warnings.append('trained_control_duration_projected')
    baseline=_number(obs.get('baseline_audit_s',settings.get('baseline_audit_s',0.)))
    margin=_positive(settings.get('sizing_safety_factor',1.15),'safety factor')
    if margin<1:raise ValueError('safety factor cannot shrink a timing projection')
    fixed=sft+dpo+audit+control
    per_map=pipeline_per_map+dataset+reverify
    max_maps=math.floor((target/margin-fixed)/per_map) if per_map else current_maps
    # Six maps preserve tiny/small and approximately half-gold quotas. If even
    # six do not fit, report that explicitly; never pretend the target is met.
    selected=max(6,min(current_maps,max_maps))
    tiny=max(2,selected//3);small=selected-tiny
    projected=margin*(fixed+per_map*selected)
    ready=bool(measured_decode and measured_pipeline and sft_observed and (not dpo_enabled or dpo_complete))
    return dict(schema='exit-d137-sizing-v1',maps=selected,tiny=tiny,small=small,k=k,workers=workers,
        population=population,steps=steps,target_s=target,projected_generation_s=projected,
        projected_generations_per_hour=3600/projected if projected else None,
        projection_only=True,fully_measured_inputs=ready,
        target_projected_met=projected<=target,baseline_startup_s=baseline,
        baseline_included_in_recurring_generation=False,
        fixed_s=dict(sft_children=sft,dpo_reference_and_update=dpo,audit=audit,gold_control=control),
        per_map_s=dict(overlapped_decode_verify=pipeline_per_map,dataset=dataset,training_reverification=reverify),
        pipeline=dict(decode_per_map_s=decode_per_map,verification_worker_per_map_s=cpu_per_map,
            measured_envelope_per_map_s=measured_floor,combination='max(decode, pooled CPU work, measured pipeline envelope)',
            cpu_evidence=cpu_evidence,observed_batches_per_map=observed_batch_per_map,
            future_batches_per_map=future_batches,decode_scaling_factor=decode_factor),
        dpo_timing_status='measured_complete' if dpo_complete else 'unmeasured' if dpo_enabled else 'disabled',
        safety_factor=margin,warnings=sorted(set(warnings)),
        approximation='map-linear projection with fixed child updates/full due audits; source-epoch recalibration required',
        teacher_probe=dict(tiny_total_s=45.22161866599345,small_total_s=[56.77173949999269,62.68090070801554],
            source_manifest_sha256='e906456175244f9210ec0c7c5d294eccdd6bd13d5164029fbf236b2895bf6266',
            extra_added_s=0,reason='teacher already belongs to pooled map work; probe is calibration evidence, not an additive envelope'))
