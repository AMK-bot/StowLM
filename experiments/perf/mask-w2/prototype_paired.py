"""Alternating paired timing, fresh engines, shared warm token tables, no GPU."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean,median
from time import perf_counter
import prototype as proto
from llm.stowlm.format import ActionCodec,load_tokenizer
from llm.stowlm.mask import MaskedDecoder
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
from stowage.rules.catalogue import rule_freeze_v1

OUT=Path(__file__).resolve().parent

def hashes():
    paths=list((proto.ROOT/'src/llm/stowlm').glob('mask*.py'))
    for folder in ('src/stowage/feasibility','src/stowage/moves','src/stowage/actions'):
        paths.extend((proto.ROOT/folder).glob('*.py'))
    paths.append(proto.ROOT/'src/stowage/conventions.py')
    return {str(p.relative_to(proto.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

def run(repeats=5,variants=('baseline','strings')):
    before=hashes();fixture=proto.module('tests/feasibility/w2_performance_probe.py','prototype_paired_fixture')
    rows=[];tokenizer=load_tokenizer()
    for rung in ('medium','full'):
        with rule_freeze_v1(enabled=False): instance=fixture.make_performance_instance(rung)
        codec=ActionCodec(tokenizer,[b.id for b in instance.containers]);token_cache=None
        for mode in ('decode','late_count','boundary_count'):
            for rep in range(repeats+1):
                order=variants if rep%2==0 else tuple(reversed(variants))
                for variant in order:
                    with rule_freeze_v1(enabled=True),proto.optimized(proto.VARIANTS[variant]):
                        e=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
                        d=MaskedDecoder(e,codec,token_cache=token_cache);token_cache=d._cache
                        start=perf_counter();n=None;count_s=0
                        allowed=d.allowed_token_ids()
                        if mode=='boundary_count':
                            cstart=perf_counter();n=d.n_legal;count_s=perf_counter()-cstart
                        tokens=0
                        while d.status=='running':
                            allowed=d.allowed_token_ids();assert allowed
                            token=allowed[0]
                            if d._node.children[token].action is not None:break
                            d.advance(token);tokens+=1
                        domain_s=perf_counter()-start-count_s
                        if mode=='late_count':
                            cstart=perf_counter();n=d.n_legal;count_s=perf_counter()-cstart
                        cstart=perf_counter();action=d.advance(token);commit_s=perf_counter()-cstart;assert action
                        if rep:
                            row=dict(rung=rung,mode=mode,repeat=rep,variant=variant,domain_ms=1000*domain_s,count_ms=1000*count_s,commit_ms=1000*commit_s,total_ms=1000*(domain_s+count_s+commit_s),n_legal=n,chosen=repr(action),tokens=tokens+1)
                            rows.append(row);print(json.dumps(row),flush=True)
        (OUT/'prototype-paired-results.json').write_text(json.dumps(dict(samples=rows,source_before=before,source_after=hashes()),indent=2)+'\n')
    summary=[]
    for rung in ('medium','full'):
        for mode in ('decode','late_count','boundary_count'):
            item=dict(rung=rung,mode=mode,variants={})
            for variant in variants:
                samples=[r for r in rows if r['rung']==rung and r['mode']==mode and r['variant']==variant]
                item['variants'][variant]={name:mean(r[name] for r in samples) for name in ('domain_ms','count_ms','commit_ms','total_ms')}
                item['variants'][variant]['median_total_ms']=median(r['total_ms'] for r in samples)
            baseline=item['variants']['baseline']['total_ms']
            for variant in variants:item['variants'][variant]['speedup']=baseline/item['variants'][variant]['total_ms']
            summary.append(item)
    final=dict(summary=summary,samples=rows,source_before=before,source_after=hashes(),source_changed=before!=hashes())
    (OUT/'prototype-paired-results.json').write_text(json.dumps(final,indent=2)+'\n');print(json.dumps(summary),flush=True)
    return final

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repeats',type=int,default=5);p.add_argument('--variant',nargs='+',default=['baseline','strings']);a=p.parse_args();run(a.repeats,tuple(a.variant))
