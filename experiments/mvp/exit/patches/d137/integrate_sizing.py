from pathlib import Path
p=Path(__file__).with_name('runner.py');s=p.read_text();start=s.index('def resize_after_pilot(');end=s.index('\n\ndef recover_generation',start)
s=s[:start]+'''def resize_after_pilot(summary,args):
    """D137 map-linear projection; unfinished DPO timings remain unmeasured."""
    from llm.exit.sizing import recommend
    result=recommend(summary,args)
    args.tiny,args.small,args.k=result['tiny'],result['small'],result['k']
    write_json(args.out/'sizing-after-gen0.json',result)
    return result
'''+s[end:]
s=s.replace("            from llm.exit.dataset_pool import choose_workers\n            from llm.exit.dataset_pool import choose_workers", "            from llm.exit.dataset_pool import choose_workers")
s=s.replace("    append_learning_curve(args.out / 'generation_times.csv', dict(generation=number,",'''    # Teacher elapsed work and selection elapsed work are disjoint jobs; their
    # sum is pooled CPU demand. Never add that to the overlapping decode wall.
    summary['sizing_observation']=dict(maps=len(paths),workers=args.workers,k=args.k,
        population_groups=len(population),model_batch_calls=decoded_calls,
        decode_wall_s=decode_wall_s,pipeline_wall_s=timings['decode_verify_pipeline_s'],
        map_cpu_worker_s=timings['teacher_worker_s']+sum(
            max(0.,s.get('verification_finished_monotonic',0.)-s.get('verification_started_monotonic',0.))
            for s in original_selections),
        dataset_s=timings.get('dataset_s',0.),
        training_reverification_s=timings.get('training_reverification_s',0.),
        sft_steps=args.steps,sft_child_s=[v for k,v in timings.items() if k.endswith('_train_s')],
        dpo_completed=any(c.get('preference_training') for c in children),
        dpo_reference_s=sum(c.get('preference_training',{}).get('reference_wall_s',0.) for c in children),
        # Complete phase elapsed time includes its reference prepass. Record it
        # exactly once, conservatively under update if the internal split is absent.
        dpo_update_s=sum(v for k,v in timings.items() if k.endswith('_dpo_s')),
        audit_s=timings.get('audit_s',0.),
        gold_control_trained=bool(audit and audit.get('gold_control',{}).get('trained_control')),
        gold_control_s=timings.get('gold_control_s'),
        baseline_audit_s=read_json(args.out/'baseline-timing.json').get('audit_s',0.)
            if (args.out/'baseline-timing.json').exists() else 0.)
    append_learning_curve(args.out / 'generation_times.csv', dict(generation=number,''')
p.write_text(s)
