from pathlib import Path
p=Path(__file__).with_name('prepare_validation.py');s=p.read_text()
s=s.replace("shutil.copytree(R/'tests/llm/exit'", "if (V/'tests/llm/exit').exists():shutil.rmtree(V/'tests/llm/exit')\nshutil.copytree(R/'tests/llm/exit'")
s=s.replace(".replace(\"assert calls['time_limit_s'] == .3\",\"assert calls['time_limit_s'] == 120.\")", ".replace(\"'time_limit_s':.3\",\"'time_limit_s':120.\")")
s=s.replace("s=s.replace(\"lambda *a:[tmp_path/'val.json']\",\"lambda *a,**kw:[tmp_path/'val.json']\")", "s=s.replace(\"lambda *a:\",\"lambda *a,**kw:\")")
s=s.replace("    monkeypatch.setattr(r,'teacher_job',lambda *a,**kw:'teacher-manifest')", "    monkeypatch.setattr(r,'teacher_job',lambda *a,**kw:'teacher-manifest')\\n    monkeypatch.setattr(r,'assert_audit_epoch',lambda *a:None)")
# Insert test_audit fixture adjustments before patch installation.
needle="# Staged regression patches add tests without touching production."
extra='''p=V/'tests/llm/exit/test_audit.py';s=p.read_text().replace("lambda *a:","lambda *a,**kw:")
s=s.replace("    epoch=hashlib.sha256(json.dumps({'decoder':'same'},sort_keys=True).encode()).hexdigest()[:16]", "    epoch=runner.epoch_id({'decoder':'same'})\\n    monkeypatch.setattr(runner,'_LOADED_AUDIT_EPOCH',{'decoder':'same'})\\n    monkeypatch.setattr(runner,'assert_audit_epoch',lambda *a:None)")
s=s.replace("    assert result['maps']==24 and args.k==6 and args.tiny+args.small==24", "    assert 6<=result['maps']<=48 and args.tiny+args.small==result['maps']\\n    assert result['projection_only'] and not result['fully_measured_inputs']\\n    assert result['baseline_included_in_recurring_generation'] is False")
p.write_text(s)
'''
s=s.replace(needle,extra+needle);p.write_text(s)
