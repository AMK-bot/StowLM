"""Close the separate v2 campaign after every fixed trial and contract is saved."""
import hashlib
import json
import shutil
from worker import HERE
import summarize_v2
import audit_v2
import compute_v2


def read(name):
    return json.loads((HERE/name).read_text())


def main():
    summarize_v2.main();audit_v2.run();compute_v2.run()
    s,a,c=(read(name)for name in ('summary-v2.json','audit-v2.json','compute-v2.json'))
    assert a['final'] and c['final'] and not a['active_owned_children']
    assert not a['current_runtime_drift'] and a['scripts_match_prelaunch_freeze'] and a['tokenizer_matches_archived_epoch']
    assert s['recorded_rollouts']==1400 and s['matched_pairs']==700
    assert s['contracts_completed']==s['contracts_passed']==100
    assert len(s['formal_cases'])==35 and all(row['completed']and row['source_valid']and row['rollouts']==40 for row in s['formal_cases'])
    assert len(a['completed_results'])==136 # fixed135 plus excluded one-pair timing pilot
    assert all(row['source_same_frozen_epoch']and row['worker_hash_matches']and row['helper_hash_matches']and row['tokenizer_hash_matches']and row['exit_code']==0 and row['reaped']for row in a['completed_results'])
    assert set(s['observed_rollout_capacity_policies'])<= {'SupportCapacity','missing'}
    assert set(s['observed_rollout_long_matching'])<= {'False','missing'}
    files={}
    for name in ('summary-v2.json','audit-v2.json','compute-v2.json','table-v2.md'):
        source=HERE/name;destination=source.with_name(source.stem+'-final'+source.suffix)
        assert not destination.exists(), 'Closed output already exists; preserve it'
        shutil.copy2(source,destination);files[destination.name]=hashlib.sha256(destination.read_bytes()).hexdigest()
    pairs=s['paired_transitions'];gains=sum(row['count']for row in pairs if row['baseline']=='dead_end_proxy'and row['lookahead']=='complete_proxy');regressions=sum(row['count']for row in pairs if row['baseline']=='complete_proxy'and row['lookahead']=='dead_end_proxy')
    b=s['total_by_arm']['baseline'];on=s['total_by_arm']['lookahead']
    recommendation=('Use this opt-in policy for the next constrained-decoding pilot; actual model-generation and physical-checker pass-rate gains remain unmeasured.'if gains>regressions else 'Keep this opt-in policy off by default: no net rollout completion benefit was demonstrated.')
    text=f"""The separate v2 contract passed 100/100 historical checker-bound plans (50 tiny, 50 small), {s['contract_tokens']:,} real Glimmer tokens and {s['contract_actions']:,} actions. All 1,400 fresh random-rollout records are saved. No random plan was subjected to the physical checker; random completion/dead-end outcomes are proxies.

Baseline completion proxy {b.get('complete_proxy',0)}/700 versus v2 {on.get('complete_proxy',0)}/700; dead ends {b.get('dead_end_proxy',0)} versus {on.get('dead_end_proxy',0)}. Matched dead→complete gains {gains}; complete→dead regressions {regressions}. CPU limits and errors remain explicit in table-v2-final.md. Policy is actual SupportCapacity, support_conditioned=True, long_matching=False, C2 off, production→lookahead→securing. Policy telemetry counts are preserved in summary-v2-final.json, including missing telemetry on interrupted queries if any.

The layer recorded {s['lookahead_coverage'].get('rejected_actions',0):,} candidate-specific rejections and {s['lookahead_coverage'].get('state_rejections',0):,} whole-state rejections from {s['lookahead_coverage'].get('candidate_probes',0):,} probes. At shared prefixes, {s['shared_prefix_actions_removed']:,} visible actions were removed across {s['shared_prefix_visible_action_reductions']} states. Whole-state closures do not increment the candidate-specific counter. Deferred/unknown scope remains in summary-v2-final.json; uncertainty is never treated as proof of infeasibility.

Matched-prefix extra-query wall (new query minus warm cached baseline): mean {s['matched_extra_query_ms']['mean']:.3f} ms, p95 {s['matched_extra_query_ms']['p95']:.3f} ms across {s['matched_prefixes']} ACTION states. Dividing by {s['matched_selected_action_tokens']:,} actual chosen Glimmer ACTION tokens gives {s['amortized_extra_query_ms_per_selected_action_token']:.3f} ms/token amortized. This is accounting, not model-generation latency. Wrapper initialization and whole-factory initialization are separate fields. Action histories are independent but native instance caches and exact securing memoization are shared; arm wall time does not isolate cold filter overhead.

{recommendation}

All 135 required results plus the separate one-pair timing pilot match the frozen source/helper/controller/tokenizer epoch, with no source exclusions. All direct children exited 0 and were reaped. Archived runtime-v2/ preserves exact sources. Runtime-v1/ and all closed v1 summaries remain immutable.

Measured full campaign worker CPU (including timing pilot): {c['campaign_completed_worker_cpu_s']:.3f} s; cooperative sleep: {c['campaign_completed_worker_sleep_s']:.3f} s; elapsed UTC span: {c['campaign_utc_span_s']:.3f} s. Per-controller estimates, measured walls, owned PIDs and per-worker lifetime RSS are in compute-v2-final.json. RSS peaks are not aggregate concurrent usage. Commands and timing qualifications: REPRODUCTION_V2.md.

"""
    text+=(HERE/'table-v2-final.md').read_text();(HERE/'RESULTS_V2.md').write_text(text);files['RESULTS_V2.md']=hashlib.sha256((HERE/'RESULTS_V2.md').read_bytes()).hexdigest()
    (HERE/'FINAL_V2.json').write_text(json.dumps(dict(files=files,gains=gains,regressions=regressions,source_epoch=read('runtime-freeze-v2.json')['epoch'],archive_manifest_sha256=hashlib.sha256((HERE/'runtime-v2/archive-manifest.json').read_bytes()).hexdigest()),indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(final=True,gains=gains,regressions=regressions,arms=s['total_by_arm'])))


if __name__=='__main__':
    main()
