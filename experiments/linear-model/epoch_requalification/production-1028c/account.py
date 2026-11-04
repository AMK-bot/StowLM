"""Compute accounting from recorded owned child processes; no process discovery."""
from datetime import datetime,timezone
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
    unique={}
    for p in ROOT.rglob('*.execution.json'):
        try: value=json.loads(p.read_text())
        except (ValueError,OSError): continue
        if not all(key in value for key in ('start_utc','pid','command','name','admission_wait_seconds')):
            continue
        token=(value['pid'],value['start_utc'])
        item=dict(record=str(p.relative_to(ROOT)),**value)
        if token not in unique or ('exit_code' in value and 'exit_code' not in unique[token]):
            unique[token]=item
    rows=list(unique.values())
    complete=[x for x in rows if 'exit_code' in x]
    pending=[x for x in rows if 'exit_code' not in x]
    starts=[datetime.fromisoformat(x['start_utc']).timestamp() for x in rows]
    ends=[datetime.fromisoformat(x['finished_utc']).timestamp() for x in complete]
    result=dict(label='pre-final',estimate=json.loads((ROOT/'compute-estimate.json').read_text()),
        recorded_jobs=len(rows),completed_jobs=len(complete),pending_jobs=pending,
        successful_jobs=sum(x['exit_code']==0 for x in complete),
        failed_or_interrupted=[x for x in complete if x['exit_code']!=0],
        total_owned_child_cpu_s=sum(x.get('cpu_seconds',0) for x in complete),
        summed_child_wall_s=sum(x.get('seconds',0) for x in complete),
        span_wall_s=max(ends)-min(starts) if ends else 0,
        highest_measured_child_rss_bytes=max((x.get('max_rss_bytes',0) for x in complete),default=0),
        peak_reserved_cores=9,peak_reserved_ram_gb=24,aggregate_peak_ram_instrumented=False,
        gpu=False,cloud=False,native_threads=1,nice='attempted10; sandbox denied setpriority',
        all_slots_released=json.loads((ROOT/'compute-active.json').read_text())==[],
        commands=rows,generated_utc=datetime.now(timezone.utc).isoformat())
    (ROOT/'compute-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('commands','estimate','failed_or_interrupted')}))
if __name__=='__main__':main()
