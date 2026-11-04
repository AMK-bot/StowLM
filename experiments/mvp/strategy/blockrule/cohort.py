#!/usr/bin/env python3
"""Freeze D185 inputs before any planner outcomes; generate only medium TRAIN.

Uses the unmodified MVP corpus builder, including producer and D58 gates.
Never opens test/showcase files. Generation is serial, one CPU worker.
"""
from __future__ import annotations
import argparse
import faulthandler
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT/'experiments/mvp/corpus_tools'))
sys.path.insert(0, str(HERE.parent/'headroom'))
import build
from inventory import blocks_of

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value):
    build.atomic_json(path, value)

def describe(path, *, split, size, ship, family, seed, **extra):
    obj = json.loads(path.read_text())
    blocks, uncovered = blocks_of(obj)
    loads = set(obj['load_list'])
    cargo = [b for b in obj['containers'] if b['id'] in loads]
    counts = Counter((b['pod'], b.get('weight_class', 'unknown')) for b in cargo)
    return dict(map_id=f'{size}-{ship}-{family}-{seed:09d}', input=str(path.relative_to(ROOT)),
        split=split, size=size, ship=ship, family=family, seed=seed, sha256=sha(path),
        n_loads=len(loads), n_onboard=len(obj['onboard']['placements']),
        hatch_count=len(obj['vessel']['hatch_covers']), block_count=len(blocks), blocks=blocks,
        uncovered_stack_ids=uncovered, loaded_ports=sorted({b['pod'] for b in cargo}),
        declared_weight_classes=sorted({b.get('weight_class', 'unknown') for b in cargo}),
        loaded_port_weight_counts=[dict(port=p,weight_class=w,count=n) for (p,w),n in sorted(counts.items())],
        schema_version=obj.get('schema_version'), **extra)

def freeze():
    path = HERE/'cohort-requests.json'
    if path.exists():
        return json.loads(path.read_text())
    manifest_path = ROOT/'experiments/mvp/corpus/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    # The adopted per-ship fitting controls apply to SMALL only. Medium uses
    # default demand: slack=0 is the builder's explicit no-reduction opt-in,
    # and mass=None omits that producer kwarg.
    settings = {ship:dict(booking_slack=0.0,booking_mass_fraction=None) for ship in build.VESSELS}
    # Explicit new training namespace; do not draw from legacy held-out splits.
    used_seeds = {r['seed'] for r in manifest['files']}
    used_seeds.update(r['seed'] for split in ('train','val') for r in json.loads((ROOT/f'experiments/mvp/corpus/{split}.requested.json').read_text())['specs'])
    specs = []
    for si, ship in enumerate(build.VESSELS):
        for fi, family in enumerate(build.FAMILIES):
            for rep in range(2):
                seed = 185_100_000 + si*100 + fi*10 + rep
                assert seed not in used_seeds
                specs.append(dict(split='train',rung='medium',vessel=ship,generator=family,seed=seed,
                    port=('laem_chabang_like','singapore_like')[rep],noise=(0.2,0.5)[rep],
                    n_boxes=None,dense_20ft=False,tiny_dg_enabled=True,max_attempts=100,**settings[ship]))
    value = dict(format='D185-blockrule-frozen-generation-requests-v2',
        frozen_at_utc=datetime.now(timezone.utc).isoformat(), split='train',
        method='3 ships × 2 yard families × 2 predetermined seeds; no planner outcome selection',
        specs=specs, ship_settings=settings, source_files=build.source_snapshot(),
        builder_sha256=sha(ROOT/'experiments/mvp/corpus_tools/build.py'),
        corpus_manifest_sha256=sha(manifest_path), root_seed_overlap_with_corpus_or_requests=0,
        generation_workers=1, generator_gates='unmodified producer, C1, D58 background; C2 OFF; V4 unset',
        map_root=str((HERE/'maps').relative_to(ROOT)))
    write(path,value)
    return value

def generate(requests):
    if (HERE/'cohort-medium.json').exists():
        verify()
        return
    started=time.monotonic()
    rows=[]
    # Observation only: expose slow background LPs without changing arguments,
    # deadlines, return values or producer acceptance.
    import stowage.vessel.fleet_balance as fleet_balance
    original_linprog=fleet_balance.linprog
    def observed_linprog(*args, **kwargs):
        t=time.monotonic()
        print(json.dumps(dict(event='background_linprog_start',n_variables=len(args[0]) if args else None)),flush=True)
        result=original_linprog(*args,**kwargs)
        print(json.dumps(dict(event='background_linprog_end',seconds=time.monotonic()-t,status=int(result.status),success=bool(result.success))),flush=True)
        return result
    fleet_balance.linprog=observed_linprog
    faulthandler.dump_traceback_later(120,repeat=True)
    for spec in requests['specs']:
        result=build.generate_one((spec,str(HERE/'maps'),requests['source_files']))
        print(json.dumps(result),flush=True)
        if result['status']=='failed':
            raise RuntimeError(f'fixed request failed; retain failure without outcome-selected replacement: {spec}')
        path=build.shard_path(spec,HERE/'maps')
        audit,instance=build.validate_shard(path,current_sources=requests['source_files'])
        rows.append(describe(path,split='train',size='medium',ship=spec['vessel'],family=spec['generator'],seed=spec['seed'],
            audit=str(path.with_suffix('.audit.json').relative_to(ROOT)),audit_sha256=sha(path.with_suffix('.audit.json')),
            source_binding_status='current_epoch_generated',release_epoch='D185-current-epoch',
            generation_settings={k:spec[k] for k in ('booking_slack','booking_mass_fraction','n_boxes','noise','port')},
            generator_gate_passed=True,plan_checker_passed=None))
        # Partial progress is separate; final cohort is published only when complete.
        write(HERE/'cohort-generation-progress.json',dict(pid=os.getpid(),finished=len(rows),requested=len(requests['specs']),
            elapsed_s=time.monotonic()-started,maps=rows))
    value=dict(format='D185-blockrule-frozen-medium-train-v1',split='train',
        frozen_at_utc=requests['frozen_at_utc'], completed_at_utc=datetime.now(timezone.utc).isoformat(),
        request_sha256=sha(HERE/'cohort-requests.json'), selection=requests['method'],
        counts=dict(Counter(r['size'] for r in rows)),maps=rows)
    write(HERE/'cohort-medium.json',value)
    faulthandler.cancel_dump_traceback_later()

