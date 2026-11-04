"""Summarise every predeclared attempt, including killed setup processes.

Completed-result rows alone would drop setup timeouts from the denominator and
inflate solve shares. Wrapper checkpoints define attempts; result rows enrich
them. This script reads experiment outputs only, never canonical instance files.
"""
from __future__ import annotations

from collections import Counter,defaultdict
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def main():
    results={}
    for name in ('v2-tiny-prewarm-results.jsonl','v2-small-prewarm-results.jsonl'):
        path=ROOT/name
        if path.exists():
            for line in path.read_text().splitlines():
                row=json.loads(line);results[row['instance']]=row
    groups=defaultdict(list)
    for name in ('v2-tiny-prewarm.jsonl','v2-small-prewarm.jsonl','v2-small-prefix.jsonl'):
        path=ROOT/name
        modern=path.with_suffix('.runs.jsonl')
        if modern.exists():path=modern
        if not path.exists():continue
        for line in path.read_text().splitlines():
            attempt=json.loads(line)
            log=Path(attempt['log'])
            start=json.loads(log.read_text().splitlines()[0])
            identity=start['instance']
            row=results.get(identity)
            status=(('MODEL_UNAVAILABLE' if row['metrics'].get('model_unavailable') else row['status'])
                    if row else attempt['status'])
            data=dict(instance=identity,split=start['split'],status=status,
                      process_wall_s=attempt['actual_wall_s'],pid=attempt['pid'],
                      outer_cap_s=attempt['estimated_wall_s'],result=row)
            groups['/'.join(identity.split('/')[:2])].append(data)
    report={}
    for group,attempts in groups.items():
        rows=[a['result'] for a in attempts if a['result']]
        report[group]=dict(attempts=len(attempts),solved=sum(r['objective'] is not None for r in rows),
            optimal=sum(r['status']=='OPTIMAL' for r in rows),
            full_checker_pass=sum(r['checker_passed'] is True for r in rows),
            full_checker_attempted=sum(r['checker_passed'] is not None for r in rows),
            full_checker_pass_rate=(sum(r['checker_passed'] is True for r in rows)/sum(r['checker_passed'] is not None for r in rows)) if any(r['checker_passed'] is not None for r in rows) else None,
            brute_force_agreements=sum(bool((r.get('oracle') or {}).get('brute_force_agreement')) for r in rows),
            statuses=dict(Counter(a['status'] for a in attempts)),
            mean_process_wall_s=sum(a['process_wall_s'] for a in attempts)/len(attempts),
            max_process_wall_s=max(a['process_wall_s'] for a in attempts),
            known_cpu_s=sum(r['cpu_s'] for r in rows),
            cpu_unmeasured_attempts=len(attempts)-len(rows),
            max_measured_peak_rss_mb=max((r['peak_rss_mb'] for r in rows),default=None),
            cases=attempts)
    (ROOT/'canonical-campaign-summary.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({group:{k:v for k,v in data.items() if k!='cases'} for group,data in report.items()},indent=2))


if __name__=='__main__':main()
