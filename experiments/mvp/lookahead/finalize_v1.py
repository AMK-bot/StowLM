"""Close and preserve the complete fixed v1 campaign, without numerical reruns."""
import hashlib
import json
import shutil
from pathlib import Path

from worker import HERE
import summarize
import audit
import compute


def read(name):
    return json.loads((HERE/name).read_text())


def main():
    summarize.main(); audit.run(); compute.run()
    summary, provenance, resources = (read(name) for name in ('summary.json', 'audit.json', 'compute.json'))
    assert provenance['final'] and resources['final'], 'Campaign still active'
    assert not provenance['current_runtime_drift']
    assert provenance['scripts_match_prelaunch_freeze']
    assert summary['contracts_passed'] == summary['contracts_completed'] == 100
    assert summary['recorded_rollouts'] == 1400
    assert len(summary['formal_cases']) == 35 and all(c['completed'] and c['source_valid'] and c['rollouts'] == 40 and not c['error'] for c in summary['formal_cases'])
    assert all(g['recorded'] == 700 for g in summary['total_by_arm'].values())
    assert all(row['source_same_frozen_epoch'] and row['worker_hash_matches'] and row['helper_hash_matches'] and row['tokenizer_hash_matches'] and row['exit_code'] == 0 and row['reaped'] for row in provenance['completed_results'])
    preserved = {}
    for name in ('summary.json', 'audit.json', 'compute.json', 'table.md'):
        source = HERE/name; destination = source.with_name(source.stem+'-v1'+source.suffix)
        if destination.exists():
            assert destination.read_bytes() == source.read_bytes(), f'Immutable result already exists: {destination}'
        else:
            shutil.copy2(source, destination)
        preserved[destination.name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    totals = summary['total_by_arm']; b=totals['baseline']; on=totals['lookahead']
    text = f"""All 100 historical checker-bound plans (50 tiny, 50 small) replayed successfully through the real Glimmer tokenizer and final production + lookahead + securing token mask: {summary['contract_tokens']:,} tokens, {summary['contract_actions']:,} actions. No physical checker was rerun by this experiment.

The fixed 35-instance random-action experiment is complete: 20 rollouts per arm per input, 1,400 records. Baseline completion proxy {b.get('complete_proxy',0)}/700 versus lookahead {on.get('complete_proxy',0)}/700; dead-end proxy {b.get('dead_end_proxy',0)} versus {on.get('dead_end_proxy',0)}. These are action-mask outcomes, not checker-accepted random plans. All {summary['matched_pairs']} paired seeds were preserved; {summary['identical_action_traces']} pairs had identical action traces.

Lookahead rejected {summary['lookahead_coverage'].get('rejected_actions',0)} actions after {summary['lookahead_coverage'].get('candidate_probes',0):,} probes. Unknown-state counts and deterministic work-cap deferrals remain in summary-v1.json. This measured v1 has no demonstrated rollout benefit if the two completion counts remain equal; keep it opt-in while stronger necessary bounds are evaluated separately.

Matched-prefix extra default query timing: mean {summary['matched_query_ms']['mean']:.3f} ms, p95 {summary['matched_query_ms']['p95']:.3f} ms across {summary['matched_prefixes']} ACTION states. Wrapper initialization mean {summary['matched_wrapper_init_ms']['mean']:.3f} ms and one-time geometry initialization mean {summary['one_time_static_init_ms']['mean']:.3f} ms are separate. This is not a per-token overhead claim. Both arms share native instance caches and exact securing memoization; arm wall-time differences do not isolate filter cost. No recorded profile establishes the cause of long native legal queries.

All completed runtime snapshots match the frozen v1 epoch with no exclusions; worker/helper/tokenizer hashes also match. runtime-v1/ preserves the exact source bytes for later reproduction. The controller hash was captured at archive time, without a pre-launch cryptographic reference. Raw files remain untouched.

Formal measured worker CPU: {resources['formal_completed_worker_cpu_s']:.3f} s; cooperative sleep {resources['formal_completed_worker_sleep_s']:.3f} s; elapsed formal UTC span {resources['formal_utc_span_s']:.3f} s. See compute-v1.json for each controller estimate versus actual, owned child PIDs, exits, reaping and lifetime RSS. Max worker RSS is not a concurrent aggregate. Native threads1, no GPU; nice was sandbox-denied and formal duty was90%.

Commands and measurement qualifications: REPRODUCTION.md. Detailed fixed-denominator table: table-v1.md. Source/process audit: audit-v1.json.

"""
    text += (HERE/'table-v1.md').read_text()
    (HERE/'RESULTS_V1.md').write_text(text)
    preserved['RESULTS_V1.md'] = hashlib.sha256((HERE/'RESULTS_V1.md').read_bytes()).hexdigest()
    (HERE/'FINAL_V1.json').write_text(json.dumps(dict(files=preserved, archive_manifest_sha256=hashlib.sha256((HERE/'runtime-v1/archive-manifest.json').read_bytes()).hexdigest(), outcome='Complete v1 measurement, source-matched, random outcomes are proxies only.'),indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(final=True, **summary['total_by_arm'])))


if __name__ == '__main__':
    main()
