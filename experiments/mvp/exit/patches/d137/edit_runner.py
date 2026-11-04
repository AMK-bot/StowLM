from pathlib import Path
p=Path(__file__).with_name('runner.py')
s=p.read_text()
s=s.replace("seen_adapter_hashes=adapter_training_hashes([row['adapter'] for row in population])))", "seen_adapter_hashes=adapter_training_hashes([row['adapter'] for row in population]),\n                     gold_fraction=getattr(args,'gold_map_fraction',.5)))")
s=s.replace("for e in schedule.state['generations'].get(key,[])}))", "for e in schedule.state['generations'].get(key,[])},\n            map_strata={e['path']:e.get('map_stratum') for e in schedule.state['generations'].get(key,[])},\n            teacher_gold_evidence={e['path']:e.get('teacher_gold_evidence')\n                for e in schedule.state['generations'].get(key,[])}))")
s=s.replace("        # APPROXIMATION: only currently reverified gold seeds the online bound.", "        # Teachers start on CPU before GPU decode, including maps with stored gold.\n        teacher_jobs=[pool.submit(teacher_job,str(path),str(args.corpus),str(args.gold),\n            str(directory/'cases'/f'{i:03d}'/'teachers'),args.teacher_planning_s,args.ballast_s)\n            for i,path in enumerate(paths)]\n        # APPROXIMATION: only currently reverified gold seeds the online bound.")
s=s.replace("for job in incumbent_jobs+jobs:job.cancel()", "for job in teacher_jobs+incumbent_jobs+jobs:job.cancel()")
s=s.replace("                    extra=([all_samples[index],args.dpo_pairs_per_map] if args.dpo else [])\n", "                    # A complete current-epoch teacher proof is required before selection.\n                    # CPU jobs were queued first and overlap adapter-major decoding.\n                    teacher_manifest=teacher_jobs[index].result()\n")
s=s.replace("polish_s=args.polish_s, ballast_s=args.ballast_s),\n                        [row['adapter'] for row in population],*extra))", "polish_s=args.polish_s, ballast_s=args.ballast_s,verify_all=True),\n                        [row['adapter'] for row in population],all_samples[index],\n                        args.dpo_pairs_per_map if args.dpo else 0,teacher_manifest))")
s=s.replace("pruning_incumbent_worker_s=sum(s['wall_s'] for s in incumbent_results))", "pruning_incumbent_worker_s=sum(s['wall_s'] for s in incumbent_results),\n        teacher_worker_s=sum(s['timings'].get('teacher_worker_s',0.) for s in selections),\n        teacher_planning_worker_s=sum((r.get('planning') or {}).get('planning_wall_s',\n            r.get('planning_wall_s',0.)) for s in selections for r in s.get('teachers',[])))\n    teacher_report=teacher_summary(selections)\n    write_json(directory/'teacher-coverage.json',teacher_report)\n    # Never silently return to self-imitation if the teacher pipeline is broken.\n    if not teacher_report['verified_teacher_maps'] or (\n            getattr(args,'gold_map_fraction',.5)>0 and not teacher_report['gold_passes']):\n        raise RuntimeError('D137 teacher coverage gate failed; saved per-map diagnostics, no child trained')")
s=s.replace("audit_paths=validation)\n            timings['gold_control_s']", "audit_paths=validation,fitness=fitness)\n            timings['gold_control_s']")
s=s.replace("phase_count=control_report['phase_count'],schedule_matched=control_report['schedule_matched'],", "phase_count=control_report['phase_count'],schedule_matched=control_report['schedule_matched'],\n                schedule_reference_adapter=control_report['schedule_reference_adapter'],\n                compared_adapter_schedule_matched=control_report['compared_adapter_schedule_matched'],\n                trained_control=control_report['trained_control'],")
s=s.replace("winners_by_source=dict(Counter(s['source'] for s in selections if s['passed'])),", "teachers=teacher_report,\n        winners_by_source=dict(Counter(s['source'] for s in selections if s['passed'])),")
s=s.replace("    ap.add_argument('--ballast-s', type=float, default=90.)", "    ap.add_argument('--gold-map-fraction',type=float,default=.5)\n    ap.add_argument('--teacher-planning-s',type=float,default=120.)\n    ap.add_argument('--baseline-label',default='8.1')\n    ap.add_argument('--ballast-s', type=float, default=float(os.environ.get('FINALIZE_S','120')))")
s=s.replace("    if args.cached_activations and not args.cpu_dry_run:", "    if not 0<=args.gold_map_fraction<=1 or args.teacher_planning_s<=0 or args.ballast_s<=0:\n        raise SystemExit('gold fraction must be in [0,1]; teacher/finisher budgets positive')\n    if args.cached_activations and not args.cpu_dry_run:")
# Every audit call receives the explicit current restart baseline label.
s=s.replace("frozen_audit_paths(ROOT,args.corpus,args.audit_list,args.out)","frozen_audit_paths(ROOT,args.corpus,args.audit_list,args.out,baseline_label=getattr(args,'baseline_label','8.1'))")
s=s.replace("audit_decision(baseline,best_rows)","audit_decision(baseline,best_rows,baseline_label=getattr(args,'baseline_label','8.1'))")
s=s.replace("audit_decision(baseline, audited_rows)","audit_decision(baseline, audited_rows,baseline_label=getattr(args,'baseline_label','8.1'))")
# Pure report helper makes unavailable teacher attempts explicit, not physics failures.
insert='''def teacher_summary(selections):
    rows=[r for s in selections for r in s.get('teachers',[])]
    return dict(map_count=len(selections),
        gold_passes=sum(r.get('passed') is True and r['source']=='gold' for r in rows),
        two_step_passes=sum(r.get('passed') is True and r['source']=='baseline_twostep' for r in rows),
        two_step_attempts=sum(r.get('attempted') is True and r['source']=='baseline_twostep' for r in rows),
        verified_teacher_maps=sum(any(r.get('passed') is True for r in s.get('teachers',[])) for s in selections),
        unavailable=sum(bool(r.get('unavailable')) for r in rows),
        per_map=[dict(instance_id=s['instance_id'],teachers=s.get('teachers',[]),
            winner=s.get('source'),winner_moves=s.get('weighted_moves')) for s in selections])


'''
s=s.replace('def teacher_job(',insert+'def teacher_job(',1)
p.write_text(s)
