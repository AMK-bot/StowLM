"""Install reviewed recovery code only after old controller/runner/GPU release.

CPU-only; never launches a process or changes another executive's source files.
D129 permits the five reviewed production changes recorded in SOURCE_DRIFT_D124.
Original generation evidence and any unadmitted old children remain untouched.
"""
from contextlib import ExitStack,contextmanager
from datetime import datetime,timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

TARGETS=('checker_watchdog','outcomes','selection','preferences','metrics','fitness',
         'audit','runner','progress','code_epoch','final_recipe','reverify','salvage',
         'finisher_outcomes','control')
EXTRAS=('salvage_prepare.py','deploy_when_ready.py','apply_recovery.py')
APPROVED_DRIFT={'src/llm/stowlm/backtrack.py','src/llm/stowlm/planner.py',
    'src/stowage/checker/rules_special.py','src/stowage/vessel/fleet_manual.py',
    'src/stowage/vessel/fleet_manual_witness.py'}
D126_TARGET='src/stowage/checker/stability.py'
D126_BEFORE='72c4ec7390b541dd5dd397b1a18c433547f1851b04a49b8cd49589f7da4a2e97'
D126_AFTER='64c0a06761da330b5ab78efb2776c8cea12d37225f8059f581893dddfa5410e5'



def sha(content):return hashlib.sha256(content).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def digest(value):return sha(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
def bundle(stage):
    stage=Path(stage);values={}
    for name in TARGETS:
        path=stage/(name+'.py')
        if not path.exists():path=stage.parent/'d124'/path.name
        values['src/llm/exit/'+name+'.py']=path.read_bytes()
    values['experiments/mvp/exit/deploy_when_ready.py']=(stage/'deploy_when_ready.py').read_bytes()
    for name in EXTRAS:values['staged/'+name]=(stage/name).read_bytes()
    return values,digest({p:sha(v) for p,v in values.items()})


@contextmanager
def released(path):
    if not Path(path).exists():raise RuntimeError('missing owner lock: '+str(path))
    with Path(path).open('r+') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise RuntimeError('old owner active: '+str(path)) from error
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def atomic(path,content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.d129.tmp')
    with temp.open('wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
    temp.replace(path)


def apply(root,source_out,expected_sha256):
    root,source_out=Path(root).resolve(),Path(source_out).resolve()
    stage=root/'experiments/mvp/exit/patches/d129';contents,bundle_hash=bundle(stage)
    if bundle_hash!=expected_sha256:raise RuntimeError('reviewed recovery bundle changed')
    if not (source_out/'deployment.cancel').is_file():raise RuntimeError('old cancellation must remain armed')
    with ExitStack() as locks:
        for path in (source_out/'.deployment.lock',source_out/'.runner.lock',
                     root/'experiments/mvp/exit/.gpu.lock'):
            locks.enter_context(released(path))
        old=read(source_out/'gen-000/instances.json')
        for index in range(len(old['instances'])):
            if not (source_out/'gen-000/cases'/f'{index:03d}'/'selection.json').is_file():
                raise RuntimeError('selection is incomplete; no recovery activation')
        # Validate all non-owned drift before writing even one owned source file.
        pins={str(p.relative_to(root)):sha(p.read_bytes()) for base in ('src/llm','src/stowage')
              for p in (root/base).rglob('*') if p.is_file() and p.suffix in ('.py','.json')
              and '__pycache__' not in p.parts}
        owned={p for p in contents if p.startswith('src/llm/exit/')}
        changes={p for p in pins.keys()|old['source_pins'].keys()
                 if pins.get(p)!=old['source_pins'].get(p)}
        nonowned=changes-owned
        approved=read(source_out/'SOURCE_DRIFT_D124.json')['files']
        optional=set()
        if D126_TARGET in nonowned:
            receipt=read(root/'experiments/mvp/fixwindow/d126/watchdog-application/RECEIPT.json')
            proofs=[r for r in receipt.get('preflight',{}).get('files',[]) if r.get('target')==D126_TARGET]
            if (receipt.get('decision')!='D126' or receipt.get('applied') is not True
                    or receipt.get('passed') is not True or len(proofs)!=1
                    or proofs[0].get('before')!=D126_BEFORE or proofs[0].get('after')!=D126_AFTER
                    or old['source_pins'].get(D126_TARGET)!=D126_BEFORE
                    or pins.get(D126_TARGET)!=D126_AFTER):
                raise RuntimeError('D126 watchdog receipt/source mismatch')
            optional.add(D126_TARGET)
        if not nonowned.issubset(APPROVED_DRIFT|optional):
            raise RuntimeError('unreviewed non-owned drift: '+repr(sorted(nonowned-APPROVED_DRIFT-optional)))
        if not APPROVED_DRIFT.issubset(approved):
            raise RuntimeError('reviewed drift inventory is incomplete')
        for path in APPROVED_DRIFT:
            if pins.get(path)!=approved[path]['current_sha256']:
                raise RuntimeError('reviewed production file changed again: '+path)
        backup=source_out/'d129-original-source-backup'
        bindings={}
        for path in owned:
            previous=old['source_pins'].get(path); new=sha(contents[path])
            if pins.get(path) not in (previous,new):raise RuntimeError('unknown owned source bytes: '+path)
            original=backup/path
            if previous is not None:
                if original.exists():
                    if sha(original.read_bytes())!=previous:raise RuntimeError('unknown original backup: '+path)
                elif pins.get(path)==previous:atomic(original,(root/path).read_bytes())
                else:raise RuntimeError('partial activation has no original backup: '+path)
            bindings[path]=dict(old_sha256=previous,new_sha256=new)
        # No gen0 summary, source-pin replacement, survivor or fake boundary is written.
        for path,content in contents.items():
            if path.startswith('staged/'):continue
            atomic(root/path,content)
        if bundle(stage)[1]!=bundle_hash:raise RuntimeError('staging changed during activation')
        projected={**pins,**{p:sha(contents[p]) for p in owned}}
        final_pins={str(p.relative_to(root)):sha(p.read_bytes()) for base in ('src/llm','src/stowage')
              for p in (root/base).rglob('*') if p.is_file() and p.suffix in ('.py','.json')
              and '__pycache__' not in p.parts}
        if final_pins!=projected:raise RuntimeError('production source changed during activation; no ready state')
        record=dict(schema='exit-d129-code-activation-v1',authorization='D124+D128+D129',
            utc=datetime.now(timezone.utc).isoformat(),reviewed_bundle_sha256=bundle_hash,
            original_generation_source=old['source_pins'],targets=bindings,
            nonowned_reviewed_drift={p:pins[p] for p in sorted(nonowned)},
            gpu_started=False,next_step='fresh CPU salvage preparation, then leader host launch')
        atomic(stage/'activation-manifest.json',(json.dumps(record,indent=2)+'\n').encode())
        return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--source-out',type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--print-digest',action='store_true')
    args=parser.parse_args()
    if args.print_digest:print(bundle(args.root/'experiments/mvp/exit/patches/d129')[1]);return
    if not args.source_out or not args.expected_sha256:parser.error('source output and reviewed digest required')
    record=apply(args.root,args.source_out,args.expected_sha256)
    print(json.dumps(dict(status='applied',sha256=record['reviewed_bundle_sha256'],gpu_started=False)))
if __name__=='__main__':main()
