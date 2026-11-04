"""Live qualification guard using the production whole-file AST hash scheme.

Historical run guards remain immutable evidence. New runs copy this file to
<run>/closure/guard.py, or pass --directory explicitly. Non-code input bytes
and evidence provenance pins remain exact; numerical Python ignores only
comments and leading docstrings, never declarations or executable code.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import tempfile
import time
import tomllib

HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'pyproject.toml').exists())


def directory(value=None):
    if value is not None:return Path(value).resolve()
    if HERE.name!='closure':raise ValueError('pass --directory <fresh-run>/closure')
    return HERE


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False) as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n');name=stream.name
    os.replace(name,path)


def raw_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dependencies():
    # Reload source on each check; a long-lived watcher must not retain an old
    # selector map or hashing implementation after a real source edit.
    return runpy.run_path(str(ROOT/'src/stowage/linear/dependencies.py'))


def code_hash(path,deps=None,symbols=None):
    deps=deps or dependencies()
    return deps['_semantic_hash'](Path(path).read_bytes(),symbols)


def catalogue_inputs():
    # This is a temporary campaign consistency guard, not an expansion of the
    # permanent numerical dependency closure. Mixed schema/data workers cannot
    # provide valid public-checker evidence even when the GM value is unchanged.
    catalogue=tomllib.loads((ROOT/'src/stowage/rules/catalogue.toml').read_text())
    rules=[rule for rule in catalogue['rules'] if rule.get('id')=='iscode.a2_2.initial_gm']
    if len(rules)!=1:raise ValueError('consumed initial GM rule missing or duplicated')
    minimum=rules[0]['parameters']['minimum'];value=float(minimum['value'])
    if minimum.get('unit')!='m' or not math.isfinite(value) or value<=0:
        raise ValueError('invalid consumed initial GM minimum')
    canonical=json.dumps(catalogue,sort_keys=True,separators=(',',':'),allow_nan=False)
    return {'consumed_initial_gm_m':value,'runtime_catalogue':hashlib.sha256(canonical.encode()).hexdigest()}


def snapshot():
    deps=dependencies();profiles={};files={}
    for name,selectors in deps['_PROFILES'].items():
        sources={path:code_hash(ROOT/'src/stowage'/path,deps,symbols)
                 for path,symbols in selectors.items()}
        files.update({'src/stowage/'+path:value for path,value in sources.items()})
        profiles[name]=dict(fingerprint=deps['dependency_fingerprint'](name),sources=sources,
                            versions=deps['builder_versions'](name))
    for name in ('dependencies.py','cache.py','margin_policy.py','boundary_calibration.py','qualification.py'):
        path='src/stowage/linear/'+name;files[path]=code_hash(ROOT/path,deps)
    # Bind the driver/tool graph as well as the numerical closure. Historical
    # guards are not edited; fresh copies use this implementation at closure/.
    owner=ROOT/'experiments/linear-model/epoch_requalification'
    producers=set(owner.glob('*.py'))|set(owner.parent.glob('*.py'))|{Path(__file__).resolve()}
    for name in ('claude-edge-cause.py','claude-face-pilot.py','claude-face-budget-pilot.py',
                 'claude-freeze-review-v2.py','d49_holdout_audit.py','claude_edge_audit.py',
                 'claude-prior-holdout-gate.py','claude_audit_inputs.py'):
        producers.add(owner/'production-1028b'/name)
    producers.add(ROOT/'src/stowage/rules/catalogue.py')
    for path in sorted(producers):files[str(path.relative_to(ROOT))]=code_hash(path,deps)
    inputs={}
    for path in sorted((ROOT/'params/fleet').glob('*.toml')):
        inputs[str(path.relative_to(ROOT))]=raw_hash(path)
        params=tomllib.loads(path.read_text())
        form=params.get('hull_form',{})
        offset=form.get('offsets_path')
        if isinstance(offset,dict):offset=offset.get('value')
        if offset:
            source=Path(offset);source=source if source.is_absolute() else ROOT/source
            inputs[str(source.resolve().relative_to(ROOT))]=raw_hash(source)
    return dict(hash_scheme_version=deps['LINEAR_DEPENDENCY_HASH_SCHEME_VERSION'],
        captured_utc=datetime.now(timezone.utc).isoformat(),profiles=profiles,
        python_sources=files,input_bytes=inputs,runtime_inputs=catalogue_inputs(),
        source_hashing='whole parsed AST without leading module/class/function docstrings; attributes excluded')


def check(*,abort=True,output=None,_bootstrap=False):
    home=directory(output);baseline={};errors={}
    try:
        baseline=json.loads((home/'start.json').read_text())
        current=snapshot()
        if baseline['hash_scheme_version']!=current['hash_scheme_version']:
            errors['@hash_scheme_version']=dict(before=baseline['hash_scheme_version'],after=current['hash_scheme_version'])
        for group in ('python_sources','input_bytes','profiles','runtime_inputs'):
            before,after=baseline[group],current[group]
            for key in before.keys()|after.keys():
                if before.get(key)!=after.get(key):errors[group+'/'+key]=dict(before=before.get(key),after=after.get(key))
        producer_manifest=home/'run-producers.json'
        finalization=home/'finalized.json'
        if not _bootstrap:
            receipt=json.loads(finalization.read_text())
            if receipt['start_sha256']!=raw_hash(home/'start.json'):
                raise ValueError('captured baseline differs from finalized producer freeze')
            if receipt['producer_manifest_sha256']!=raw_hash(producer_manifest):
                raise ValueError('finalized producer manifest changed')
        if producer_manifest.exists():
            manifest=json.loads(producer_manifest.read_text());deps=dependencies()
            if manifest['hash_scheme_version']!=current['hash_scheme_version']:
                raise ValueError('producer manifest hash scheme differs; choose a fresh run')
            for name,expected in manifest['semantic_sources'].items():
                path=ROOT/name;actual=code_hash(path,deps) if path.suffix=='.py' else raw_hash(path)
                if actual!=expected:errors['producer/'+name]=dict(before=expected,after=actual)
    except Exception as exc:
        # A malformed live dependency module may also fail while being loaded.
        # Every failed check must emit the shared stop sentinel, never merely
        # kill the watcher while leaving admitted workers unaware.
        errors['@invalid_source_or_baseline']=repr(exc)
    result=dict(passed=not errors,checked_utc=datetime.now(timezone.utc).isoformat(),
                hash_scheme_version=baseline.get('hash_scheme_version'),strict_changes=errors)
    write(home/'latest-check.json',result)
    if errors and abort:write(home.parent/'ABORT',dict(reason='qualification AST/input drift or invalid source',**result))
    return result


def assert_current():
    result=check()
    if not result['passed'] or (directory().parent/'ABORT').exists():
        raise RuntimeError('qualification source guard failed; stop and report')
    return result


def capture(output=None):
    home=directory(output);home.mkdir(parents=True,exist_ok=True);state=snapshot()
    with (home/'start.json').open('x') as stream:json.dump(state,stream,indent=2);stream.write('\n')
    return state


def freeze_producers(paths,output=None):
    home=directory(output)
    if (home/'run-producers.json').exists() or (home/'finalized.json').exists():
        raise FileExistsError('producer freeze is immutable; use a fresh run')
    # Capture alone is never admitted: only this private bootstrap check may
    # run without immutable producer membership and its finalization receipt.
    if not check(output=home,_bootstrap=True)['passed']:raise ValueError('source guard failed before producer freeze')
    deps=dependencies();sources={};semantic={}
    for value in paths:
        path=Path(value).resolve();name=str(path.relative_to(ROOT));sources[name]=raw_hash(path)
        semantic[name]=code_hash(path,deps) if path.suffix=='.py' else sources[name]
    record=dict(hash_scheme_version=deps['LINEAR_DEPENDENCY_HASH_SCHEME_VERSION'],sources=sources,semantic_sources=semantic)
    with (home/'run-producers.json').open('x') as stream:json.dump(record,stream,indent=2);stream.write('\n')
    receipt=dict(start_sha256=raw_hash(home/'start.json'),
                 producer_manifest_sha256=raw_hash(home/'run-producers.json'))
    with (home/'finalized.json').open('x') as stream:json.dump(receipt,stream,indent=2);stream.write('\n')
    if not check(output=home)['passed']:raise ValueError('source guard failed after producer freeze')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--capture',action='store_true');group.add_argument('--check',action='store_true')
    group.add_argument('--watch',action='store_true');group.add_argument('--freeze-run-producers',nargs='+')
    args=parser.parse_args();home=directory(args.directory)
    if args.capture:capture(home)
    elif args.freeze_run_producers:freeze_producers(args.freeze_run_producers,home)
    elif args.check:
        result=check(output=home);print(json.dumps(result));raise SystemExit(0 if result['passed'] and not (home.parent/'ABORT').exists() else 1)
    else:
        while not (home/'STOP_WATCHER').exists():
            result=check(output=home)
            if not result['passed'] or (home.parent/'ABORT').exists():raise SystemExit(1)
            time.sleep(5)

if __name__=='__main__':main()
