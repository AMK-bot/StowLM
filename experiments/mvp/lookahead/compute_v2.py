"""Summarize measured worker resources; does not rerun any solver or mask."""
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def run():
    campaigns = []
    for folder in ('pilot-v2-contracts', 'pilot-v2-paired', 'formal-v2-contracts', 'formal-v2-paired/shard0', 'formal-v2-paired/shard1'):
        path = HERE / folder / 'controller.json'
        if not path.exists():
            continue
        controller = read(path)
        children = []
        for job in controller['jobs']:
            if job.get('skipped_completed'):
                continue
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
    formal = campaigns
    final = len(formal)==5 and all(c['controller_completed'] for c in formal)
    span = None
    if final:
        span = (max(datetime.fromisoformat(c['ended_utc']) for c in formal) - min(datetime.fromisoformat(c['started_utc']) for c in formal)).total_seconds()
    value = dict(final=final, campaigns=campaigns, campaign_utc_span_s=span,
                 campaign_completed_worker_cpu_s=sum(c['measured_completed_worker_cpu_s'] for c in formal),
                 campaign_completed_worker_sleep_s=sum(c['measured_completed_worker_sleep_s'] for c in formal),
                 estimate=read(HERE/'campaign-v2.json'),
                 active_owned_children=[dict(controller_pid=c['controller_pid'], **j) for c in campaigns for j in c['children'] if j['pid'] and not j['reaped']],
                 resource_policy='Includes separate one-pair timing pilot, excluded from formal1400 denominator. Worker CPU and lifetime RSS measured in each child; controller CPU/RSS not measured. Max lifetime worker RSS is not concurrent aggregate RSS. Overlapping worker wall sums are not elapsed campaign duration.',
                 priority_policy='nice/setpriority denied. V2 pilot and formal cooperative duty90%. No GPU and all native thread limits1.',
                 scheduling_note='Separate all-new v2 campaign; completed fixed pilot cases skipped by main controllers, counted only once. No signals sent.')
    (HERE/'compute-v2.json').write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(final=final, campaign_completed_worker_cpu_s=value['campaign_completed_worker_cpu_s'], active=[(j['controller_pid'],j['pid'],j['case']) for j in value['active_owned_children']])))


if __name__ == '__main__':
    run()
