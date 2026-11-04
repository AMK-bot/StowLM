from pathlib import Path
p=Path(__file__).with_name('runner.py');s=p.read_text()
s=s.replace("attribution_eligible=bool(control_report['example_count_matched']\n                    and not control_report['dpo_updates_unmatched'])", "attribution_eligible=bool(control_report['example_count_matched']\n                    and control_report['trained_control'] and control_report['schedule_matched']\n                    and control_report['compared_adapter_schedule_matched']\n                    and not control_report['dpo_updates_unmatched'])")
s=s.replace("get('reference_wall_s',0.)", "get('reference_prepass_s',0.)")
s=s.replace("dpo_update_s=sum(v for k,v in timings.items() if k.endswith('_dpo_s')),", "dpo_update_s=max(0.,sum(v for k,v in timings.items() if k.endswith('_dpo_s'))-sum(\n            c.get('preference_training',{}).get('reference_prepass_s',0.) for c in children)),")
p.write_text(s)
p=Path(__file__).with_name('assemble.py');s=p.read_text()
s=s.replace("before=before,prerequisites=", "before=before,prerequisites=")
# Bind all prerequisite bytes as well as D137 differences, without putting prerequisites into our code diff.
s=s.replace("(HERE/'installed-manifest.json').write_text", "for p in (base/'src/llm/exit').glob('*.py'):\n    rel='src/llm/exit/'+p.name\n    targets.setdefault(rel,hashlib.sha256((OUT/rel).read_bytes()).hexdigest())\n(HERE/'installed-manifest.json').write_text")
p.write_text(s)
