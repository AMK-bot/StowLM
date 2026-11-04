"""Fresh ordinary full-checker verification with artifact/source identity.

This deliberately imports neither the search oracle nor any checker wrapper.
Run in a separate process after search. A failed report is saved as a failure.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
            'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT/'src'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--input', type=Path, default=ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_500/random_yard/seed-185100000.json')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.out.resolve().is_relative_to(HERE):
        parser.error('output must remain in the experiment')
    args.out.mkdir(parents=True, exist_ok=True)
    print(json.dumps({'owned_pid': os.getpid()}), flush=True)
    from stowage.model import Instance, MovePlan
    from stowage.io import _check_version
    from stowage.moves import apply_plan
    from stowage.checker import check
    input_bytes = args.input.read_bytes()
    plan_bytes = args.plan.read_bytes()
    _check_version(json.loads(input_bytes)['schema_version'])
    _check_version(json.loads(plan_bytes)['schema_version'])
    instance = Instance.model_validate_json(input_bytes)
    plan = MovePlan.model_validate_json(plan_bytes)
    started = monotonic()
    replay = apply_plan(instance, plan)
    loaded = {p.container_id for p in replay.final_state.ship.placements}
    all_requested = set(instance.load_list) <= loaded
    complete = bool(replay.legal and all_requested)
    report = check(instance, plan)
    report_path = args.out/'full-report.json'
    report_path.write_text(report.model_dump_json(indent=2)+'\n')
    copied = args.out/'plan.json'
    copied.write_bytes(plan_bytes)
    (args.out/'input.json').write_bytes(input_bytes)
    sources = {}
    for module in tuple(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        if filename:
            path = Path(filename).resolve()
            if path.is_relative_to(ROOT/'src') and path.is_file():
                sources[str(path.relative_to(ROOT))] = digest(path)
    summary = dict(instance_id=instance.id, input=str(args.input.resolve().relative_to(ROOT)),
        input_sha256=hashlib.sha256(input_bytes).hexdigest(), plan_sha256=digest(copied),
        report_sha256=digest(report_path), native_source_sha256=sources,
        replay_legal=replay.legal, all_requested_aboard=all_requested, complete_stow=complete,
        requested_boxes=len(instance.load_list), requested_loaded=len(set(instance.load_list)&loaded),
        full_checker_passed=report.passed, accepted=bool(report.passed and complete),
        lifts=float(report.count.weighted_nonproductive) if report.passed else None,
        wall_s=monotonic()-started,
        violation_count=len(report.violations), rule_ids=sorted({v.rule_id for v in report.violations}),
        checker='unmodified stowage.checker.check in a fresh process; no search imports')
    (args.out/'verification.json').write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'native_source_sha256'}), flush=True)


if __name__ == '__main__':
    main()
