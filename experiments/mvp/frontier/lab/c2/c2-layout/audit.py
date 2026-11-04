"""Fresh-process full checker audit of the selected measured portfolio."""
from pathlib import Path
import argparse
import json
from time import monotonic

from stowage.checker import check
from stowage.io import load_instance
from stowage.model.plan import MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--comparison', default=str(HERE/'comparison.json'))
    ap.add_argument('--out', default=str(HERE/'audit.json'))
    args = ap.parse_args()
    rows = []
    for chosen in json.loads(Path(args.comparison).read_text())['maps']:
        if not chosen['passed']:
            continue
        started = monotonic()
        row = json.loads(Path(chosen['evidence']).read_text())
        instance = load_instance(row['input'])
        plan = MovePlan.model_validate(row['plan'])
        replay = apply_plan(instance, plan)
        report = check(instance, plan)
        result = dict(instance_id=instance.id, evidence=chosen['evidence'],
                      replay_legal=replay.legal, passed=report.passed,
                      loads=report.count.loads, boxes=len(instance.load_list),
                      lifts=report.count.weighted_nonproductive,
                      violations=len(report.violations),
                      wall_s=monotonic()-started)
        assert result['replay_legal'] and result['passed']
        assert result['loads'] == result['boxes']
        assert result['lifts'] == row['lifts']
        assert result['lifts'] >= chosen['lower_bound']
        print(json.dumps(result), flush=True)
        rows.append(result)
        Path(args.out).write_text(json.dumps(rows, indent=2)+'\n')


if __name__ == '__main__':
    main()
