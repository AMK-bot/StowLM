"""Three fresh public seagoing checks after concurrent in-port-only edits."""
import json
import resource
import time
from followup2_common import OUT, save, source_hashes
from followup2_binding import contexts, COEFFICIENTS
from stowage.model import Instance
from stowage.checker.stability import check_stability
from feeder_envelope import evaluate
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_budget import budget

start=time.perf_counter();before=source_hashes();rows=[]
for name in ('feeder_500','feeder_800','feeder_1700'):
    payload=json.loads((OUT/f'{name}-C1-input.json').read_text())
    instance=Instance.model_validate(payload['instance'])
    _,_,context,ship=next(contexts(instance,instance.onboard,['arrival']))
    # The earlier cold1700 public replay exceeded120s at liquid/hull
    # equilibrium. Match the original campaign's direct-geometry warm-up
    # before replaying the public wrapper; no physical input is changed.
    if name=='feeder_1700':
        p=json.loads((OUT/f'{name}-params.json').read_text())
        with budget(120.):
            warm,_,_=evaluate(context,ship,p,StabilityGeometry(p))
        print(name,'direct warm-up complete',flush=True)
    rep=check_stability(context,ship,call_index=context.call_index,in_port=False)
    assert rep.ran and not rep.error,rep
    tag='4p5' if name=='feeder_500' else 'native'
    previous=json.loads((OUT/f'{name}-C1-arrival-v{tag}.json').read_text())['rows'][0]
    differences={k:rep.metrics[k]-previous['metrics'][k] for k in previous['metrics']}
    assert max(map(abs,differences.values()))<1e-8,(name,differences)
    rows.append(dict(ship=name,report=rep.model_dump(mode='json'),metric_differences=differences))
    print(name,max(map(abs,differences.values())),flush=True)
result=dict(rows=rows,source_hashes_before=before,source_hashes_after=source_hashes(),
    cold1700_attempt=dict(ran=False,time_budget_s=120.,elapsed_s=132.6712722079974,
        error='stability time budget exceeded: liquid/hull equilibrium',
        log='post-epoch-baseline-replay.log'),
    elapsed_s=time.perf_counter()-start,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
save(OUT/'post-epoch-baseline-replay.json',result)
assert before==result['source_hashes_after']
