"""Production schedule: exact n_legal queried after first allowed-token query."""
import argparse,json,time
from pathlib import Path
from profile_mask import *

def run_boundary(rung,repeats):
    before=hashes()
    with rule_freeze_v1(enabled=False): instance=load('tests/feasibility/w2_performance_probe.py','boundary_fixture').make_performance_instance(rung)
    codec=ActionCodec(load_tokenizer(),[c.id for c in instance.containers]);cache=None;samples=[]
    with rule_freeze_v1(enabled=True):
        for repeat in range(repeats+1):
            e=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
            d=MaskedDecoder(e,codec,token_cache=cache);cache=d._cache
            t=time.perf_counter();allowed=d.allowed_token_ids();heads=time.perf_counter()-t
            t=time.perf_counter();n=d.n_legal;count=time.perf_counter()-t
            row=action(d,False)
            row.update(heads_s=heads,boundary_count_s=count,n_legal=n,total_s=heads+count+row['total_s'])
            if repeat:samples.append(row)
    result=dict(rung=rung,schedule='first allowed_token_ids; n_legal; remaining tokens; commit',samples=samples,
        mean={k:sum(s[k] for s in samples)/repeats for k in ('heads_s','boundary_count_s','domain_s','commit_s','total_s')},
        source_hashes_before=before,source_hashes_after=hashes())
    result['source_drift']={k:[v,result['source_hashes_after'].get(k)] for k,v in before.items() if result['source_hashes_after'].get(k)!=v}
    (OUT/f'profile-{rung}-boundary-count.json').write_text(json.dumps(result,indent=2));print(json.dumps(result['mean']),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rung',choices=['medium','full'],required=True);p.add_argument('--repeats',type=int,default=3);a=p.parse_args();run_boundary(a.rung,a.repeats)
