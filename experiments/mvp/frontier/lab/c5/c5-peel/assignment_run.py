"""One fresh-process assignment-ladder row, or an independent checker audit."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path.insert(0, str(HERE))
from runtime import load_engine
load_engine()
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.checker import check
from tiers import analyze_report
from assignment_method import solve, normal

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, default=str)+'\n')
    temporary.replace(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--paired', action='store_true')
    args = parser.parse_args()
    if args.verify:
        old = json.loads(args.verify.read_text())
        instance = load_instance(ROOT/old['input'])
        report = check(instance, MovePlan.model_validate(old['plan']))
        analysis = analyze_report(report, instance=instance)
        matching = (analysis['level'] == old['level'] and
            analysis['broken_rule_ids'] == sorted(r['rule_id'] for r in old['rules_broken']) and
            report.model_dump(mode='json')['violations'] == old['checker_report']['violations'] and
            float(report.count.weighted_nonproductive) == float(old['lifts']) and
            analysis['boxes_left_ashore'] == old['boxes_left_ashore'])
        row = dict(input=old['input'], instance_id=instance.id, level=analysis['level'],
            matching=matching, rules_broken=analysis['broken_rules'],
            checker_report=report.model_dump(mode='json'), fresh_pid=os.getpid())
        write(args.out, row)
        if not matching:
            raise SystemExit('Fresh checker evidence mismatch')
        return
    relative = args.input
    if not relative or any(part in relative for part in ('valsplit', 'testv3', 'small-v21', 'test-0.9', 'unseen15-0.9')):
        raise ValueError('Missing or forbidden input path')
    instance = load_instance(ROOT/relative)
    print('START', instance.id, 'pid', os.getpid(), flush=True)
    started = time.monotonic()
    try:
        if args.paired:
            reference, metadata = normal(instance)
            result, row = solve(instance, normal_call=lambda *args, **kwargs: (reference, metadata))
            row.update(reference_passed=reference is not None,
                reference_plan=reference.plan.model_dump(mode='json') if reference else None,
                L0_result_identity=(result is reference) if reference else None,
                L0_plan_unchanged=(result.plan.model_dump(mode='json') == reference.plan.model_dump(mode='json')) if reference else None,
                comparison='same-run ordinary method through assignment ladder L0 gate')
        else:
            result, row = solve(instance)
    except Exception as error:
        row = dict(level=None, passed=False, completed=None, best_safe_partial=None,
            completion_status='not_completed', status='method_error',
            error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    manifest = HERE/'assignment-frozen-hashes.json'
    row.update(input=relative, instance_id=instance.id, seed=0,
        wall_s=time.monotonic()-started, fresh_pid=os.getpid(),
        experiment='assignment LP ladder', peel_contract='D242-assignment',
        input_sha256=hashlib.sha256((ROOT/relative).read_bytes()).hexdigest(),
        source_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None)
    write(args.out/(instance.id+'.json'), row)
    print('DONE', instance.id, row.get('level'), row.get('completion_status'), round(row['wall_s'], 2), flush=True)

if __name__ == '__main__':
    main()
