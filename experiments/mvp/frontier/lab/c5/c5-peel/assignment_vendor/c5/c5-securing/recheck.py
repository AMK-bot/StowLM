"""Independent check of saved passing plans in a fresh process. Imports only the unchanged checker and replay."""
import hashlib
import json
from pathlib import Path
import sys

from stowage.checker import check
from stowage.model import Instance, MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
out = []
for path in sorted((HERE/sys.argv[1]).glob('*.json')):
    row = json.loads(path.read_text())
    if not row.get('passed'):
        continue
    raw = (ROOT/row['input']).read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == row['input_sha256']
    instance = Instance.model_validate_json(raw)
    plan = MovePlan.model_validate(row['plan'])
    replay = apply_plan(instance, plan)
    report = check(instance, plan)
    aboard = {p.container_id for p in replay.final_state.ship.placements}
    item = dict(instance=row['instance_id'], replay_legal=replay.legal, passed=report.passed, violations=len(report.violations),
                lifts=float(report.count.weighted_nonproductive), recorded=row['lifts'],
                all_loaded=set(instance.load_list) <= aboard, securing_margin=min(v for k, v in report.parts['lashing'].margins.items()))
    out.append(item)
    print(item, flush=True)
    (HERE/(sys.argv[2])).write_text(json.dumps(out, indent=1)+'\n')
