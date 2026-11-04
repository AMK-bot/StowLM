"""Idle sequential orchestration of predeclared cold benchmark arms.

Every numerical child is wrapped by the one-job secondary-slot guard. This
module is outside frozen method sources and never changes a solver decision.
"""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
XL=HERE.parent
ROOT=next(p for p in HERE.parents if(p/'pyproject.toml').exists())

def write(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(value,indent=2)+'\n');t.replace(p)
def drift(snapshot):
    return [f['path']for f in snapshot['files']if not Path(f['path']).is_file()or hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()!=f['sha256']]
def main():
    p=argparse.ArgumentParser();p.add_argument('--selection',required=True,type=Path);p.add_argument('--snapshot',required=True,type=Path)
    p.add_argument('--ordinals',required=True,help='Explicit predeclared ordinal list');p.add_argument('--arms',choices=('all','variant','baseline'),default='all');p.add_argument('--record',required=True,type=Path);a=p.parse_args()
    for path in (a.selection,a.snapshot,a.record):
        if not path.resolve().is_relative_to(XL):p.error('Artifact outside own scope')
    wanted=[int(s)for s in a.ordinals.split(',')];selection=json.loads(a.selection.read_text());rows={r['ordinal']:r for r in selection['rows']};snapshot=json.loads(a.snapshot.read_text())
    if len(wanted)!=len(set(wanted))or not set(wanted)<=rows.keys():p.error('Invalid or duplicate predeclared ordinals')
    tasks=[]
    for ordinal in wanted:
        r=rows[ordinal];arms=r['arms']if a.arms=='all'else[a.arms]
        if not set(arms)<=set(r['arms']):p.error('Arm not predeclared for ordinal')
        for arm in arms:tasks.append((r,arm))
    done=[];record=dict(started_unix=time.time(),parent_pid=os.getpid(),selection=str(a.selection),snapshot=str(a.snapshot),tasks=[dict(ordinal=r['ordinal'],arm=arm,input=r['input'])for r,arm in tasks],finished_arms=done)
    write(a.record,record)
    for row,arm in tasks:
        before=drift(snapshot)
        if before:record.update(status='input_drift',drift=before);write(a.record,record);return 42
        ordinal=row['ordinal'];out=XL/'results'/f'xl-{arm}'/f'{ordinal:03d}'
        existing=[]
        for path in out.glob('*.json'):
            previous=json.loads(path.read_text())
            if 'recorded_pids'in previous:
                if previous.get('input')!=row['input']:raise ValueError('Ordinal output collision')
                existing.append(previous)
        if existing:
            # Preserve every terminal measurement, including failures/censors.
            # Reading completed measurement metadata is not a solver warmstart.
            done.append(dict(ordinal=ordinal,arm=arm,preserved_existing_terminal_measurement=True))
            write(a.record,record);continue
        method=XL/('solver/method.py'if arm=='variant'else'bench/baseline/method.py')
        guard=XL/'solver'/f"guard-{selection['ship']}-{arm}{ordinal}.json"
        cmd=[sys.executable,'-B',str(HERE/'slot_guard.py'),'--kind','bench','--out',str(out),'--guard-out',str(guard),'--peer-root',str(XL/'results/f1700-variant'),'--peer-root',str(XL/'results/f1700-baseline'),'--hard-s','1240','--',sys.executable,'-B',str(XL/'bench/run.py'),'--maps',row['maplist'],'--method',str(method)+':propose','--finish',str(method)+':finish','--out',str(out),'--budget-s','240','--finalize-s','60','--hard-s','1200','--memory-gb','56','--tries','1','--seed','0','--resume']
        print(f"QUEUE ordinal{ordinal} {arm} cold {row['input']}",flush=True)
        proc=subprocess.Popen(cmd,cwd=ROOT,env=dict(os.environ));record.update(active=dict(ordinal=ordinal,arm=arm,owned_outer_guard_pid=proc.pid,command=cmd));write(a.record,record)
        code=proc.wait();after=drift(snapshot)
        for path in out.glob('*.json'):
            result=json.loads(path.read_text())
            if result.get('input')==row['input']and'recorded_pids'in result:
                result.update(input_source_stable=not after,input_snapshot=str(a.snapshot))
                if after:result.update(source_stable=False,passed=False,completed=None,status='input_drift',input_drift=after)
                write(path,result)
        done.append(dict(ordinal=ordinal,arm=arm,outer_returncode=code,input_source_stable=not after));record['active']=None;write(a.record,record)
        if after:record.update(status='input_drift',drift=after);write(a.record,record);return 42
        if code:record.update(status='guard_or_supervisor_stopped');write(a.record,record);return code
    record.update(status='complete',finished_unix=time.time());write(a.record,record);return 0
if __name__=='__main__':sys.exit(main())
