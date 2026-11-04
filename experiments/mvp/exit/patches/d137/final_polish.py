from pathlib import Path
p=Path(__file__).with_name('runner.py');s=p.read_text().replace("baseline=baseline_candidate(instance,time_limit_s=.3)","baseline=baseline_candidate(instance,time_limit_s=120.)")
p.write_text(s)
