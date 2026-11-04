"""Fresh native replay/full checks of all distinct final passing plan artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from time import monotonic

from catalog import ensure_safe
from stowage.checker import check
from stowage.checker.full import report_matches_inputs
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs',nargs='+',required=True)
    args = parser.parse_args()
    print('Owned audit PID',os.getpid(),flush=True)
    manifest = {r['input']:r for r in json.loads((HERE/'MANIFEST.json').read_text())}
    seen,out = set(),[]
    (HERE/'audit-reports').mkdir(exist_ok=True)
    for run in args.runs:
        for evidence in sorted(Path(run).glob('*.json')):
            row = json.loads(evidence.read_text())
            if not isinstance(row,dict) or row.get('passed') is not True or not row.get('plan'):
                continue
            ensure_safe(row['input'])
            identity = manifest[row['input']]
            digest = hashlib.sha256(Path(row['input']).read_bytes()).hexdigest()
            assert digest==identity['input_sha256']==row['input_sha256']
            raw = json.dumps(row['plan'],sort_keys=True,separators=(',',':'))
            plan_hash = hashlib.sha256(raw.encode()).hexdigest()
            key = digest,plan_hash
            if key in seen:
                continue
            seen.add(key)
            instance = load_instance(row['input'])
            plan = MovePlan.model_validate(row['plan'])
            started = monotonic()
            replay = apply_plan(instance,plan)
            report = check(instance,plan)
            assert replay.legal and report.passed and not report.violations
            assert report.count.loads==len(instance.load_list)
            assert report.count.weighted_nonproductive==row['lifts']
            assert report_matches_inputs(report,instance,plan)
            target = HERE/'audit-reports'/f'{instance.id}-{plan_hash[:12]}.json'
            target.write_text(report.model_dump_json(indent=2)+'\n')
            record = dict(instance_id=instance.id,input=row['input'],evidence=str(evidence),
                          input_sha256=digest,plan_sha256=plan_hash,report=str(target),
                          report_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                          replay_legal=True,passed=True,loads=report.count.loads,
                          lifts=row['lifts'],violations=0,wall_s=monotonic()-started,
                          audited_utc=datetime.now(timezone.utc).isoformat())
            out.append(record)
            (HERE/'AUDIT.json').write_text(json.dumps(out,indent=2)+'\n')
            print(instance.id,row['lifts'],'native audit passed',flush=True)
    print('Audited',len(out),'distinct plans',flush=True)


if __name__=='__main__':
    main()
