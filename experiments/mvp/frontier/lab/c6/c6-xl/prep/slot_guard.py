"""Idle outer guard for one owned secondary-slot job; no numerical imports.

Stops only the child subtree started here, through the exact recorded worker
stop file. Fresh peer high-water RSS is summed conservatively with our peak.
"""
from __future__ import annotations
import argparse,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
XL=HERE.parent
ROOT=next(p for p in HERE.parents if(p/'pyproject.toml').exists())
SENTINEL=ROOT/'experiments/mvp/frontier/lab/c1/leader/BENCH-RUNNING'

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    t=path.with_suffix(path.suffix+'.tmp');t.write_text(json.dumps(value,indent=2)+'\n');t.replace(path)
def read(path):
    try:return json.loads(Path(path).read_text())
    except(OSError,ValueError):return {}
def fresh_peer_peak(roots,now):
    seen=[]
    for root in roots:
        for path in root.rglob('*.monitor'):
            try:fresh=now-path.stat().st_mtime<5.
            except OSError:continue
            value=read(path)
            row=read(path.with_suffix('.json'))
            finalized='guard_status'in row
            active_pending=(path.parent/'ACTIVE.lock').is_file()and not finalized
            # A native call holding the GIL can delay Python telemetry. Retain
            # that active worker's last high-water RSS until parent finalizes it.
            if not finalized and (active_pending or(fresh and value.get('status')=='running')):
                seen.append((int(value.get('peak_rss_bytes',0)),str(path),value.get('pid')))
    return max(seen,default=(0,None,None))
def owned_worker(kind,out,parent_pid,started):
    if kind=='manual':
        identity=read(out/'ownership.json');monitor=out/'monitor.json';stop=out/'monitor.stop'
        if identity.get('parent_pid')!=parent_pid:return None
        expected=identity.get('owned_child_pid')
    else:
        try:identity=int((out/'ACTIVE.lock').read_text())
        except(OSError,ValueError):return None
        if identity!=parent_pid:return None
        paths=list(out.glob('*.monitor'))
        if len(paths)!=1:return None
        monitor=paths[0];stop=Path(str(monitor)+'.stop');expected=None
    try:
        if monitor.stat().st_mtime<started:return None
    except OSError:return None
    observation=read(monitor)
    if not observation.get('pid')or(expected is not None and observation['pid']!=expected):return None
    return dict(pid=observation['pid'],peak_rss_bytes=int(observation.get('peak_rss_bytes',0)),monitor=str(monitor),stop=str(stop))

def main():
    p=argparse.ArgumentParser();p.add_argument('--kind',choices=('manual','bench'),required=True);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--guard-out',required=True,type=Path);p.add_argument('--peer-root',action='append',type=Path,default=[]);p.add_argument('--hard-s',required=True,type=float);p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args();cmd=a.command[1:]if a.command[:1]==['--']else a.command
    out=a.out.resolve();guard=a.guard_out.resolve()
    if not out.is_relative_to(XL)or not guard.is_relative_to(XL):p.error('Outputs outside own experiment')
    if not cmd:p.error('Missing exact child supervisor command')
    if a.kind=='bench':
        if '--maps'not in cmd:p.error('Bench command requires an explicit one-map list')
        paths=Path(cmd[cmd.index('--maps')+1]).read_text().splitlines();paths=[s for s in paths if s.strip()and not s.lstrip().startswith('#')]
        if len(paths)!=1:p.error('Outer slotguard permits exactly one cold map per invocation')
        if '--out'not in cmd or Path(cmd[cmd.index('--out')+1]).resolve()!=out:p.error('Bench command output mismatch')
    else:
        if '--out'not in cmd or Path(cmd[cmd.index('--out')+1]).resolve()!=out:p.error('Manual command output mismatch')
    if SENTINEL.exists():
        write(guard,dict(status='not_started_cpu_slot_reduced',sentinel=str(SENTINEL),command=cmd));return 75
    started_wall=time.time();started=time.monotonic();owned=[];reason=None;peak_sum=0
    child=subprocess.Popen(cmd,cwd=ROOT,env=dict(os.environ))
    write(guard,dict(status='running',parent_pid=os.getpid(),owned_supervisor_pid=child.pid,command=cmd,started_unix=started_wall))
    try:
        while child.poll()is None:
            worker=owned_worker(a.kind,out,child.pid,started_wall)
            peer=fresh_peer_peak([r.resolve()for r in a.peer_root],time.time())
            if worker:
                if worker['pid']not in owned:owned.append(worker['pid'])
                peak_sum=max(peak_sum,worker['peak_rss_bytes']+peer[0])
            if reason is None:
                if SENTINEL.exists():reason='cpu_slot_reduced'
                elif peak_sum>=56_000_000_000:reason='combined_memory_limit'
                elif time.monotonic()-started>=a.hard_s:reason='outer_timeout'
            if reason and worker:
                # Worker self-watchdog consumes only this exact stop path,
                # associated with the supervisor PID started and recorded here.
                write(worker['stop'],dict(status='memory_limit'if reason=='combined_memory_limit'else reason,reason=reason))
            write(guard,dict(status=reason or'running',parent_pid=os.getpid(),owned_supervisor_pid=child.pid,owned_worker_pids=owned,worker=worker,peer_peak=peer,combined_peak_rss_upper_bound_bytes=peak_sum,command=cmd,elapsed_s=time.monotonic()-started))
            time.sleep(.2)
    except BaseException:
        worker=owned_worker(a.kind,out,child.pid,started_wall)
        if worker:write(worker['stop'],dict(status='requested_stop',reason='outer_guard_interrupted'))
        # This exact Popen PID is our own recorded supervisor; never discover
        # or signal ambient processes by name, pattern, or process listing.
        try:child.terminate()
        except PermissionError:pass
        raise
    record=read(guard);record.update(status=reason or'normal',returncode=child.returncode,elapsed_s=time.monotonic()-started,finished_unix=time.time());write(guard,record)
    if reason and a.kind=='bench':
        for path in out.glob('*.json'):
            row=read(path)
            if row.get('recorded_pids')and set(row['recorded_pids'])<=set(owned):
                row.update(external_guard_status=reason,external_guard=str(guard.relative_to(ROOT)),passed=False,completed=None,status=reason);write(path,row)
    return 75 if reason else child.returncode
if __name__=='__main__':sys.exit(main())
