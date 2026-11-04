"""D187 sealed test inputs: generation/D58 only; never run a loading planner."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

import build
import NEWtest_v3_epoch as epoch
import test_v3_identity as identity

TOOLS = Path(__file__).resolve().parent
OUT = build.CORPUS/'test-v3'
EPOCH = TOOLS/'test_v3_epoch_D187'


def snapshot_sources():
    package = Path(next(iter(importlib.util.find_spec('stowage').submodule_search_locations)))
    if package.resolve() != (EPOCH/'src/stowage').resolve():
        raise RuntimeError('wrong imported epoch: '+str(package))
    return {prefix+'/'+str(p.relative_to(directory)):build.sha256(p)
        for prefix,directory in (('src/stowage',package),('params',EPOCH/'params'))
        for p in sorted(directory.rglob('*')) if p.is_file() and p.suffix in {'.py','.json','.toml'}}


def path_for(spec, root=OUT):
    return Path(root)/spec['rung']/spec['vessel']/spec['generator']/f"seed-{spec['seed']:09d}.json"


def canonical_sources(pin):
    return {p:h for p,h in pin['source_sha256'].items()
            if p.startswith(('src/stowage/','params/'))}


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'manifest.json').exists():
        raise RuntimeError('sealed release already exists; no generation permitted')
    build.atomic_json(OUT/'SEALED.json',dict(status='generating_sealed',decision='D187/D174',
        planner_runs=0,plan_quality_measured=False,policy='No planner or evaluation; generator validity checks only'))
    pin=epoch.snapshot(EPOCH)
    specs=identity.make_specs(build.CORPUS)
    excluded=identity.exclusion_inventory(build.CORPUS,OUT)
    identity.assert_disjoint(specs,excluded)
    request=dict(format='stowlm-test-v3-request-v1',specs=specs,counts={'tiny':15,'small':30},
        source_pin_sha256=build.sha256(EPOCH/'source-pin.json'),
        exclusion_proof=excluded['proof'],
        assumption='Explicit D187 count 15 tiny +30 small supersedes v2.1 actual 15+15; each small stratum repeated with reversed odd family slot')
    p=OUT/'requested.json'
    if p.exists():
        existing=json.loads(p.read_text())
        if existing['specs']!=specs or existing['source_pin_sha256']!=request['source_pin_sha256']:
            raise RuntimeError('cannot replace admitted sealed identities or epoch')
    else:
        build.atomic_json(p,request)
    return specs


def worker(index):
    if (OUT/'manifest.json').exists():
        raise RuntimeError('sealed release cannot be reopened')
    request=json.loads((OUT/'requested.json').read_text())
    assert build.sha256(EPOCH/'source-pin.json')==request['source_pin_sha256']
    pin=json.loads((EPOCH/'source-pin.json').read_text())
    actual=snapshot_sources()
    assert actual==canonical_sources(pin), 'producer epoch changed'
    for relative,digest in pin['source_sha256'].items():
        if relative not in actual:
            assert build.sha256(EPOCH/relative)==digest, 'physical/runtime input changed'
    spec=request['specs'][index]
    build.source_snapshot=snapshot_sources
    build.shard_path=path_for
    build.POLICY='sealed-test-v3-familiar-families-disjoint-root-seeds'
    result=build.generate_one((spec,str(OUT),actual))
    print(json.dumps(result,sort_keys=True),flush=True)
    if result['status']=='failed':
        raise SystemExit(2)


def seal(specs,started):
    # Only byte hashes are read here; map payloads are never parsed or evaluated.
    pin=epoch.verify(EPOCH)
    excluded=identity.exclusion_inventory(build.CORPUS,OUT)
    records=[]
    for spec in specs:
        path=path_for(spec)
        audit_path=path.with_suffix('.audit.json')
        audit=json.loads(audit_path.read_text())
        assert build.sha256(path)==audit['instance_sha256'], 'raw bytes changed'
        assert audit['source_files']==canonical_sources(pin), 'wrong producer epoch'
        assert audit['config']==spec and audit['accepted'] is True
        assert audit['rule_freeze_v1_enabled'] is False and audit['c2_screened'] is False
        assert audit['background_checks']>0 and audit['background_last']['accepted'] is True
        records.append(dict(path=str(path.relative_to(OUT)),sha256=audit['instance_sha256'],
            audit_path=str(audit_path.relative_to(OUT)),audit_sha256=build.sha256(audit_path),
            seed=spec['seed'],ship=spec['vessel'],rung=spec['rung'],family=spec['generator'],
            config=spec,versions=audit['instance_versions'],schema_version=audit['schema_version'],
            source_files=audit['source_files'],stage_environment=audit['stage_environment'],
            background_physics_sha256=audit['background_last']['physics_sha256'],
            generator_gate_passed=True,plan_checked=False,generation_seconds=audit['elapsed_generation_s']))
    identity.assert_disjoint(specs,excluded,[r['sha256'] for r in records])
    assert len(records)==45
    counts=Counter((r['ship'],r['rung'],r['family']) for r in records)
    manifest=dict(format='stowlm-sealed-test-v3-v1',sealed=True,status='sealed',decision='D187/D174',
        created_at_utc=datetime.now(timezone.utc).isoformat(),count=45,counts={'tiny':15,'small':30},
        counts_per_ship_rung_family=[dict(ship=k[0],rung=k[1],family=k[2],count=v) for k,v in sorted(counts.items())],
        seed_ranges={'tiny':[187000000,187000014],'small':[187100000,187100029]},
        rule_freeze_v1_enabled=False,global_screen=0,planner_runs=0,plan_quality_measured=False,
        generator_checks_only=True,source_pin_path=str(EPOCH/'source-pin.json'),
        source_pin_sha256=build.sha256(EPOCH/'source-pin.json'),
        acceleration_receipt_sha256=build.sha256(EPOCH/'vessel-cache-acceleration.json'),
        python=sys.version,epoch_source_files=pin['source_sha256'],
        runtime_environment={k:os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','STOWAGE_BALLAST_V4')},
        elapsed_wall_seconds=time.monotonic()-started,identity_exclusion_proof=excluded['proof'],
        assumptions=[json.loads((OUT/'requested.json').read_text())['assumption']],files=records)
    build.atomic_json(OUT/'SEALED.json',dict(status='sealed',sealed_at_utc=manifest['created_at_utc'],
        planner_runs=0,plan_quality_measured=False,policy='No planner, solve, tuning, diagnosis or plan-quality computation authorized'))
    readme='''# Test v3 — SEALED (D187 / D174)

45 fresh synthetic maps: **15 tiny +30 small** across feeder_500, feeder_800 and
feeder_1700. Each ship has five tiny maps and ten small maps; small is balanced
five random-yard/five simulated-yard per ship. No medium or full-ship map.

**Sealed:** no loading planner, solving, plan quality, diagnosis or tuning has
been run or is authorized on these maps. Only the frozen generator's ordinary
validity checks ran: necessary screens, D58 fixed-background witness checks,
clean provenance and serialization round-trip. D58's internal background ballast
witness is generator acceptance, not a loading plan for these target boxes.
No loading-plan feasibility or quality is claimed.

Tiny seeds: **187000000–187000014**. Small: **187100000–187100029**.
Identity guards cover current and archived train/val/test/showcase metadata,
including requested-but-unemitted and rejected showcase seeds; raw map hashes
are also disjoint. Existing raw maps were not opened for these guards.

Current code/parameters/hull inputs were copied into a hash-pinned immutable
epoch; existing numerical acceleration artifacts retain the normal generator
qualification checks. The source pin and each audit bind actual inputs/runtime.
Rule-freeze OFF; C2 global screen OFF; generation uses legacy ballast environment.
Small ship slack/mass settings: 500=.25/.55, 800=.25/.45, 1700=.35/.45.

Assumption: the explicit D187 **15+30** count takes precedence over v2.1's actual
15+15. Small repeats each old stratum with the odd family assignment reversed.
The shared ship/family strata measure fresh familiar-family draws.

`manifest.json` lists identities, source/schema versions and checks. `SHA256SUMS`
binds raw maps, audits and release metadata. Files become read-only after sealing.
Do not unseal or run a planner without a separate leader order.
'''
    (OUT/'README.md').write_text(readme)
    build.atomic_json(OUT/'manifest.json',manifest)
    paths=[OUT/'manifest.json',OUT/'README.md',OUT/'SEALED.json',OUT/'requested.json']
    digests={str(p.relative_to(OUT)):build.sha256(p) for p in paths}
    for r in records:
        digests[r['path']]=r['sha256'];digests[r['audit_path']]=r['audit_sha256']
    (OUT/'SHA256SUMS').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(digests.items())))
    for p in paths+[OUT/'SHA256SUMS']+[OUT/r[k] for r in records for k in ('path','audit_path')]:
        p.chmod(0o444)
    print(json.dumps(dict(status='sealed',count=45,manifest_sha256=build.sha256(OUT/'manifest.json'),
        SHA256SUMS_sha256=build.sha256(OUT/'SHA256SUMS'),elapsed_wall_s=time.monotonic()-started)),flush=True)


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--worker',type=int)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--seal',action='store_true')
    args=parser.parse_args()
    if args.worker is not None:
        worker(args.worker);return
    started=time.monotonic()
    state=dict(pid=os.getpid(),started_at_utc=datetime.now(timezone.utc).isoformat(),workers=1,gpu=False,requested_nice=10)
    try:os.setpriority(os.PRIO_PROCESS,0,10)
    except OSError as error:state['nice_error']=str(error)
    state['effective_nice']=os.getpriority(os.PRIO_PROCESS,0)
    build.atomic_json(TOOLS/'v3-runtime.json',state)
    specs=prepare()
    if args.prepare:return
    env=dict(os.environ,PYTHONPATH=str(EPOCH/'src')+os.pathsep+str(build.ROOT/'src'),
        OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',MKL_NUM_THREADS='1',
        TOKENIZERS_PARALLELISM='false')
    env.pop('STOWAGE_BALLAST_V4',None)
    count=0
    for i,spec in enumerate(specs):
        if path_for(spec).with_suffix('.audit.json').exists():continue
        if args.limit is not None and count>=args.limit:break
        with (TOOLS/'v3-generation.log').open('a') as output:
            child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',str(i)],env=env,
                                   stdout=output,stderr=subprocess.STDOUT)
            state.update(child_pid=child.pid,index=i,seed=spec['seed'])
            build.atomic_json(TOOLS/'v3-runtime.json',state)
            code=child.wait()
        state.update(last_exit_code=code,elapsed_wall_s=time.monotonic()-started)
        build.atomic_json(TOOLS/'v3-runtime.json',state)
        if code:raise SystemExit(code)
        count+=1
    if args.seal:seal(specs,started)
    else:print(json.dumps({'generated_this_run':count,'elapsed_wall_s':time.monotonic()-started}),flush=True)


if __name__=='__main__':main()
