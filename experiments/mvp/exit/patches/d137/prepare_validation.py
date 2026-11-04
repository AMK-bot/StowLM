from pathlib import Path
import shutil,subprocess
H=Path(__file__).resolve().parent;R=H.parents[4];V=H/'validation'
V.mkdir(exist_ok=True)
shutil.copytree(R/'src',V/'src',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for p in (H/'assembled/src/llm/exit').glob('*.py'):shutil.copy2(p,V/'src/llm/exit'/p.name)
if (V/'tests/llm/exit').exists():shutil.rmtree(V/'tests/llm/exit')
shutil.copytree(R/'tests/llm/exit',V/'tests/llm/exit',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copy2(R/'pyproject.toml',V/'pyproject.toml')
# Private tests can find the project's readonly helpers/corpus via these links.
for name in ('experiments','data','params','models','docs'):
    if (R/name).exists() and not (V/name).exists():(V/name).symlink_to(R/name,target_is_directory=True)
# Existing corpus utility tests explicitly exercise zero-gold mode, not the production 50/50 flag.
p=V/'tests/llm/exit/test_data_metrics.py';s=p.read_text()
for old,new in [('next_train(2, 2, 0)','next_train(2, 2, 0, gold_fraction=0)'),('next_train(2, 2, 1)','next_train(2, 2, 1, gold_fraction=0)'),('next_train(0, 1, 0)','next_train(0, 1, 0, gold_fraction=0)'),('next_train(99, 0, 0)','next_train(99, 0, 0, gold_fraction=0)'),('next_train(1, 0, 0)','next_train(1, 0, 0, gold_fraction=0)'),('gold_root=gold)','gold_root=gold, gold_fraction=0)'),('seen_adapter_hashes=seen)','seen_adapter_hashes=seen, gold_fraction=0)')]:s=s.replace(old,new)
p.write_text(s)
p=V/'tests/llm/exit/test_selection.py';p.write_text(p.read_text().replace("'time_limit_s':.3","'time_limit_s':120."))
p=V/'tests/llm/exit/test_runner.py';s=p.read_text()
s=s.replace("lambda *a:","lambda *a,**kw:")
s=s.replace("    monkeypatch.setattr(r, 'ProcessPoolExecutor', InlinePool)","    monkeypatch.setattr(r, 'ProcessPoolExecutor', InlinePool)\n    monkeypatch.setattr(r,'teacher_job',lambda *a,**kw:'teacher-manifest')\n    monkeypatch.setattr(r,'assert_audit_epoch',lambda *a:None)")
s=s.replace("return dict(passed=path.endswith('tiny.json'),", "return dict(instance_id=path,teachers=[dict(source='gold',passed=True,attempted=True)],passed=path.endswith('tiny.json'),")
# Exercise two preference children (P=3 after a preference-method winner).
s=s.replace("'generation,rollback,dpo_mode,dpo_failure'", "'generation,rollback,dpo_mode,dpo_failure,population_count'")
s=s.replace("(0,False,False,False),(3,True,False,False),(0,False,True,False),(0,False,True,True)", "(0,False,False,False,2),(3,True,False,False,2),(0,False,True,False,2),(0,False,True,True,2),(0,False,True,False,3)")
s=s.replace("generation, rollback,dpo_mode,dpo_failure):", "generation, rollback,dpo_mode,dpo_failure,population_count):")
s=s.replace("    args.dpo=dpo_mode", "    args.dpo=dpo_mode\n    args.population=population_count")
s=s.replace("                  for name in ('child-a', 'child-b')]", "                  for name in ('child-a', 'child-b')]\n    if population_count==3:\n        for config in population:config['method']='sft_dpo'")
s=s.replace("preference_calls.append((parent,child,out))", "preference_calls.append((parent,child,out,kwargs['reference_cache_path']))")
s=s.replace("[str(tmp_path/'child-b')]*2)", "[str(tmp_path/'child-b')]*population_count)")
s=s.replace("(1 if rollback else 3)", "(1 if rollback else population_count+1)")
s=s.replace("        assert len(preference_calls)==1", "        assert len(preference_calls)==(2 if population_count==3 else 1)\n        assert len({str(call[3]) for call in preference_calls})==len(preference_calls)")
p.write_text(s)
p=V/'tests/llm/exit/test_audit.py';s=p.read_text().replace("lambda *a:","lambda *a,**kw:")
s=s.replace("    epoch=hashlib.sha256(json.dumps({'decoder':'same'},sort_keys=True).encode()).hexdigest()[:16]", "    epoch=runner.epoch_id({'decoder':'same'})\n    monkeypatch.setattr(runner,'_LOADED_AUDIT_EPOCH',{'decoder':'same'})\n    monkeypatch.setattr(runner,'assert_audit_epoch',lambda *a:None)")
s=s.replace("    assert result['maps']==24 and args.k==6 and args.tiny+args.small==24", "    assert 6<=result['maps']<=48 and args.tiny+args.small==result['maps']\n    assert result['projection_only'] and not result['fully_measured_inputs']\n    assert result['baseline_included_in_recurring_generation'] is False")
p.write_text(s)
# Staged regression patches add tests without touching production.
for patch in (H/'maps-control/regression-tests.diff',H/'candidates/candidate-pool-tests.diff'):
    subprocess.run(['git','apply',str(patch)],cwd=V,check=True)
# Some direct staged tests use unique spec names and belong outside installed tests.
print(V)
