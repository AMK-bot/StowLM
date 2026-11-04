"""Growth with box count, from scale_rows.json + each run's progress.jsonl: tokens per box, seconds per token, memory.
Power-law fits are log-log least squares over per-map points; projections past measured data are labelled in SCALE.md."""
import json
from collections import defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent
rows = json.loads((HERE/'scale_rows.json').read_text())
ends = {}
for run in sorted((HERE/'runs').glob('*/progress.jsonl')):
    events = [json.loads(l) for l in run.open()]
    done = [e for e in events if e['event'] == 'decode_end']
    ends[run.parent.name] = done
by_id = {}
for name, done in ends.items():
    ids = sorted(json.loads(p.read_text())['instance_id'] for p in (HERE/'runs'/name/'instances').glob('*.json'))
    records = sorted(((json.loads(p.read_text())['input'], json.loads(p.read_text())['instance_id'])
                      for p in (HERE/'runs'/name/'instances').glob('*.json')))
    config = json.loads((HERE/'runs'/name/'config.json').read_text())
    order = [i['path'] for i in config['inputs']]
    records.sort(key=lambda r: order.index(r[0]))
    for (path, iid), e in zip(records, done):
        by_id[iid] = e
pts = []
for r in rows:
    e = by_id[r['id']]
    pts.append(dict(id=r['id'], rung=r['rung'], boxes=r['boxes'], yard_steps=e['steps'], decode_s=e['elapsed_s'], tokens=e['tokens'],
                    s_per_step=e['elapsed_s']/e['steps'], steps_per_box=e['steps']/r['boxes'], actions_per_box=r['actions']/r['touched'],
                    peak_gb=r['peak_gb'], rss=e['rss_max_gb'], tokens_in=r['tokens_in'], pipeline_s=r['pipeline_s']))
rung = defaultdict(list)
for p in pts:
    rung[p['rung']].append(p)
out = {}
for k, v in rung.items():
    out[k] = {m: round(float(np.mean([p[m] for p in v])), 3) for m in ('boxes', 'decode_s', 'tokens', 's_per_step', 'actions_per_box', 'peak_gb', 'pipeline_s')}
    out[k]['n'] = len(v)
x = np.log([p['boxes'] for p in pts])
fits = {}
for m in ('s_per_step', 'decode_s', 'tokens', 'peak_gb'):
    b, a = np.polyfit(x, np.log([p[m] for p in pts]), 1)
    fits[m] = dict(exponent=round(float(b), 3), coefficient=round(float(np.exp(a)), 5))
small = [p for p in pts if p['rung'] in ('tiny', 'small')]
print(json.dumps(dict(by_rung=out, fits=fits, points=pts), indent=1))
(HERE/'scale_fit.json').write_text(json.dumps(dict(by_rung=out, fits=fits, points=pts), indent=1))
