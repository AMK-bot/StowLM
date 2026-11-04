"""Summarize completed checkpoints without opening input or held-out files."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean


def read_lines(path):
    rows=[]
    if path.exists():
        for line in path.read_text().splitlines():
            try:rows.append(json.loads(line))
            except json.JSONDecodeError:pass  # An active writer can leave its last frame incomplete.
    return rows


def stable_physics(row):
    return (row.get("runtime_source_before")==row.get("runtime_source_after") and
            row.get("input_physics_source_comparison",{}).get("physical_source_match") is True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,default=Path(__file__).resolve().parent/'paired-current')
    args=p.parse_args();selection=json.loads((args.directory/'selection.json').read_text())['entries']
    rows=read_lines(args.directory/'results.jsonl');latest={}
    for row in rows:latest[(row['entry']['spec_key'],row['planner'])]=row
    report=dict(planned_cases=len(selection),raw_completed_rows=len(latest),
        planned_cohort_complete=all((e['spec_key'],planner) in latest and stable_physics(latest[(e['spec_key'],planner)])
            for e in selection for planner in ('exact','scalable')),
        scope='Pinned prior-rule epoch only; no claim for latest mutable source',
        quarantined_rows=[dict(spec_key=r['entry']['spec_key'],planner=r['planner'],
            runtime_epoch_changed=r.get('runtime_source_before')!=r.get('runtime_source_after'),
            physical_sources=r.get('input_physics_source_comparison')) for r in latest.values() if not stable_physics(r)],
        rungs={},point_gaps=[],inconsistencies=[])
    for rung in ('tiny','small'):
        chosen=[e for e in selection if e['rung']==rung]
        group=dict(planned_cases=len(chosen),actual_load_counts=[],splits=dict(Counter(e['split'] for e in chosen)))
        for planner in ('exact','scalable'):
            done=[latest[(e['spec_key'],planner)] for e in chosen if (e['spec_key'],planner) in latest]
            stable=[r for r in done if stable_physics(r)]
            stable_optimal=sum((r.get('exact_certificate') or {}).get('status')=='OPTIMAL' for r in stable)
            complete=[r for r in done if (r.get('validation') or {}).get('completed')]
            checked=[r for r in complete if (r.get('validation') or {}).get('checker_passed') is not None]
            passed=[r for r in checked if r['validation']['checker_passed']]
            group[planner]=dict(raw_processed=len(done),planned_cases=len(chosen),raw_complete_departures=len(complete),
                stable_physics_processed=len(stable),stable_optimal=stable_optimal,
                stable_checker_passes=sum((r.get('validation') or {}).get('checker_passed') is True for r in stable),
                observed_proved_share=stable_optimal/len(stable) if stable and planner=="exact" else None,
                stable_mean_search_s=mean(r["timing_s"]["search"] for r in stable) if stable else None,
                stable_mean_pipeline_s=mean(r["timing_s"]["total"] for r in stable) if stable else None,
                raw_checked_departures=len(checked),raw_checker_passes=len(passed),
                raw_checker_pass_rate=len(passed)/len(checked) if checked else None,
                stable_solved_share=sum((r.get("validation") or {}).get("checker_passed") is True for r in stable)/len(stable) if stable else None,
                raw_optimal=sum((r.get('exact_certificate') or {}).get('status')=='OPTIMAL' for r in done),
                raw_mean_search_s=mean(r['timing_s']['search'] for r in done) if done else None,
                raw_mean_pipeline_s=mean(r['timing_s']['total'] for r in done) if done else None,
                raw_max_pipeline_s=max((r['timing_s']['total'] for r in done),default=None),
                raw_total_pipeline_s=sum(r['timing_s']['total'] for r in done),
                raw_search_overruns=sum(not r.get('search_finished_within_budget',r['timing_s']['search']<=r['config']['search_s']) for r in done),
                raw_source_epoch_changes=sum(r.get('runtime_source_before')!=r.get('runtime_source_after') for r in done),
                raw_physical_source_mismatches=sum((r.get('input_physics_source_comparison') or {}).get('physical_source_match') is not True for r in done),
                raw_validation_timeouts=sum(r.get('validation_timeout',False) for r in done),
                raw_violation_counts=dict(Counter(v for r in checked for v in r['validation'].get('violation_ids',[]))))
        for entry in chosen:
            exact=latest.get((entry['spec_key'],'exact'));scalable=latest.get((entry['spec_key'],'scalable'))
            if exact:group['actual_load_counts'].append(exact['n_load'])
            if not exact or not scalable:continue
            echeck=exact.get('validation') or {};scheck=scalable.get('validation') or {}
            stable=all(r.get('runtime_source_before')==r.get('runtime_source_after') and
                       r.get('input_physics_source_comparison',{}).get('physical_source_match') is True
                       for r in (exact,scalable))
            eligible=(exact['exact_certificate']['status']=='OPTIMAL' and stable and
                      exact['input_sha256']==scalable['input_sha256'] and
                      exact['config']['c2_off']==scalable['config']['c2_off'] and
                      all(c.get('checker_passed') and c.get('configured_replay_passed') for c in (echeck,scheck)))
            item=dict(spec_key=entry['spec_key'],rung=rung,qualified=eligible,point_gap=None,lift_difference=None)
            if eligible:
                e,s=echeck['objective'],scheck['objective'];item.update(exact_lifts=e,scalable_lifts=s,
                    lift_difference=s-e,point_gap=(s-e)/s if s else 0.)
                if s<e:report['inconsistencies'].append(item)
            report['point_gaps'].append(item)
        report['rungs'][rung]=group
    oracle=read_lines(args.directory/'oracle-results.jsonl')
    report['oracle']=dict(attempted=len(oracle),brute_force_agreement=sum(r.get('brute_force_agreement',False) for r in oracle),
        full_checker_optimal=sum(r.get('status')=='OPTIMAL' and r.get('checker_passed') for r in oracle),
        statuses=dict(Counter(r['status'] for r in oracle)),
        override_necessity_counts=dict(Counter(r.get('override_necessity','unresolved') for r in oracle)))
    (args.directory/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,sort_keys=True))


if __name__=='__main__':main()