def small():
    if (HERE/'cohort-small-train.json').exists() and (HERE/'cohort-small-val.json').exists():
        return
    path=ROOT/'experiments/mvp/corpus/manifest.json'
    data=json.loads(path.read_text())
    exclusions=set()
    for prev in ('headroom/train_maps.json','proposer/maps.json'):
        exclusions.update(r['input'] for r in json.loads((HERE.parent/prev).read_text())['maps'])
    # Fresh means previously unselected train instances, not new fitting data.
    groups=defaultdict(list)
    for r in data['files']:
        if r['split']=='train' and r['rung']=='small' and r.get('active_for_current_release',True):
            full='experiments/mvp/corpus/'+r['path']
            if full not in exclusions:
                groups[r['ship'],r['family']].append(r)
    selected=[]
    for key,rows in sorted(groups.items()):
        rows.sort(key=lambda r:hashlib.sha256(('D185-fresh-small-train-v1:'+r['path']).encode()).hexdigest())
        selected.extend(rows[:5])
    assert len(selected)==30
    rows=[]
    for r in selected:
        p=ROOT/'experiments/mvp/corpus'/r['path']
        assert sha(p)==r['sha256']
        rows.append(describe(p,split='train',size='small',ship=r['ship'],family=r['family'],seed=r['seed'],
            manifest=str(path.relative_to(ROOT)),release_epoch=r.get('release_epoch'),
            source_binding_status=r.get('source_binding_status')))
    write(HERE/'cohort-small-train.json',dict(format='D185-blockrule-fresh-small-train-v1',split='train',
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),manifest_sha256=sha(path),
        selection='5 per ship × family; SHA256(D185-fresh-small-train-v1:+path); exclude headroom/proposer',
        fresh_sample=True,newly_generated=False,excluded_prior_inputs=sorted(exclusions),maps=rows))
    vals=[]
    for name in ('audit_small30.txt','reserve_small25.txt'):
        vals.extend(line.strip() for line in (ROOT/'experiments/mvp/valsplit'/name).read_text().splitlines() if line.strip())
    assert len(vals)==len(set(vals))==55
    bypath={r['path']:r for r in data['files'] if r['split']=='val' and r['rung']=='small'}
    rows=[]
    for val in sorted(vals):
        p=ROOT/val
        rel=str(p.relative_to(ROOT/'experiments/mvp/corpus'))
        r=bypath[rel]
        # Hash only: don't inspect validation instance content before train freeze.
        assert sha(p)==r['sha256']
        rows.append(dict(map_id=f"small-{r['ship']}-{r['family']}-{r['seed']:09d}",input=val,
            split='val',size='small',ship=r['ship'],family=r['family'],seed=r['seed'],sha256=sha(p),
            release_epoch=r.get('release_epoch'),source_binding_status=r.get('source_binding_status')))
    write(HERE/'cohort-small-val.json',dict(format='D185-blockrule-small-validation-v1',split='val',
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),selection='exact existing audit30 + reserve25',
        metadata_only=True,manifest_sha256=sha(path),maps=rows))

def verify():
    rows=[]
    for name in ('medium','small-train','small-val'):
        path=HERE/f'cohort-{name}.json'
        if not path.exists():
            continue
        data=json.loads(path.read_text())
        for r in data['maps']:
            assert sha(ROOT/r['input'])==r['sha256']
            assert r['split']==('val' if name=='small-val' else 'train')
            assert '/test/' not in r['input'] and '/showcase/' not in r['input']
            if name=='medium':
                build.validate_shard(ROOT/r['input'],current_sources=json.loads((HERE/'cohort-requests.json').read_text())['source_files'])
            rows.append((r['input'],r['sha256']))
    assert len(rows)==len({p for p,h in rows})==97
    assert len({h for p,h in rows})==97
    write(HERE/'cohort-verification.json',dict(verified_at_utc=datetime.now(timezone.utc).isoformat(),
        n_maps=len(rows),all_input_hashes_match=True,unique_paths=True,unique_instance_hashes=True,
        full_producer_audit_validation_medium=12,no_planner_or_checker_results_used_for_selection=True))
    print('Verified 97 input hashes, unique paths and instance hashes; 12 full producer audits.',flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','generate','verify'))
    args=parser.parse_args()
    if args.action=='freeze':
        freeze();small()
    elif args.action=='generate':
        generate(freeze());verify()
    else:
        verify()

if __name__=='__main__':
    main()
