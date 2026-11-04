"""Close benchmark-only handoff without rerunning numerical work."""
import hashlib,json,statistics
from pathlib import Path
from datetime import datetime,timezone
H=Path(__file__).resolve().parent

def read(p):return json.loads((H/p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):(H/p).write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')

s=read('summary-v2-combined.json');a=read('audit-v2-combined.json');c=read('compute-v2-combined.json');root=read('ROOT_FINAL_VERIFICATION.json');rows=read('rollouts-v2-combined.json');pilot=read('prototype/wide-config32-512/pilot-paired-archive/launcher.json');pr=read('prototype/wide-config32-512/pilot-paired-archive/test15-28100010/result.json')
assert root['verified'] and a['final'] and a['all_accepted_exact'] and a['all_raw_children_reaped_exit0'] and not a['active_owned_children']
assert pilot['completed'] and pilot['reaped'] and pilot['exit_code']==0 and pr['source_valid']
witness=read('primary-v2-witnesses.json')['cases'];pairs={}
for r in rows:pairs.setdefault((r['case'],r['repetition']),{})[r['arm']]=r
rescues=[]
for (case,rep),pair in pairs.items():
 if pair['baseline']['status']=='dead_end_proxy'and pair['lookahead']['status']=='complete_proxy':
  path=Path(witness[case]);rescues.append(dict(case=case,repetition=rep,result=str(path),baseline_artifact=str(path.parent/f'baseline-{rep:02}.json'),lookahead_artifact=str(path.parent/f'lookahead-{rep:02}.json'),baseline_steps=len(pair['baseline']['steps']),lookahead_steps=len(pair['lookahead']['steps'])))
stages={}
for arm in('baseline','lookahead'):
 rr=[r for r in rows if r['arm']==arm]
 stages[arm]=dict(n=len(rr),sampling_cpu_s=sum(r['sampling_cpu_s']for r in rr),sampling_wall_s=sum(r['sampling_wall_s']for r in rr),mean_sampling_cpu_s=statistics.mean(r['sampling_cpu_s']for r in rr),mean_sampling_wall_s=statistics.mean(r['sampling_wall_s']for r in rr))
review=dict(status='Unverified cross-campaign reproducibility; within-process paired contrast retained.',v1_baseline_complete=297,v2_baseline_complete=304,compared=700,identical_traces=690,different_traces=10,status_changes=7,input_hashes_equal=True,repetition_seeds_equal=True,first_difference='Necessary securing force-limit queries (fitting/racking/lashing); no earlier action divergence on those prefixes.',cause='Not established. PYTHONHASHSEED was not pinned; no numerical isolation experiment was run. Static review found no missing mutable auxiliary data on the C2-off screen path; this does not identify the cause.',details=str(H/'baseline-divergence-v2.json'),claim_limit='Do not attribute the seven baseline changes to lookahead or claim identical V1/V2 control trajectories.')
save('baseline-reproducibility-review.json',review)
files=('summary-v2-combined.json','table-v2-combined.md','audit-v2-combined.json','compute-v2-combined.json','ROOT_FINAL_VERIFICATION.json','runtime-freeze-v2.json','runtime-v2/archive-manifest.json','primary-v2-witnesses.json','contract-exposure-v2.json','baseline-divergence-v2.json','baseline-reproducibility-review.json','REPRODUCTION_V2.md','compute-all-v2.json','runtime-live-v2-contracts/archive-manifest.json')
result=dict(completed_utc=datetime.now(timezone.utc).isoformat(),final=True,runtime_epoch=root['runtime_epoch'],required_rollouts=1400,total_by_arm=s['total_by_arm'],paired_transitions=s['paired_transitions'],rescues=rescues,regressions=0,contracts=dict(passed=100,required=100,tiny=50,small=50,tokens=s['contract_tokens'],actions=s['contract_actions'],exposure=read('contract-exposure-v2.json')),coverage=s['lookahead_coverage'],deferred_reasons=s['lookahead_deferred_reasons'],visible_actions_removed=s['shared_prefix_actions_removed'],stages=stages,overhead=dict(matched_query_ms=s['matched_query_ms'],matched_extra_query_ms=s['matched_extra_query_ms'],wrapper_init_ms=s['matched_wrapper_init_ms'],factory_init_ms=s['lookahead_factory_initialization_ms'],amortized_extra_query_ms_per_actual_action_token=s['amortized_extra_query_ms_per_selected_action_token']),compute=c,reproducibility=review,optional_wide=dict(status='Formal extension skipped by parent decision; private one-pair cold pilot only, excluded from all rates.',config=pr['job']['lookahead_config'],launcher=pilot,result=str(H/'prototype/wide-config32-512/pilot-paired-archive/test15-28100010/result.json'),both_complete=True,candidate_probes=pr['rollouts'][1]['diagnostics']['lookahead']['candidate_probes'],action_cuts=pr['rollouts'][1]['diagnostics']['lookahead']['rejected_actions']),all_owned_children_reaped=True,current_live_source_drift=a.get('current_live_drift',a.get('current_runtime_drift')),honesty='Random complete/dead outcomes are mask proxies, not full-checker acceptance. Gold plans have exact historical checker bindings; no new physics claim. All14 excluded drift attempts and80 excluded raw rollout records remain preserved. Primary claims refer to frozen old runtime, not later changed live securing/mask code.',artifacts={name:dict(path=str(H/name),sha256=sha(H/name))for name in files if(H/name).exists()},handoff_script_sha256=sha(__file__))
live_path=H/'formal-live-v2-contracts/summary.json'
if live_path.exists():
 live=read('formal-live-v2-contracts/summary.json');assert live['final'] and live['valid'] and not live['active_children']
 result['current_live_contract_gate']=dict(path=str(live_path),sha256=sha(live_path),epoch=live['epoch'],passed=live['passed'],required=100,by_rung=live['by_rung'],tokens=live['tokens'],exposure=live['exposure'],worker_cpu_s=live['worker_cpu_s'],worker_wall_sum_s=live['worker_wall_sum_s'],max_worker_lifetime_rss_bytes=live['max_worker_lifetime_rss_bytes'],controllers=[{k:x.get(k)for k in('pid','completed','wall_s','started_utc','ended_utc')}for x in live['controllers']],all_children_reaped=True)
 result['artifacts']['formal-live-v2-contracts/summary.json']=dict(path=str(live_path),sha256=sha(live_path))
save('FINAL_V2.json',result)
print(json.dumps(dict(final=True,rescues=rescues,all_owned_children_reaped=True,artifact=str(H/'FINAL_V2.json'))))
