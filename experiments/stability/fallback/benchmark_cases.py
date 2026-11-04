"""Time complete production checks on the former fallback regressions.

Run only after cache_warm. The checker owns its bounded fail-closed direct path;
this driver neither changes numerical tolerances nor suppresses violations.
A first process call retains disk caches, followed by repeat warm calls.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from cases import CASES, build_case
from stowage.checker.full import check


def run(case, output, repeats=3):
    start=perf_counter()
    instance,plan,info=build_case(case)
    result=dict(case=case,construction=info,assembly_seconds=perf_counter()-start,
                timing_scope='full production checker; disk caches retained',checks=[])
    for repeat in range(repeats+1):
        start=perf_counter()
        report=check(instance,plan)
        stability=report.parts['stability']
        result['checks'].append(dict(seconds=perf_counter()-start,passed=report.passed,
            stability_ran=stability.ran,stability_error=stability.error,
            stability_metrics=stability.metrics,stability_margins=stability.margins,
            violation_ids=sorted({violation.rule_id for violation in report.violations})))
        output.write_text(json.dumps(result,indent=2,default=str)+'\n')
        print(f"{case} call{repeat}: {result['checks'][-1]['seconds']:.3f}s "
              f"stability_ran={stability.ran} error={stability.error}",flush=True)
    result['cold_seconds']=result['checks'][0]['seconds']
    if repeats:
        result['warm_seconds']=median(row['seconds'] for row in result['checks'][1:])
    output.write_text(json.dumps(result,indent=2,default=str)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=CASES,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    run(args.case,args.output,args.repeats)
