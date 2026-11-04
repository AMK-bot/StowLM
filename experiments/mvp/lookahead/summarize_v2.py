"""Refresh finite-campaign tables; random completion is never checker acceptance."""
import json,math,statistics
from collections import Counter
from pathlib import Path
from worker import HERE,save

def stats(xs):
 xs=sorted(xs)
 return dict(n=len(xs),mean=statistics.mean(xs)if xs else None,p50=statistics.median(xs)if xs else None,p95=xs[max(0,math.ceil(.95*len(xs))-1)]if xs else None,max=max(xs)if xs else None)
def main():
 inv=json.loads((HERE/'inventory.json').read_text());contractmap=json.loads((HERE/'contract-witnesses-v2.json').read_text())['cases'];contracts=[]
 for c in inv['contracts']:
  f=Path(contractmap[c['case']]);d=json.loads(f.read_text())if f.exists()else{};contracts.append(dict(case=c['case'],rung=c['rung'],ship=c['ship'],family=c['family'],artifact=str(f),completed=d.get('completed',False),passed=bool(d.get('completed')and d.get('contract_passed')and d.get('source_valid')),error=d.get('error'),masked_action_index=d.get('rejected_action_index'),source_valid=d.get('source_valid'),actions=d.get('actions'),tokens=d.get('tokens')))
 cases=[];rollouts=[];prefixes=[];deferred=Counter();lookahead_counts=Counter();static=[]
 for manifest in sorted((HERE/'formal-v2-paired').glob('shard*/manifest.json')):
  for job in json.loads(manifest.read_text())['jobs']:
   f=Path(job['directory'])/'result.json';d=json.loads(f.read_text())if f.exists()else{};c=job['case'];cases.append(dict(case=c['case'],cohort=c['cohort'],ship=c['ship'],family=c['family'],artifact=str(f),completed=d.get('completed',False),source_valid=d.get('source_valid'),rollouts=len(d.get('rollouts',[])),error=d.get('error'),wall_s=d.get('wall_s'),cpu_s=d.get('cpu_s'),sleep_s=d.get('sleep_s')))
   for r in d.get('rollouts',[]):
    rollouts.append(dict(case=c['case'],cohort=c['cohort'],ship=c['ship'],family=c['family'],case_source_finalized=d.get('completed',False),case_source_valid=d.get('source_valid'),**r))
    if r['arm']=='lookahead':
     diag=r.get('diagnostics',{}).get('lookahead',{});deferred.update(diag.get('deferred_reasons',{}));lookahead_counts.update({k:diag.get(k,0)for k in ['states_evaluated','candidate_probes','rejected_actions','state_rejections','unknown_states','bound_seconds']})
   prefixes.extend([dict(case=c['case'],cohort=c['cohort'],ship=c['ship'],**r)for r in d.get('matched_prefixes',[])])
   if'lookahead_factory_s'in d:static.append(d['lookahead_factory_s']*1000)
 groups=[]
 for cohort in ['test15','train20']:
  for ship in ['all','feeder_500','feeder_800','feeder_1700']:
   n=sum(c['cohort']==cohort and(ship=='all'or c['ship']==ship)for c in inv['rollout_instances'])
   for arm in ['baseline','lookahead']:
    rs=[r for r in rollouts if r['cohort']==cohort and r['arm']==arm and(ship=='all'or r['ship']==ship)];count=Counter(r['status']for r in rs);groups.append(dict(cohort=cohort,ship=ship,arm=arm,expected=n*20,attempted=len(rs),complete_proxy=count['complete_proxy'],dead_end_proxy=count['dead_end_proxy'],cpu_limit=count['cpu_limit'],errors=count['error'],missing=n*20-len(rs),complete_rate=count['complete_proxy']/len(rs)if rs else None,dead_end_rate=count['dead_end_proxy']/len(rs)if rs else None,source_finalized=sum(r['case_source_finalized']for r in rs)))
 pairs={}
 for r in rollouts:pairs.setdefault((r['case'],r['repetition']),{})[r['arm']]=r
 paired=Counter();trace_equal=0;paired_count=0;visible_reductions=[];visible_increases=[];common_prefix_states=0
 for item in pairs.values():
  if set(item)=={'baseline','lookahead'}:
   b,on=item['baseline'],item['lookahead'];assert b['seed']==on['seed'];paired[(b['status'],on['status'])]+=1;paired_count+=1;trace_equal+=([s['action']for s in b['steps']]==[s['action']for s in on['steps']])
   bs,os=b['steps'],on['steps'];matched=0
   for depth,(left,right)in enumerate(zip(bs,os)):
    common_prefix_states+=1;diff=left['legal_actions']-right['legal_actions']
    if diff:(visible_reductions if diff>0 else visible_increases).append(dict(case=b['case'],repetition=b['repetition'],depth=depth,baseline_actions=left['legal_actions'],lookahead_actions=right['legal_actions'],reduction=diff))
    if left['action']!=right['action']:break
    matched+=1
   if matched==min(len(bs),len(os))and len(bs)!=len(os):
    if len(os)<len(bs)and on['status']=='dead_end_proxy':visible_reductions.append(dict(case=b['case'],repetition=b['repetition'],depth=len(os),baseline_actions=bs[len(os)]['legal_actions'],lookahead_actions=0,reduction=bs[len(os)]['legal_actions']))
    if len(bs)<len(os)and b['status']=='dead_end_proxy':visible_increases.append(dict(case=b['case'],repetition=b['repetition'],depth=len(bs),baseline_actions=0,lookahead_actions=os[len(bs)]['legal_actions'],reduction=-os[len(bs)]['legal_actions']))
 out=dict(required_rollouts=1400,recorded_rollouts=len(rollouts),required_contracts=100,contracts_completed=sum(c['completed']for c in contracts),contracts_passed=sum(c['passed']for c in contracts),contract_by_rung={r:dict(required=50,completed=sum(c['rung']==r and c['completed']for c in contracts),passed=sum(c['rung']==r and c['passed']for c in contracts))for r in ['tiny','small']},contract_failures=[c for c in contracts if c['completed']and not c['passed']],formal_cases=cases,groups=groups,paired_transitions=[dict(baseline=k[0],lookahead=k[1],count=v)for k,v in paired.items()],lookahead_coverage=dict(lookahead_counts),lookahead_deferred_reasons=dict(deferred),matched_prefixes=len(prefixes),matched_query_ms=stats([r['new_default_query_ms']for r in prefixes]),matched_cached_baseline_ms=stats([r['baseline_cached_query_ms']for r in prefixes]),matched_wrapper_init_ms=stats([r['wrapper_initialization_ms']for r in prefixes]),lookahead_factory_initialization_ms=stats(static),scope='Uniform legal ACTION rollout completion/deadend PROXIES only; NO ballast/full checker run on random plans. Historical checker-bound gold100 replay through actual finaldefaultfactory and realGlimmertokenizer. P95=nearest-rank ceil(.95*n). Initial timing pilots excluded from formal numerators.')
 out['contract_tokens']=sum(c.get('tokens')or 0 for c in contracts);out['contract_actions']=sum(c.get('actions')or 0 for c in contracts)
 out['observed_rollout_capacity_policies']=dict(Counter(r.get('diagnostics',{}).get('lookahead',{}).get('capacity_policy','missing')for r in rollouts if r['arm']=='lookahead'))
 out['observed_rollout_long_matching']=dict(Counter(str(r.get('diagnostics',{}).get('lookahead',{}).get('long_matching','missing'))for r in rollouts if r['arm']=='lookahead'))
 token_prefixes=[r for r in prefixes if r.get('chosen_action_token_count',0)>0]
 out['matched_prefixes_without_chosen_token_count']=len(prefixes)-len(token_prefixes)
 out['matched_extra_query_ms']=stats([r['new_default_query_ms']-r['baseline_cached_query_ms']for r in prefixes])
 out['matched_selected_action_tokens']=sum(r['chosen_action_token_count']for r in token_prefixes)
 out['amortized_extra_query_ms_per_selected_action_token']=(sum(r['new_default_query_ms']-r['baseline_cached_query_ms']for r in token_prefixes)/out['matched_selected_action_tokens']if token_prefixes else None)
 out['per_state_amortized_query_ms_per_action_token']=stats([(r['new_default_query_ms']-r['baseline_cached_query_ms'])/r['chosen_action_token_count']for r in token_prefixes])
 out['token_amortization_policy']='Measured same-prefix new-query minus cached-baseline-query wall divided by actual chosen Glimmer ACTION-token counts; EOS excluded. Amortized accounting, not measured per-token model-generation latency. Factory initialization includes static capacity and all decoder wrappers; no isolated static-init timing.'
 out['rollout_action_tokens']={arm:sum(step['action_token_count']for r in rollouts if r['arm']==arm for step in r['steps'])for arm in ['baseline','lookahead']}
 out['matched_pairs']=paired_count;out['identical_action_traces']=trace_equal
 out['common_prefix_action_states']=common_prefix_states;out['shared_prefix_visible_action_reductions']=len(visible_reductions);out['shared_prefix_actions_removed']=sum(r['reduction']for r in visible_reductions);out['shared_prefix_action_increases']=visible_increases
 save(HERE/'shared-prefix-action-reductions-v2.json',visible_reductions)
 out['warm_cache_timing_policy']='Explicit shared necessary-securing cache plus native instance caches shared by shallow native.copy(); independent action histories. Arm sampling times are warm-cache trajectory measurements, not isolated filter overhead.'
 old_baseline={(r['case'],r['repetition']):r for r in json.loads((HERE/'rollouts.json').read_text())if r['arm']=='baseline'}
 cross=[]
 for r in rollouts:
  if r['arm']=='baseline':
   prior=old_baseline[(r['case'],r['repetition'])];assert prior['seed']==r['seed'];cross.append(dict(case=r['case'],repetition=r['repetition'],same_status=prior['status']==r['status'],same_actions=[s['action']for s in prior['steps']]==[s['action']for s in r['steps']],old_status=prior['status'],new_status=r['status']))
 out['v1_v2_baseline_control']=dict(compared=len(cross),identical=sum(r['same_status']and r['same_actions']for r in cross),differences=[r for r in cross if not(r['same_status']and r['same_actions'])])
 out['stall_attribution']='Unverified. Securing evaluation_seconds measures only component calculation; placement_seconds overlaps some evaluation time and omits other orchestration. No per-native-rule timing profile was recorded.'
 out['total_by_arm']={arm:dict(expected=700,recorded=sum(r['arm']==arm for r in rollouts),**dict(Counter(r['status']for r in rollouts if r['arm']==arm)))for arm in ['baseline','lookahead']}
 out['matched_prefix_action_count_changes']=sum(r['baseline_actions']!=r['lookahead_actions']for r in prefixes)
 out['matched_prefix_action_probe_cap_hits']=sum((r.get('lookahead_diagnostics')or{}).get('candidate_probes',0)>=128 for r in prefixes)
 save(HERE/'summary-v2.json',out);save(HERE/'rollouts-v2.json',rollouts);save(HERE/'matched-prefixes-v2.json',prefixes);save(HERE/'contract-results-v2.json',contracts)
 lines=['|Cohort|Ship|Arm|Recorded / required|Complete proxy|Dead end proxy|CPU limit|Error|','|---|---|---|---:|---:|---:|---:|---:|']
 for g in groups:lines.append(f"|{g['cohort']}|{g['ship']}|{g['arm']}|{g['attempted']}/{g['expected']}|{g['complete_proxy']}|{g['dead_end_proxy']}|{g['cpu_limit']}|{g['errors']}|")
 (HERE/'table-v2.md').write_text('\n'.join(lines)+'\n');print(json.dumps({k:out[k]for k in ['recorded_rollouts','contracts_completed','contracts_passed','matched_prefixes','matched_query_ms','contract_failures']}))
if __name__=='__main__':main()
