"""Admitted, one-lane offline README rehearsal; stop only recorded owned PIDs."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPORT = HERE / 'readme-run-v5.json'
LOGS = HERE / 'readme-run-v5-logs'


def main():
    LOGS.mkdir(exist_ok=True)
    snapshot = json.loads((HERE/'generated-v5/closure.json').read_text())
    scratch = Path(tempfile.mkdtemp(prefix='stowlm-v5-readme-', dir='/private/tmp'))
    tree = scratch/'proposed'
    tree.mkdir()
    state = dict(scratch=str(scratch), tree=str(tree), started_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        manifest_sha256=hashlib.sha256((HERE/'candidate-paths-v5.txt').read_bytes()).hexdigest(), jobs=[],
        copy_errors=[], snapshot_paths=len(snapshot['rows']), copy_count=0,
        limitations=['Offline constrained variants of README commands, no network/GPU/model inference.',
            'No Git clone permitted: explicit manifest copied into empty scratch directory.',
            'Cache-seeded phase uses local third-party download cache only; no source environment/data/models borrowed.',
            'GPU imports and external network are blocked; sealed test-v3 reads are blocked.',
            'Command failure does not enumerate failures in code that never ran.'])
    def save(): REPORT.write_text(json.dumps(state, indent=2)+'\n')
    for row in snapshot['rows']:
        src=Path(snapshot['pinned_source'])/row['path']
        raw=src.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:
            state['copy_errors'].append(row['path']); continue
        dst=tree/row['path']; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(raw)
        state['copy_count']+=1
    # Root files are explicitly review-only substitutions, rather than claiming they exist in the source.
    substitutions={'README.md':ROOT/'experiments/mvp/README-draft.md',
        'LICENSE':HERE/'LICENSE.draft', 'NOTICE':HERE/'NOTICE.draft'}
    state['draft_root_substitutions']={}
    for dst,src in substitutions.items():
        raw=src.read_bytes(); (tree/dst).write_bytes(raw)
        state['draft_root_substitutions'][dst]=dict(source=src.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(raw).hexdigest())
    save()
    if state['copy_errors']:
        state['completed']=False; state['blocked']='Source changed after manifest; regenerate before running'; save(); return
    guard=scratch/'guard'; guard.mkdir()
    (guard/'sitecustomize.py').write_text('''import os,sys,json
from pathlib import Path
p=os.environ.get("STOWLM_OWNED_PID_FILE")
if p:
 with open(p,"a") as f:f.write(str(os.getpid())+"\\n")
def audit(event,args):
 if event=="import" and args[0].split(".")[0]=="mlx": raise RuntimeError("REHEARSAL_POLICY: GPU/MLX imports disabled")
 if event=="ctypes.dlopen" and args and "mlx" in str(args[0]).lower(): raise RuntimeError("REHEARSAL_POLICY: MLX library disabled")
 if event=="socket.connect": raise RuntimeError("REHEARSAL_POLICY: network disabled")
 if event=="open" and args and isinstance(args[0],(str,bytes)):
  name=os.fsdecode(args[0]).replace("\\\\","/")
  if any(part in {"test-v3","test_v3"} for part in name.split("/")): raise RuntimeError("REHEARSAL_POLICY: sealed test-v3 input disabled")
sys.addaudithook(audit)
''')
    env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ['TOKEN','SECRET','PASSWORD','API_KEY','CREDIT_READY'])
        and k not in ['VIRTUAL_ENV','PYTHONHOME','PYTHONPATH','UV_PROJECT_ENVIRONMENT','PYTEST_ADDOPTS']}
    env.update(UV_CACHE_DIR=str(tree/'.uv-cache'), PYTHONPATH=str(guard), PYTHONDONTWRITEBYTECODE='1',
        HF_HOME=str(scratch/'empty-hf'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1',
        NUMEXPR_NUM_THREADS='1', BLIS_NUM_THREADS='1', RAYON_NUM_THREADS='1', POLARS_MAX_THREADS='1',
        UV_CONCURRENT_BUILDS='1', UV_CONCURRENT_INSTALLS='1', UV_CONCURRENT_DOWNLOADS='1', TOKENIZERS_PARALLELISM='false')
    env['PATH']=':'.join(p for p in env.get('PATH','').split(':') if '.venv' not in p)
    def admission():
        deadline=time.monotonic()+180
        last={}
        while True:
            s=json.loads((ROOT/'experiments/fleet/core-state.json').read_text()); age=time.time()-s['t']
            last=dict(s, age_s=age, mask_cores=int(s['mask']).bit_count())
            if last['mask_cores']>=8 and s['load1']<=45 and 0<=age<120:return last
            if time.monotonic()>=deadline:return None
            time.sleep(2)
    def run(label,args,timeout=40,server=False):
        allowed=admission()
        row=dict(label=label,command=args,cwd='scratch/proposed',admission=allowed,owned_pids=[],status='blocked-no-admission')
        state['jobs'].append(row); save()
        if allowed is None:return
        pidfile=LOGS/(label+'.pids'); pidfile.write_text('')
        env['STOWLM_OWNED_PID_FILE']=str(pidfile)
        start=time.monotonic()
        with (LOGS/(label+'.stdout.txt')).open('w') as out, (LOGS/(label+'.stderr.txt')).open('w') as err:
            p=subprocess.Popen(args,cwd=tree,env=env,stdout=out,stderr=err)
            row.update(owned_pids=[p.pid], status='running'); save()
            timeout_hit=False
            try:p.wait(timeout=8 if server else timeout)
            except subprocess.TimeoutExpired:timeout_hit=True
            pids={p.pid}|{int(x) for x in pidfile.read_text().split()}
            row['owned_pids']=sorted(pids); save()
            if p.poll() is None:
                for pid in reversed(sorted(pids)):
                    try:os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    for pid in pids:
                        try:os.kill(pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                    p.wait()
        row.update(exit_code=p.returncode,wall_s=time.monotonic()-start,timed_out=timeout_hit,
            status='stopped-after-start-window' if server and timeout_hit else 'timeout' if timeout_hit else 'passed' if p.returncode==0 else 'failed',reaped=True)
        for kind in ['stdout','stderr']:
            log=LOGS/(label+'.'+kind+'.txt'); raw=log.read_text(errors='replace')
            raw=raw.replace(str(ROOT),'<source-checkout>').replace(str(Path.home()),'<home>')
            log.write_text(raw)
        save(); print(label,row['status'],row['exit_code'],round(row['wall_s'],2),flush=True)
    uv=shutil.which('uv')
    run('01-pristine-sync',[uv,'sync','--offline','--python','3.12'],timeout=45)
    run('02-pristine-data-setup',[uv,'run','--offline','--no-sync','python','-m','stowage.data_setup'])
    run('03-pristine-app',[uv,'run','--offline','--no-sync','python','-m','app'],server=True)
    run('04-pristine-pytest',[uv,'run','--offline','--no-sync','pytest','-q'])
    # Additional diagnostic, not a cold-download claim. Cache contents are third-party packages.
    cache=tree/'.uv-cache'
    def hardlink_or_copy(src,dst):
        try:os.link(src,dst)
        except OSError:shutil.copy2(src,dst)
    shutil.copytree(ROOT/'.uv-cache',cache,dirs_exist_ok=True,symlinks=True,
        copy_function=hardlink_or_copy,ignore=shutil.ignore_patterns('.lock','*.lock'))
    state['cache_seeded']=True; save()
    run('05-seeded-sync',[uv,'sync','--offline','--python','3.12','--link-mode','copy'],timeout=60)
    run('06-seeded-data-setup',[uv,'run','--offline','--no-sync','python','-m','stowage.data_setup'])
    run('07-seeded-app',[uv,'run','--offline','--no-sync','python','-m','app'],server=True)
    run('08-seeded-pytest',[uv,'run','--offline','--no-sync','pytest','-q'],timeout=60)
    state['not_executed']=[dict(command='uv run python -m stowage.data_setup --archive /path/to/kcs_offsets.ZIP',reason='README placeholder path; no archive distributed in candidate'),
        dict(command='{{VERIFIED_READER_COMMAND}}',reason='unresolved placeholder; GPU/inference prohibited'),
        dict(command='online uv sync / KCS download',reason='offline run constraint/network policy; upstream availability unverified')]
    state['completed']=True; save()
    print('receipt',REPORT.relative_to(ROOT),flush=True)


if __name__=='__main__':main()
