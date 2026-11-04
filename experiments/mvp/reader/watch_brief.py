"""Refresh D220's report and verify completion without starting more solvers."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time

from experiments.mvp.reader.brief_report import build
from experiments.mvp.reader.run_brief import RUN, cases, write, verify_sources
from experiments.mvp.hybrid.admission import evidence_errors


def snapshot():
    return {folder: len(list((RUN/folder).glob('*.json'))) for folder in ('parser','plans','audits')}


def verify_completion():
    """Cheap evidence verification; the independent full checks already ran."""
    verify_sources()
    cohort = cases()
    errors, cost, returned, compliant = [], Decimal(0), 0, 0
    for case in cohort:
        name = f"{case['case_id']}.json"
        parsed = json.loads((RUN/'parser'/name).read_text())
        planned = json.loads((RUN/'plans'/name).read_text())
        audited = json.loads((RUN/'audits'/name).read_text())
        if not audited.get('audit_completed') or not audited.get('baseline_verified'):
            errors.append(case['case_id']+': incomplete fresh audit')
        if audited.get('error_type') or planned.get('error_type'):
            errors.append(case['case_id']+': unexpected execution/audit error')
        if evidence_errors(planned['admission']) or evidence_errors(audited['admission']):
            errors.append(case['case_id']+': invalid admission')
        if 'plan' in planned:
            returned += 1
            if not audited.get('passed') or not audited.get('compliant_pass'):
                errors.append(case['case_id']+': returned plan failed independent gate')
        compliant += bool(audited.get('compliant_pass'))
        for sentence in parsed['brief']['sentences']:
            cost += Decimal(sentence.get('parser',{}).get('cost_usd','0'))
    if len(cohort) != 30 or cost > Decimal('2'):
        errors.append('Cohort count or Spark budget violation')
    summary = build()  # Also verifies cohort, source and audit-artifact hashes.
    return dict(status='complete' if not errors else 'needs_review', errors=errors,
                verified_utc=datetime.now(timezone.utc).isoformat(), count=len(cohort),
                returned_plans=returned, compliant_passes=compliant, cost_usd=str(cost), summary=summary)


def watch(max_hours=4.):
    started = time.time()
    write(RUN/'watcher.json', dict(pid=os.getpid(), controller=json.loads((RUN/'controller.json').read_text()),
                                  started_utc=datetime.now(timezone.utc).isoformat(),
                                  max_hours=max_hours, action='report refresh and evidence verification only'))
    previous = None
    while time.time()-started < max_hours*3600:
        current = snapshot()
        if current != previous:
            summary = build()
            print(json.dumps(dict(event='report_refreshed', **current, summary=summary)), flush=True)
            previous = current
        log = (RUN/'run.log').read_text()
        if current['audits'] == 30 and current['plans'] == 30:
            result = verify_completion()
            write(RUN/'completion.json', result)
            print(json.dumps(result), flush=True)
            return 0 if result['status']=='complete' else 1
        if 'Traceback (most recent call last)' in log:
            write(RUN/'completion.json', dict(status='needs_review', reason='Controller traceback; no automatic retries', snapshot=current))
            return 1
        time.sleep(15)
    write(RUN/'completion.json', dict(status='incomplete', reason='Monitor deadline; workers are not killed or retried', snapshot=snapshot()))
    return 2


if __name__ == '__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--once',action='store_true')
    cli.add_argument('--max-hours',type=float,default=4.)
    args=cli.parse_args()
    if args.once:
        print(json.dumps(dict(snapshot=snapshot(), report=build())))
    else:
        raise SystemExit(watch(args.max_hours))
