"""Independent raw witness, replay coverage and checker-binding final audit."""
import ast
import hashlib
import json
from pathlib import Path
from time import monotonic

from audit import verify_row, verify_witness, load_raw
from run import dump, inputs
from stowage.checker.full import report_matches_inputs
from stowage.io import load_instance
from stowage.model import CheckReport, MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent


def main():
    started = monotonic()
    result = dict(outer_witnesses=[], replays=[], source_syntax=[])
    for path in inputs():
        raw,digest = load_raw(path)
        name = raw['id']+'.json'
        outer = json.loads((HERE/'results-rod'/name).read_text())
        checked = verify_row(outer)
        assert checked['verified'],checked
        target = verify_witness(raw,outer['page_targets'])
        assert target['verified'],target
        checked['target_verified'] = True
        result['outer_witnesses'].append(checked)
        row = json.loads((HERE/'results-final'/name).read_text())
        if 'page_followup' not in row:
            continue
        f = row['page_followup']
        instance = load_instance(path)
        plan = MovePlan.model_validate(f['plan'])
        replay = apply_plan(instance,plan)
        loaded = {p.container_id for p in replay.final_state.ship.placements}
        report = CheckReport.model_validate(f['full_checker_report'])
        bound = report_matches_inputs(report,instance,plan)
        checked = dict(instance_id=instance.id,replay_legal=replay.legal,
            exact_load_list=set(instance.load_list)==loaded,
            loads=replay.count.loads,expected_loads=len(instance.load_list),
            full_checker_report_matches_inputs=bound,full_checker_passed=report.passed,
            plan_sha256=hashlib.sha256(plan.model_dump_json().encode()).hexdigest())
        assert checked['replay_legal'] and checked['exact_load_list'] and bound,checked
        assert checked['loads']==checked['expected_loads'] and not report.passed,checked
        result['replays'].append(checked)
    for p in HERE.glob('*.py'):
        ast.parse(p.read_text(),filename=str(p))
        result['source_syntax'].append(p.name)
    result['elapsed_s'] = monotonic()-started
    dump(HERE/'verification.json',result)
    print(json.dumps(dict(witnesses_verified=len(result['outer_witnesses']),
                          complete_replays_verified=len(result['replays']),
                          elapsed_s=result['elapsed_s'])))


if __name__=='__main__':
    main()
