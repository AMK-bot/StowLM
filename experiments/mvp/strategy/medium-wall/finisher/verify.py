"""Reconcile finished train diagnostic artifacts and pin identities."""
from pathlib import Path
import json
import time
import run

assert (run.HERE/'completion.json').exists() and (run.HERE/'postcheck-completion.json').exists()
snapshot=run.read(run.RUNTIME/'snapshot.json');cache=run.read(run.RUNTIME/'cache-snapshot.json')
for manifest in (snapshot,cache):
    for rel,digest in manifest['files'].items():assert run.sha(run.RUNTIME/rel)==digest,rel
cohort=run.read(run.ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json');assert cohort['split']=='train'
results=[]
for task in run.read(run.HERE/'tasks.json'):
    job=next(j for j in cohort['maps'] if j['map_id']==task['map_id'])
    assert run.sha(run.ROOT/job['input'])==job.get('input_sha256',job.get('sha256'))
    f=run.HERE/'runs'/task['name'];complete=run.read(f/'complete.json');outcome=run.read(f/'outcome.json')
    assert complete['status']==outcome['status'] and complete['cause']==outcome['not_found_cause']
    assert complete['raw_sha256']==run.sha(run.ROOT/task['raw_result'])
    assert complete['snapshot_sha256']==snapshot['snapshot_sha256']
    diagnostics=outcome['diagnostics'];trace=[json.loads(line) for line in (f/'evaluations.jsonl').read_text().splitlines()]
    counter_gap=len(trace)-diagnostics['evaluations']-diagnostics['cache_hits']
    assert counter_gap in (0,1)
    if counter_gap:assert trace[-1]['error'] in ('Deadline','ExactDeadline')
    rep=run.read(f/'representative-summary.json');plan=run.read(f/'representative-plan.json');report=run.read(f/'representative-full-report.json')
    selected=trace[rep['evaluation_line_zero_based']]
    assert selected==rep['search_row'] and selected['selected_keys'] is None and selected['error'] is None
    assert rep['checker']==run.summary(report) and rep['checker']['passed']==report['passed']
    instance=run.read(run.ROOT/job['input'])
    tanks=sorted((t for t in instance['vessel']['tanks'] if t['adjustable']),key=lambda t:t['id'])
    expected={t['id']:fraction*t['capacity_m3']*t['content_density_kg_m3'] for t,fraction in zip(tanks,selected['fractions'])}
    assert plan['plan']['ballast']['tank_masses_kg']==expected
    results.append(dict(map_id=task['map_id'],status=outcome['status'],cause=outcome['not_found_cause'],
        observer_calls=len(trace),evaluations=diagnostics['evaluations'],cache_hits=diagnostics['cache_hits'],
        attempts_stopped_before_evaluation_counter=counter_gap,
        representative_full_checker_passed=report['passed'],representative_rules=rep['checker']['rules']))
for row in run.read(run.HERE/'visibility-attribution.json'):
    for phase in row['conditions'].values():
        assert isinstance(phase['groups']['fixed_and_covers']['geometric_rules_pass'],bool)
        assert phase['groups']['fixed_and_covers']['geometric_rules_pass'] is True
        for k in ('blind_distance_m','max_blind_sector_rad','total_blind_sector_rad','min_clear_sector_rad'):
            assert phase['groups']['all'][k]==phase['groups']['decision_only'][k]
for row in run.read(run.HERE/'lashing-attribution.json'):
    assert set(row['container_reference_counts'])=={'loaded_this_call'}
modules=sorted(run.HERE.glob('*.py'))
for path in modules:compile(path.read_text(),str(path),'exec')
verification=dict(passed=True,unix=time.time(),source_files_checked=len(snapshot['files']),
    cache_files_checked=len(cache['files']),compiled_modules=len(modules),results=results)
run.save(run.HERE/'verification.json',verification)
index={str(p.relative_to(run.HERE)):run.sha(p) for p in run.HERE.rglob('*') if p.is_file()
    and p.name!='evidence-index.json' and p.suffix not in ('.lock',) and '__pycache__' not in p.parts}
run.save(run.HERE/'evidence-index.json',dict(unix=time.time(),sha256=index))
print(json.dumps(verification,indent=2))
