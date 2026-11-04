"""One-core warm action profiling. Run from repo with uv run python PATH."""
from __future__ import annotations
import argparse, cProfile, hashlib, importlib.util, json, os, pstats, time
from pathlib import Path
from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskedDecoder
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.feasibility.in_port import in_port_screen_enabled
from stowage.rules.catalogue import rule_freeze_v1
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

def hashes():
    paths=list((ROOT/'src/llm/stowlm').glob('mask*.py'))
    for folder in ('src/stowage/feasibility','src/stowage/actions','src/stowage/moves','src/stowage/rules'):
        paths+=list((ROOT/folder).glob('*.py'))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

def action(decoder,count):
    t=time.perf_counter(); tokens=[]; allowed_sets=[]
    while decoder.status=='running':
        allowed=decoder.allowed_token_ids(); assert allowed
        token=allowed[0]; allowed_sets.append(allowed); tokens.append(token)
        if decoder._node.children[token].action is not None: break
        decoder.advance(token)
    domain=time.perf_counter()-t
    t=time.perf_counter(); n=decoder.n_legal if count else None; count_time=time.perf_counter()-t if count else 0.
    t=time.perf_counter(); a=decoder.advance(token); commit=time.perf_counter()-t; assert a is not None
    return dict(domain_s=domain,count_s=count_time,commit_s=commit,total_s=domain+count_time+commit,tokens=tokens,
                allowed_sha=hashlib.sha256(repr(allowed_sets).encode()).hexdigest(),chosen=repr(a),n_legal=n,
                diagnostics=decoder.engine.screen_diagnostics())

def category(file,func):
    # Exclusive self time: helpers below calls (e.g. deepcopy) are deliberately
    # separately visible. Cumulative entry-point rows retain their full subtree.
    if '/copy.py' in file or func in ('__deepcopy__','model_copy','_copy_and_set_values'): return 'copy/models'
    if '/rules/catalogue.py' in file or func in ('_CatalogueContext','_in_catalogue_context'): return 'catalogue/context'
    if '/moves/_paths.py' in file: return 'crossing paths'
    if '/moves/_sequencing.py' in file or func=='_pending_hatch_can_clear': return 'D69 sequencing'
    if '/feasibility/in_port.py' in file: return 'D64 screen'
    if '/actions/expander.py' in file: return 'expander'
    if '/moves/' in file: return 'move replay'
    if '/llm/stowlm/' in file: return 'tokenizer/trie'
    if '/feasibility/_engine.py' in file and (func.startswith('_dg') or func in ('_pair_ok','_pair_kind','_c1g')): return 'DG cache/screen'
    if '/feasibility/' in file: return 'other feasibility'
    if '/conventions.py' in file: return 'address parsing'
    if 'pydantic' in file: return 'pydantic models'
    return 'other/library'

def summarize(prof):
    stats=pstats.Stats(prof); exclusive={}; rows=[]
    for (file,line,func),(pcalls,ncalls,self_s,cum_s,callers) in stats.stats.items():
        key=category(file,func); exclusive[key]=exclusive.get(key,0.)+self_s
        rows.append(dict(file=file,line=line,func=func,primitive_calls=pcalls,calls=ncalls,self_s=self_s,cum_s=cum_s))
    return dict(profile_total_s=stats.total_tt,exclusive_s=exclusive,functions=sorted(rows,key=lambda r:-r['cum_s']))

def run(rung,repeats):
    before=hashes()
    with rule_freeze_v1(enabled=False): instance=load('tests/feasibility/w2_performance_probe.py','perf_fixture').make_performance_instance(rung)
    codec=ActionCodec(load_tokenizer(),[c.id for c in instance.containers]); cache=None
    result=dict(rung=rung,cells=len(instance.vessel.cells),pending=len(instance.load_list),onboard=len(instance.onboard.placements),
                source_hashes_before=before,env={k:os.getenv(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','STOWAGE_IN_PORT_WORKERS')})
    with rule_freeze_v1(enabled=True):
        result['d64_enabled']=in_port_screen_enabled()
        for count in (False,True):
            mode='with_count' if count else 'decode'; samples=[]
            for i in range(repeats+1):
                engine=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
                dec=MaskedDecoder(engine,codec,token_cache=cache); cache=dec._cache
                row=action(dec,count)
                if i: samples.append(row)
            result[mode]=dict(samples=samples,mean={k:sum(s[k] for s in samples)/repeats for k in ('domain_s','count_s','commit_s','total_s')})
            engine=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
            dec=MaskedDecoder(engine,codec,token_cache=cache); prof=cProfile.Profile()
            prof.enable(); row=action(dec,count); prof.disable()
            prof.dump_stats(str(OUT/f'profile-{rung}-{mode}.pstats'))
            result[mode]['profile']=dict(measurement=row,**summarize(prof))
            # Phase-specific profiles distinguish exact-count expansion from domain work.
            engine=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
            dec=MaskedDecoder(engine,codec,token_cache=cache)
            phaseprofs={}
            domain=cProfile.Profile(); domain.enable()
            while True:
                allowed=dec.allowed_token_ids(); token=allowed[0]
                if dec._node.children[token].action is not None: break
                dec.advance(token)
            domain.disable(); phaseprofs['domain']=domain
            if count:
                p=cProfile.Profile();p.enable();n=dec.n_legal;p.disable();phaseprofs['count']=p
            p=cProfile.Profile();p.enable();dec.advance(token);p.disable();phaseprofs['commit']=p
            result[mode]['phases']={}
            for phase,p in phaseprofs.items():
                p.dump_stats(str(OUT/f'profile-{rung}-{mode}-{phase}.pstats'))
                result[mode]['phases'][phase]=summarize(p)
            (OUT/f'profile-{rung}.json').write_text(json.dumps(result,indent=2))
            print(json.dumps(dict(rung=rung,mode=mode,mean=result[mode]['mean'],top=result[mode]['profile']['functions'][:12])),flush=True)
    result['source_hashes_after']=hashes();result['source_drift']={k:[v,result['source_hashes_after'].get(k)] for k,v in before.items() if result['source_hashes_after'].get(k)!=v}
    (OUT/f'profile-{rung}.json').write_text(json.dumps(result,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rung',choices=['medium','full'],required=True);p.add_argument('--repeats',type=int,default=3);args=p.parse_args();run(args.rung,args.repeats)
