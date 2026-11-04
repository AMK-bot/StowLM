"""Private CPU inventory exercise; never used as a deployment schedule."""
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from collections import Counter

ROOT=next(path for path in Path(__file__).resolve().parents if (path/'pyproject.toml').exists())
OUT=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    start=time.perf_counter()
    from llm.exit import identity
    from llm.exit.replay import adapter_training_hashes
    corpus=ROOT/'experiments/mvp/corpus'
    # Preserve actual held-out identity rules without reading held-out bodies.
    # The production cache is accepted only for exact current file-stat keys;
    # sidecar/manifest metadata supplies publication identities as in production.
    saved=json.loads((ROOT/'experiments/mvp/exit/identity-cache.json').read_text())
    cache={tuple(row['key']):{tuple(token) for token in row['tokens']} for row in saved['files']}
    guard=identity.IdentityGuard.__new__(identity.IdentityGuard)
    guard.root=corpus.resolve();guard.identities={split:set() for split in ('test','showcase','val')}
    manifest=json.loads((corpus/'manifest.json').read_text())
    for entry in manifest['files']:
        if entry.get('split') in guard.identities:
            for key in ('sha256','seed','instance_id'):
                if entry.get(key) is not None:
                    guard.identities[entry['split']].add((key,str(entry[key])))
    bodies=0;sidecars=0
    for split,tokens in guard.identities.items():
        for path in (corpus/split).rglob('*.json'):
            if path.name.endswith('.selection.json'):
                continue  # Showcase selection bookkeeping has no instance identities.
            key=(str(path),path.stat().st_mtime_ns,path.stat().st_size)
            if key in cache:
                tokens.update(cache[key]);bodies+=not path.name.endswith('.audit.json');continue
            if path.name.endswith('.audit.json'):
                value=json.loads(path.read_text());sidecars+=1
                if value.get('instance_sha256'):tokens.add(('sha256',value['instance_sha256']))
                if value.get('config',{}).get('seed') is not None:
                    tokens.add(('seed',str(value['config']['seed'])))
            else:raise RuntimeError(f'uncached held-out identity refuses body access: {path}')
    identity.identity_guard=lambda root=corpus:guard
    # Enforce the no-held-out-body promise even if a called helper changes.
    original_text,original_bytes=Path.read_text,Path.read_bytes
    def blocked(path):
        return any(path.resolve().is_relative_to(corpus/split) for split in ('test','showcase','val')) and not path.name.endswith('.audit.json')
    def text(path,*args,**kwargs):
        if blocked(path):raise RuntimeError(f'forbidden held-out body read: {path}')
        return original_text(path,*args,**kwargs)
    def data(path,*args,**kwargs):
        if blocked(path):raise RuntimeError(f'forbidden held-out body read: {path}')
        return original_bytes(path,*args,**kwargs)
    Path.read_text,Path.read_bytes=text,data
    state=load('_d137_review_restart_state',ROOT/'experiments/mvp/exit/patches/d137/restart/state.py')
    prior,sources=state.union_prior_reservations(state.prior_schedule_paths())
    target=OUT/'PRIVATE-DO-NOT-DEPLOY-schedule.json'
    target.write_text(json.dumps(prior))
    module=load('_d137_review_data',ROOT/'experiments/mvp/exit/patches/d137/assembled/src/llm/exit/data.py')
    module.ROOT=ROOT
    adapter=ROOT/'experiments/mvp/sft/stowlm-v2/adapter'
    seen=adapter_training_hashes([adapter])
    schedule=module.CorpusSchedule(corpus,target)
    paths=schedule.next_train(4,8,0,gold_root=ROOT/'experiments/mvp/gold',seen_adapter_hashes=seen,gold_fraction=.5)
    entries=schedule.state['generations']['0']
    before=set(prior['used'])
    assert len(paths)==12 and not before.intersection(e['sha256'] for e in entries)
    assert Counter(e['map_stratum'] for e in entries)=={'checked_gold_teacher':6,'no_stored_gold':6}
    assert all(e['split']=='train' for e in entries)
    result=dict(status='passed',private_not_deploy=True,wall_s=time.perf_counter()-start,
        maps=len(paths),rungs=dict(Counter(e['rung'] for e in entries)),
        strata=dict(Counter(e['map_stratum'] for e in entries)),
        novelty=dict(Counter(e['novelty'] for e in entries)),
        ships=dict(Counter(e['ship'] for e in entries)),
        inherited_excluded_maps=len(prior['used']),inherited_excluded_seeds=len(prior['used_seeds']),
        prior_sources=sources,overlap_with_previous_reserved_hashes=0,
        adapter_training_inventory_known=seen is not None,
        adapter_training_inventory_size=len(seen) if seen is not None else None,
        heldout_bodies_read=0,cached_heldout_body_identities=bodies,uncached_audit_sidecars_read=sidecars,
        teacher_sources=[dict(path=e['path'],evidence=e['teacher_gold_evidence']) for e in entries if e['gold_available_at_selection']],
        maps_by_stratum=[dict(path=e['path'],stratum=e['map_stratum'],novelty=e['novelty'],rung=e['rung'],ship=e['ship']) for e in entries],
        qualification='Historical full-check evidence only; deployment still requires fresh current-epoch teacher full check and mask replay.')
    (OUT/'schedule-exercise.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('teacher_sources','maps_by_stratum','prior_sources')},indent=2))

if __name__=='__main__':main()
