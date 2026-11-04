"""Count every measured v2 attempt, including excluded drift effort and reruns."""
import json
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
def read(path):return json.loads(Path(path).read_text())


def main():
    names=('pilot-v2-contracts','pilot-v2-paired','formal-v2-contracts','formal-v2-paired/shard0','formal-v2-paired/shard1','formal-v2-archive/shard0','formal-v2-archive/shard1')
    campaigns=[];all_rows=[]
    for name in names:
        controller=read(HERE/name/'controller.json');rows=[]
        for job in controller['jobs']:
            if not job.get('pid'):continue
            path=Path(job['result']);result=read(path)if path.exists()else{}
            row=dict(case=job['case'],result=str(path),pid=job['pid'],exit_code=job.get('exit_code'),reaped=job.get('reaped',False),completed=result.get('completed',False),cpu_s=result.get('cpu_s'),wall_s=result.get('wall_s'),sleep_s=result.get('sleep_s'),peak_rss_bytes=result.get('peak_rss_bytes'));rows.append(row);all_rows.append(row)
        manifest=read(HERE/name/'manifest.json');campaigns.append(dict(name=name,controller_pid=controller['pid'],completed=controller['completed'],started_utc=controller['started_utc'],ended_utc=controller.get('ended_utc'),controller_wall_s=controller.get('wall_s'),estimate_s=manifest.get('estimate_s',manifest.get('estimated_wall_s')),v1_weight_s=manifest.get('estimated_v1_weight_s'),children=rows))
    witness=read(HERE/'primary-v2-witnesses.json')['cases'];contract=read(HERE/'contract-witnesses-v2.json')['cases'];accepted_paths=set(witness.values())|set(contract.values());selection=read(HERE/'archive-continuation-selection.json');excluded_paths={row['original_result']for row in selection['selected']}
    final=all(row['completed']for row in campaigns);span=(max(datetime.fromisoformat(row['ended_utc'])for row in campaigns)-min(datetime.fromisoformat(row['started_utc'])for row in campaigns)).total_seconds()if final else None
    result=dict(final=final,campaigns=campaigns,all_attempts_worker_cpu_s=sum(row.get('cpu_s')or 0 for row in all_rows),all_attempts_worker_wall_sum_s=sum(row.get('wall_s')or 0 for row in all_rows),all_attempts_sleep_s=sum(row.get('sleep_s')or 0 for row in all_rows),
                accepted_worker_cpu_s=sum(row.get('cpu_s')or 0 for row in all_rows if row['result']in accepted_paths),
                excluded_original_worker_cpu_s=sum(row.get('cpu_s')or 0 for row in all_rows if row['result']in excluded_paths),
                excluded_original_worker_wall_s=sum(row.get('wall_s')or 0 for row in all_rows if row['result']in excluded_paths),
                max_worker_lifetime_rss_bytes=max(row.get('peak_rss_bytes')or 0 for row in all_rows),campaign_utc_span_s=span,
                raw_direct_worker_count=len(all_rows),active_owned_children=[row for row in all_rows if not row['reaped']],
                continuation_launchers=[read(HERE/f'formal-v2-archive/shard{index}/archive-launcher.json')for index in(0,1)],
                policy='All attempts included:135 required accepted results +14 excluded original drift attempts +one excluded one-pair timing pilot. Worker CPU/wall counted once; launcher descendants not added again. Worker lifetime RSS is not aggregate simultaneous usage. Current live compatibility and optional wide configuration are separate.')
    (HERE/'compute-v2-combined.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps({key:result[key]for key in('final','all_attempts_worker_cpu_s','excluded_original_worker_cpu_s','raw_direct_worker_count','active_owned_children')}))


if __name__=='__main__':main()
