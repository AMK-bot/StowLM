"""Apply reviewed D124 files only after generation0 and all owners release.

CPU only: this command neither starts a controller nor imports MLX. The existing
deployment.cancel must remain present. The owner restarts from a GPU-capable host
only after the new code/adapter smoke succeeds under the usual admission gates.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

TARGETS=('checker_watchdog','outcomes','selection','preferences','metrics',
         'fitness','audit','runner','progress','code_epoch','final_recipe')
SCHEMA='exit-d124-activation-v1'


def sha(content):return hashlib.sha256(content).hexdigest()


def stable_digest(value):
    return sha(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode())


def staged_bundle(staging):
    staging=Path(staging)
    names=[name+'.py' for name in TARGETS]+['boundary_handoff.py']
    contents={name:(staging/name).read_bytes() for name in names}
    return contents,stable_digest({name:sha(content) for name,content in contents.items()})


def loaded_module(name,content,path):
    result=ModuleType(name);result.__file__=str(path)
    exec(compile(content,str(path),'exec'),result.__dict__)
    return result


def atomic_bytes(path,content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.d124.tmp')
    with temporary.open('wb') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)


def ensure_cancel(out):
    if not (out/'deployment.cancel').is_file():
        raise RuntimeError('deployment.cancel must stay present throughout D124 activation')


def subprocess_commit(root,out,boundary,approved_changes,gens):
    staging=root/'experiments/mvp/exit/patches/d124'
    command=[sys.executable,str(staging/'boundary_handoff.py'),'--root',str(root),
             '--out',str(out),'--boundary',str(boundary),'--gens',str(gens)]
    for path in approved_changes:command+=['--approved-change',path]
    environment=dict(os.environ)
    environment['PYTHONPATH']=str(root/'src')+os.pathsep+environment.get('PYTHONPATH','')
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                 'VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):environment[name]='1'
    result=subprocess.run(command,cwd=root,env=environment,text=True,capture_output=True,timeout=60)
    atomic_bytes(out/'d124-boundary.log',(result.stdout+result.stderr).encode())
    if result.returncode:
        raise RuntimeError(f'fresh CPU boundary commit failed ({result.returncode}); see d124-boundary.log')


def apply(root,out,*,expected_staged_sha256,gens=8,committer=None):
    """Recover only known old/new bytes; reject drift before any source write."""
    root,out=Path(root).resolve(),Path(out).resolve()
    staging=root/'experiments/mvp/exit/patches/d124'
    contents,bundle_sha=staged_bundle(staging)
    if bundle_sha!=expected_staged_sha256:
        raise RuntimeError('staged bundle differs from the reviewed SHA-256; no activation')
    helper=loaded_module('d124_handoff',contents['boundary_handoff.py'],staging/'boundary_handoff.py')
    epoch=loaded_module('d124_epoch',contents['code_epoch.py'],root/'src/llm/exit/code_epoch.py')
    epoch.ROOT=root
    boundary=out/'epoch-boundary.json'
    backup=out/'d124-activation-backup'
    ensure_cancel(out)
    # The controller lock remains held through the fresh CPU subprocess. Runner
    # and GPU locks protect every source mutation, then the child acquires them
    # independently before committing the restart record.
    with helper.released_lock(out/'.deployment.lock'):
        with ExitStack() as owners:
            owners.enter_context(helper.released_lock(out/'.runner.lock'))
            owners.enter_context(helper.released_lock(root/'experiments/mvp/exit/.gpu.lock'))
            ensure_cancel(out)
            manifest=helper.read(out/'gen-000/instances.json')
            old=manifest['source_pins']
            current=epoch.source_snapshot(root)
            target_bindings={}
            projected=dict(current)
            for name in TARGETS:
                path=f'src/llm/exit/{name}.py'
                previous,new=old.get(path),sha(contents[name+'.py'])
                if current.get(path) not in (previous,new):
                    raise RuntimeError(f'unknown current target bytes: {path}')
                if previous==new:
                    raise RuntimeError(f'staged target has no D124 change: {path}')
                target_bindings[path]=dict(old_sha256=previous,new_sha256=new)
                projected[path]=new
            changed=sorted(path for path in old.keys()|projected.keys() if old.get(path)!=projected.get(path))
            unknown=[path for path in changed if path not in target_bindings
                     and (path not in projected or not epoch._speed_path(path))]
            if unknown:raise RuntimeError(f'unapproved non-target source drift: {unknown}')
            draft=helper.prepare(out,current_pins=projected,approved_changes=changed,gens=gens)
            activation=dict(schema=SCHEMA,authorization='D124',targets=target_bindings)
            activation_path=staging/'activation-manifest.json'
            if activation_path.exists() and helper.read(activation_path)!=activation:
                raise RuntimeError('existing activation manifest differs from committed generation0/staged files')
            existing=None
            if boundary.exists():
                existing=helper.read(boundary)
                payload={key:value for key,value in existing.items() if key!='state_sha256'}
                if (existing.get('schema')!=epoch.SCHEMA or existing.get('phase')!='between_generations'
                        or existing.get('exit_code')!=75 or existing.get('state_sha256')!=epoch._digest(payload)
                        or existing.get('next_generation')!=1 or existing.get('loaded_pins')!=old
                        or existing.get('population')!=draft['population']):
                    raise RuntimeError('existing boundary is not the committed generation0 handoff')
                if existing.get('plan',{}).get('kind')=='mask_speedup':
                    epoch.restore_boundary(boundary,existing['current_pins'])
                elif (existing.get('plan',{}).get('kind')!='checker_watchdog'
                        or existing.get('current_pins')!=projected or existing.get('args')!=draft['args']):
                    raise RuntimeError('existing D124 boundary differs from proposed handoff')
                else:
                    if not activation_path.exists():
                        raise RuntimeError('existing D124 boundary has no activation manifest')
                    epoch.restore_boundary(boundary,projected)
            # Complete all refusal checks before publishing the authorization or
            # touching any live source. Save original bytes for every old file.
            for path,binding in target_bindings.items():
                original=backup/path
                if binding['old_sha256'] is None:continue
                if original.exists():
                    if sha(original.read_bytes())!=binding['old_sha256']:
                        raise RuntimeError(f'original backup has unknown bytes: {path}')
                elif current.get(path)==binding['old_sha256']:
                    atomic_bytes(original,(root/path).read_bytes())
                else:raise RuntimeError(f'partially applied target lacks its original backup: {path}')
            ensure_cancel(out)
            atomic_bytes(activation_path,(json.dumps(activation,sort_keys=True,indent=2)+'\n').encode())
            for path,binding in target_bindings.items():
                ensure_cancel(out)
                if current.get(path)!=binding['new_sha256']:
                    atomic_bytes(root/path,contents[Path(path).name])
            if epoch.source_snapshot(root)!=projected:
                raise RuntimeError('source changed during D124 activation; no boundary commit')
            if existing and existing['plan']['kind']=='mask_speedup':
                archive=out/f"epoch-boundary.pre-d124.{existing['state_sha256'][:16]}.json"
                if archive.exists():
                    if archive.read_bytes()!=boundary.read_bytes():raise RuntimeError('boundary archive differs')
                    boundary.unlink()
                else:boundary.replace(archive)
                existing=None
            if existing:
                if staged_bundle(staging)[1]!=expected_staged_sha256:
                    raise RuntimeError('staged bundle changed during activation')
                epoch.restore_boundary(boundary,projected)
                return dict(status='already_applied',boundary=str(boundary),staged_sha256=bundle_sha)
        ensure_cancel(out)
        if staged_bundle(staging)[1]!=expected_staged_sha256:
            raise RuntimeError('staged bundle changed before the fresh CPU boundary commit')
        (committer or subprocess_commit)(root,out,boundary,changed,gens)
        ensure_cancel(out)
        restored=epoch.restore_boundary(boundary,epoch.source_snapshot(root))
        if (restored['next_generation']!=1 or restored['population']!=draft['population']
                or restored['args']!=draft['args']):
            raise RuntimeError('fresh boundary failed committed generation0 state validation')
        return dict(status='applied',boundary=str(boundary),staged_sha256=bundle_sha)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--gens',type=int,default=8)
    parser.add_argument('--expected-staged-sha256')
    parser.add_argument('--print-staged-digest',action='store_true')
    args=parser.parse_args()
    if args.print_staged_digest:
        print(staged_bundle(args.root/'experiments/mvp/exit/patches/d124')[1]);return 0
    if args.out is None or not args.expected_staged_sha256:
        parser.error('--out and --expected-staged-sha256 required for activation')
    print(json.dumps(apply(args.root,args.out,expected_staged_sha256=args.expected_staged_sha256,gens=args.gens)))
    return 0


if __name__=='__main__':raise SystemExit(main())
