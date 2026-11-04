"""Read-only exact-parent descendant ownership/RSS telemetry for one prep job."""
import argparse,json,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('ownership',type=Path);a=p.parse_args();r=json.loads(a.ownership.read_text());owner=r['owned_child_pid'];out=a.ownership.parent/f'descendants-{owner}.json';done=a.ownership.with_name(a.ownership.name.replace('.ownership.json','.execution.json'));seen={};peak=0
while not done.exists():
    rows={}
    result=subprocess.run(['ps','-axo','pid=,ppid=,rss='],capture_output=True,text=True,check=True)
    for line in result.stdout.splitlines():
        fields=line.split()
        if len(fields)==3:
            pid,parent,rss=map(int,fields);rows[pid]=(parent,1024*rss)
    owned={owner};changed=True
    while changed:
        more={pid for pid,(parent,rss)in rows.items()if parent in owned};changed=not more<=owned;owned|=more
    current={str(pid):dict(parent_pid=rows[pid][0],rss_bytes=rows[pid][1])for pid in owned if pid in rows}
    for pid,value in current.items():seen[pid]=dict(value,max_rss_bytes=max(value['rss_bytes'],seen.get(pid,{}).get('max_rss_bytes',0)))
    combined=sum(v['rss_bytes']for v in current.values());peak=max(peak,combined)
    value=dict(owned_root_pid=owner,current=current,ever_seen=seen,combined_current_rss_bytes=combined,combined_peak_sampled_rss_bytes=peak,read_only=True)
    tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(out)
    if combined>=56_000_000_000:
        (a.ownership.parent/f'stop-{owner}.json').write_text(json.dumps(dict(reason='Owned descendant aggregate exceeds56GB',record=str(out))))
    time.sleep(.1)
