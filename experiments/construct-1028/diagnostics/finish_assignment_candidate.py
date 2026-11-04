"""Diagnostic only: finish a saved complete assignment despite its mask failure.

The result is never admitted by the constructive planner. File-backed main is
required by the ballast watchdog's multiprocessing spawn on macOS.
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from cache_runtime import redirect_cache
# Spawned watchdog workers import this file too, so every process receives the
# private cache paths before it imports the target ballast function.
redirect_cache(HERE / 'runtime')


def main():
    import argparse
    from time import monotonic
    from stowage.model import Instance, MovePlan
    from stowage.solver.ballast import finish_ballast
    from stowage.checker import check
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output-prefix',type=Path,required=True)
    parser.add_argument('--budget',type=float,default=180)
    args=parser.parse_args()
    if not args.output_prefix.resolve().is_relative_to(HERE):
        parser.error('output-prefix must stay under construct-1028')
    instance=Instance.model_validate_json(args.input.read_text())
    plan=MovePlan.model_validate_json(args.plan.read_text())
    started=monotonic()
    outcome=finish_ballast(instance,plan,time_limit_s=args.budget,seed=0)
    print('FINISH',outcome.status,'TIME',monotonic()-started,flush=True)
    args.output_prefix.with_suffix('.finisher.json').write_text(outcome.model_dump_json(indent=2))
    if outcome.plan is not None:
        report=check(instance,outcome.plan)
        print('CHECK',report.passed,'RULES',[v.rule_id for v in report.violations if v.severity=='hard'],
              'TIME',monotonic()-started,flush=True)
        args.output_prefix.with_suffix('.full-check.json').write_text(report.model_dump_json(indent=2))


if __name__=='__main__':
    main()
