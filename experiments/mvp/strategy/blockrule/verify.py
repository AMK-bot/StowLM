"""Read-only verification of frozen inputs and all reported certificates."""
from __future__ import annotations
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(HERE),str(ROOT/'src')]
import study


def main():
    records = []
    sources = set()
    configs = set()
    failures = []
    total = 0
    for name in ('small-train','medium-train','small-val'):
        out=HERE/'runs'/name
        if not (out/'completion.json').exists():
            failures.append(f'{name}: not complete')
            continue
        run=study.read_json(out/'run.json')
        done=study.read_json(out/'completion.json')
        if done['errors']:
            failures.append(f'{name}: controller errors')
        sources.add(json.dumps(run['sources'],sort_keys=True))
        configs.add(json.dumps(run['config'],sort_keys=True))
        drift=[p for p,h in run['sources'].items() if study.digest(ROOT/p)!=h]
        if drift:
            failures.append(f'{name}: source drift {drift}')
        count=0
        for job in run['jobs']:
            assert study.digest(ROOT/job['input'])==job['input_sha256']
            summary=study.read_json(out/'maps'/job['map_id']/'summary.json')
            assert summary['complete'] is True, job['map_id']
            for arm in summary['arms'].values():
                for variant in ('raw','compact'):
                    record=arm[variant]
                    if record.get('passed'):
                        assert record.get('checker_attempted') is True
                        assert record.get('checker_passed') is True
                    if record.get('checker_attempted'):
                        report=study.read_json(ROOT/record['report_path'])
                        finished=study.read_json(ROOT/record['finished_path'])
                        assert report['passed'] is record['checker_passed']
                        assert finished['plan'] is not None
                        if record['passed']:
                            assert report['passed'] is True and finished['status']=='ok'
                            assert not record['layout_violations']
                            assert report['count']['weighted_nonproductive']==record['moves']
                        count+=1
            for variant,pair in summary['selected'].items():
                default,rule,chosen=pair['default'],pair['rule'],pair['planner']
                assert chosen==study.choose(default,rule)
                if default['passed']:
                    assert chosen['passed'] and chosen['moves']<=default['moves']
                if variant=='compact':
                    for arm in ('default','rule'):
                        assert pair[arm]==study.best_variant(summary['arms'][arm]['raw'],summary['arms'][arm]['compact'])
        records.append(dict(campaign=name,maps=len(run['jobs']),full_checker_reports=count))
        total+=count
    if len(sources)>1 or len(configs)>1:
        failures.append('campaigns used different source/config epochs')
    claim=HERE/'validation-once.json'
    if not claim.exists():
        failures.append('validation-once claim missing')
    result=dict(passed=not failures,campaigns=records,full_checker_reports=total,
                input_hashes_verified=True,checker_costs_match=True,
                selection_recomputed=True,default_never_worsened=True,
                no_source_drift=len(sources)==1 and not any('drift' in s for s in failures),
                failures=failures)
    study.atomic_json(HERE/'verification.json',result)
    print(json.dumps(result,indent=2))
    if failures:
        raise SystemExit(1)


if __name__=='__main__':
    main()
