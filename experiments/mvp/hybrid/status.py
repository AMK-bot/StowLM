"""Read-only admission and measurement progress, with quarantines excluded."""
from datetime import datetime,timezone
import fcntl
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
out = Path(sys.argv[1]) if len(sys.argv)>1 else HERE/'runs/val-d205'
quarantines = [str(p) for p in (HERE/'runs').glob('*') if (p/'QUARANTINED.json').exists() or (p/'ABORTED.json').exists()]
result = dict(run=str(out),excluded_cohorts=quarantines)
if not (out/'manifest.json').exists():
    result.update(state='not launched',completed=0,total=120)
else:
    manifest = json.loads((out/'manifest.json').read_text())
    events = [json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()] if (out/'events.jsonl').exists() else []
    rows = [json.loads(p.read_text()) for arm in ('none','llm','random') for p in (out/arm).glob('*.json')
            if not p.name.endswith('.audit.json')]
    complete = [r for r in rows if r.get('completed')]
    probe = next((e for e in reversed(events) if e['event']=='admission_probe'),None)
    try:
        lock = (out/'RUN.LOCK').open('r')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        alive = False
        fcntl.flock(lock,fcntl.LOCK_UN)
    except BlockingIOError:
        alive = True
    except FileNotFoundError:
        alive = False
    result.update(cohort_id=manifest.get('cohort_id'),workers=manifest.get('workers'),controller_alive=alive,
                  completed=len(complete),total=manifest['n_maps']*3,admission=probe,done=(out/'DONE.json').exists(),
                  state='quarantined' if str(out) in quarantines else 'stopped' if (out/'STOP').exists() else
                        'armed; held for skill-check Part 2' if (out/'HOLD').exists() else 'prepared; no controller' if not alive else
                        'waiting admission' if probe and not probe.get('allowed') else 'running',
                  errors=[r.get('error') for r in complete if r.get('error')])
    if complete:
        result['eta_remaining_minutes'] = round(sum(r['wall_s'] for r in complete)/len(complete)*(manifest['n_maps']*3-len(complete))/4/60)
print(json.dumps(result,sort_keys=True))
