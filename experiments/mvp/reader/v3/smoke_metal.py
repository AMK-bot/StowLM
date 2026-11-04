"""D224 Metal smoke of v3's schema-and-context mask: real Glimmer + stopped adapter-v1 on two validation rows through evaluate.run_glimmer, the same path as final scoring.
No sealed row is read. Usage: python -m experiments.mvp.reader.v3.smoke_metal"""
import json
from pathlib import Path
import time

from experiments.mvp.reader.v3 import evaluate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

rows = [json.loads(line) for line in (HERE/'data-domain/valid.jsonl').read_text().splitlines()]
picked = [rows[0], rows[-1]]
started = time.monotonic()
outputs = evaluate.run_glimmer(picked, ROOT/'models/glimmer-30b-4bit', HERE.parent/'adapter-v1')
report = dict(seconds=round(time.monotonic()-started, 1), outputs=[dict(id=o.get('id'), text=str(o.get('prediction'))[:160]) for o in outputs])
for o in outputs:
    json.loads(o['prediction']) if isinstance(o['prediction'], str) else o['prediction']
report['all_json'] = True
(HERE/'smoke-metal.json').write_text(json.dumps(report, indent=1)+'\n')
print(json.dumps(report))
