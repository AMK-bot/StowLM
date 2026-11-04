from pathlib import Path
h=Path(__file__).resolve().parent;p=h/'runner.py';s=p.read_text().replace("reference_cache_path=directory/'dpo-reference.json'", "reference_cache_path=child/'dpo-reference.json'")
p.write_text(s)
p=h/'prepare_validation.py';s=p.read_text();needle="p.write_text(s)\np=V/'tests/llm/exit/test_audit.py'"
extra='''# Exercise two preference children (P=3 after a preference-method winner).
s=s.replace("'generation,rollback,dpo_mode,dpo_failure'", "'generation,rollback,dpo_mode,dpo_failure,population_count'")
s=s.replace("(0,False,False,False),(3,True,False,False),(0,False,True,False),(0,False,True,True)", "(0,False,False,False,2),(3,True,False,False,2),(0,False,True,False,2),(0,False,True,True,2),(0,False,True,False,3)")
s=s.replace("generation, rollback,dpo_mode,dpo_failure):", "generation, rollback,dpo_mode,dpo_failure,population_count):")
s=s.replace("    args.dpo=dpo_mode", "    args.dpo=dpo_mode\\n    args.population=population_count")
s=s.replace("                  for name in ('child-a', 'child-b')]", "                  for name in ('child-a', 'child-b')]\\n    if population_count==3:\\n        for config in population:config['method']='sft_dpo'")
s=s.replace("preference_calls.append((parent,child,out))", "preference_calls.append((parent,child,out,kwargs['reference_cache_path']))")
s=s.replace("[str(tmp_path/'child-b')]*2)", "[str(tmp_path/'child-b')]*population_count)")
s=s.replace("(1 if rollback else 3)", "(1 if rollback else population_count+1)")
s=s.replace("        assert len(preference_calls)==1", "        assert len(preference_calls)==(2 if population_count==3 else 1)\\n        assert len({str(call[3]) for call in preference_calls})==len(preference_calls)")
'''
s=s.replace(needle,extra+needle)
p.write_text(s)
p=h/'assemble.py';s=p.read_text().replace("if (HERE/'sizing.py').exists():replacements['sizing.py']=HERE/'sizing.py'", "if (HERE/'sizing.py').exists():replacements['sizing.py']=HERE/'sizing.py'\nif (HERE/'final_recipe.py').exists():replacements['final_recipe.py']=HERE/'final_recipe.py'")
p.write_text(s)
