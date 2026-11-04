from pathlib import Path
p=Path(__file__).with_name('runner.py');s=p.read_text()
s=s.replace("    from llm.exit.selection import prepare_teacher_candidates\n", "    from llm.exit.selection import prepare_teacher_candidates,verify_candidate\n",1)
s=s.replace("load_gold_candidate(input_path,corpus_root,gold_root),planning_s=planning_s,ballast_s=ballast_s)","load_gold_candidate(input_path,corpus_root,gold_root),planning_s=planning_s,ballast_s=ballast_s,\n        verifier=lambda i,c:verify_candidate(i,c,ballast_s=ballast_s,\n            expected_outcome_epoch=_LOADED_AUDIT_EPOCH))",1)
s=s.replace("    gold_scores = [c.metadata['weighted_moves'] for c in result.candidates\n                   if c.source == 'gold' and 'weighted_moves' in c.metadata]", "    gold_scores = [v['weighted_moves'] for v in result.verifications\n                   if v.get('source')=='gold' and v.get('passed') is True\n                   and v.get('weighted_moves') is not None]")
s=s.replace("    binding_path=target.with_name('baseline-binding.json')", "    if getattr(args,'baseline_label','8.1')!='8.1':\n        binding['baseline_label']=args.baseline_label\n    binding_path=target.with_name('baseline-binding.json')")
s=s.replace("row-d: greedy-first best-of-4 T0.7/backtrack32", "9.1: greedy single sequential decode, securing+lookahead, rewinds32, V4 finisher")
p.write_text(s)
