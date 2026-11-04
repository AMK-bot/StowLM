"""Summarize measured worker resources; does not rerun any solver or mask."""
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def run():
    campaigns = []
    for folder in ('pilot-contract-final', 'formal-contracts', 'formal-paired/shard0', 'formal-paired/shard1', 'pilot-paired-new-order'):
        controller = read(HERE / folder / 'controller.json')
        children = []
        for job in controller['jobs']:
            path = Path(job['result'])
            result = read(path) if path.exists() else {}
            children.append(dict(case=job['case'], pid=job.get('pid'), exit_code=job.get('exit_code'), reaped=job.get('reaped', False),
                                 completed=result.get('completed', False), result=str(path),
                                 wall_s=result.get('wall_s'), cpu_s=result.get('cpu_s'), sleep_s=result.get('sleep_s'),
                                 peak_rss_bytes=result.get('peak_rss_bytes')))
        campaigns.append(dict(name=folder, controller_pid=controller['pid'], controller_completed=controller['completed'],
                              started_utc=controller['started_utc'], ended_utc=controller.get('ended_utc'),
                              controller_wall_s=controller.get('wall_s'), children=children,
                              measured_completed_worker_cpu_s=sum(j.get('cpu_s') or 0 for j in children),
                              measured_completed_worker_wall_s=sum(j.get('wall_s') or 0 for j in children),
                              measured_completed_worker_sleep_s=sum(j.get('sleep_s') or 0 for j in children),
                              max_worker_lifetime_rss_bytes=max((j.get('peak_rss_bytes') or 0 for j in children), default=0)))
    formal = campaigns[:4]
    final = all(c['controller_completed'] for c in formal)
    span = None
    if final:
        span = (max(datetime.fromisoformat(c['ended_utc']) for c in formal) - min(datetime.fromisoformat(c['started_utc']) for c in formal)).total_seconds()
    value = dict(final=final, campaigns=campaigns, formal_utc_span_s=span,
                 formal_completed_worker_cpu_s=sum(c['measured_completed_worker_cpu_s'] for c in formal),
                 formal_completed_worker_sleep_s=sum(c['measured_completed_worker_sleep_s'] for c in formal),
                 estimate=dict(formal_contracts_s=900, paired_shard0_s=3040, paired_shard1_s=2950, admission_upper_bound_s=4500),
                 active_owned_children=[dict(controller_pid=c['controller_pid'], **j) for c in campaigns for j in c['children'] if j['pid'] and not j['reaped']],
                 resource_policy='Worker CPU and lifetime RSS measured in each child; controller CPU/RSS not measured. Max lifetime worker RSS is not concurrent aggregate RSS. Overlapping worker wall sums are not elapsed campaign duration.',
                 priority_policy='nice/setpriority denied. Formal cooperative duty90%; initial exploratory baseline50%. No GPU and all native thread limits1.',
                 exploratory_baseline20=dict(result=str(HERE/'pilot-small-baseline/result.json'), **{k:read(HERE/'pilot-small-baseline/result.json').get(k) for k in ('pid','wall_s','cpu_s','sleep_s','peak_rss_bytes','source_valid')}),
                 scheduling_note='Attempted own-controller SIGSTOP denied EPERM; no pause succeeded; no foreign PID signalled.')
    (HERE/'compute.json').write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(final=final, formal_completed_worker_cpu_s=value['formal_completed_worker_cpu_s'], active=[(j['controller_pid'],j['pid'],j['case']) for j in value['active_owned_children']])))


if __name__ == '__main__':
    run()
