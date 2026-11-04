"""Metadata-only inventory; val.jsonl is opened solely to extract exclusion identifiers."""
import collections
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
GOLD = ROOT / 'experiments/mvp/gold'
HERE = GOLD / 'twostep-teacher'
OUT = Path(__file__).resolve().parent

def read(p):
    return json.loads(p.read_text())

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def write(name, value):
    (OUT/name).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')

def counts(entries):
    return dict(sorted(collections.Counter(e['ship'] for e in entries).items()))

val = ROOT/'experiments/mvp/sft/stowlm-v2/data/val.jsonl'
# Do not retain tokens or plan content from these rows; only IDs and gold source exclusions.
exclusions = []
with val.open() as stream:
    for line in stream:
        row = json.loads(line)
        source = row['meta']['gold']['source']
        case = str(Path(source.split('experiments/mvp/gold/',1)[1]).parent)
        exclusions.append({'instance_id':row['instance_id'], 'case':case})
small_exclusions = [r for r in exclusions if r['case'].startswith('train/small/')]
assert len(small_exclusions) == 40
excluded_cases = {e['case'] for e in exclusions}
excluded_ids = {e['instance_id'] for e in exclusions}
write('val-id-exclusions.json', {'source':str(val), 'source_sha256':sha(val), 'small_count':40, 'entries':exclusions})

snapshot = read(HERE/'maps.json')
snapshot_small = {e['case'] for e in snapshot if e['rung']=='small'}
d151 = read(HERE/'d151-manifest.json')
d151_entries = {e['case']:e for e in d151['entries']}
winners = {}
for p in (HERE/'best-d151').glob('*/record.json'):
    x = read(p)
    winners[x['provenance']['case']] = {'path':str(p),'sha256':sha(p),'source':x['provenance']['source']}

eligible, excluded, rejected, passed = [], [], [], []
for p in sorted((GOLD/'train/small').glob('*/*/*/result.json')):
    r = read(p); case = str(p.parent.relative_to(GOLD))
    common = dict(case=case, ship=r.get('ship',case.split('/')[2]), instance_id=r.get('instance_id'), completed=r.get('completed'), passed=r.get('passed'), drop_reason=r.get('drop_reason'), finished_utc=r.get('finished_utc'))
    if not (r.get('completed') is True and r.get('passed') is True):
        rejected.append(common)
        continue
    passed.append(common)
    # Held-out IDs and case paths are excluded before accessing a train input or plan.
    if case in excluded_cases or r.get('instance_id') in excluded_ids:
        excluded.append(common)
        continue
    assert r.get('split')=='train' and r.get('rung')=='small'
    input_path = Path(r['input_path'])
    assert (ROOT/'experiments/mvp/corpus/train/small') in input_path.resolve().parents
    plan,checker = p.with_name('plan.json'),p.with_name('checker.json')
    problems=[]
    if not plan.exists() or sha(plan)!=r.get('plan_sha256'): problems.append('plan missing/hash differs')
    if not input_path.exists() or sha(input_path)!=r.get('input_sha256'): problems.append('input missing/hash differs')
    c = read(checker) if checker.exists() else {}
    if c.get('passed') is not True: problems.append('checker missing/not passed')
    if c.get('count',{}).get('weighted_nonproductive')!=r.get('objective'): problems.append('checker objective differs')
    if r.get('stages',{}).get('mask',{}).get('passed') is not True: problems.append('mask not passed')
    if r.get('relevant_changed_source_files'): problems.append('source drift')
    if problems:
        rejected.append(dict(common, verification_problems=problems)); continue
    rowp = HERE/'rows'/(case.replace('/','__')+'.json')
    row = read(rowp) if rowp.exists() else None
    e = dict(common, family=r['family'], solver=r['solver'], policy=r.get('policy'), input=str(input_path), input_sha256=r['input_sha256'], gold_plan_sha256=r['plan_sha256'], result_path=str(p), result_sha256=sha(p), checker_sha256=sha(checker), stored_moves=r['objective'], in_maps_snapshot=case in snapshot_small, in_d151_manifest=case in d151_entries, d151_mode=d151_entries.get(case,{}).get('mode'), d151_winner=winners.get(case), existing_row=str(rowp) if row else None, existing_row_sha256=sha(rowp) if row else None, existing_two_step_attempted=bool(row and (row.get('two_step') or {}).get('attempted')), existing_row_crashed=bool(row and row.get('crashed')))
    eligible.append(e)

eligible_cases = {e['case'] for e in eligible}
small_winners = {c for c in winners if c.startswith('train/small/')}
missing_winner = [e for e in eligible if e['case'] not in winners]
no_row = [e for e in eligible if e['existing_row'] is None]
post_snapshot = [e for e in eligible if not e['in_maps_snapshot']]
summary = dict(schema='d172-small-gold-inventory-v1', generated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), raw_case_results=len(passed)+len(rejected), completed_passing_gold=len(passed), completed_passing_by_ship=counts(passed), held_out_passing_small=len(excluded), held_out_passing_by_ship=counts(excluded), eligible_verified_gold=len(eligible), eligible_by_ship=counts(eligible), rejected_or_unfinished=len(rejected), rejected_by_ship=counts(rejected), maps_snapshot_small=len(snapshot_small), eligible_absent_from_snapshot=len(post_snapshot), eligible_absent_from_snapshot_by_ship=counts(post_snapshot), existing_d151_small_winners=len(small_winners), eligible_with_d151_winner=len(eligible_cases & small_winners), eligible_no_d151_winner=len(missing_winner), eligible_no_d151_winner_by_ship=counts(missing_winner), eligible_without_any_row=len(no_row), eligible_without_any_row_by_ship=counts(no_row), existing_d151_all_winners=len(winners), expected_union_with_d151=len(eligible_cases | set(winners)), val_source_sha256=sha(val), maps_snapshot_sha256=sha(HERE/'maps.json'), d151_manifest_sha256=sha(HERE/'d151-manifest.json'), assumptions=['Admit only completed=true and passed=true gold with current stored input/plan hashes, passing stored checker with matching objective, passing recorded mask stage, no relevant source drift. Current-epoch re-verification remains the teacher pipeline responsibility.','Retain D151 gold_only winners; do not re-run warmstart gold-only rows merely because they have no two-step candidate.','Retry the D151 dropped map because its gold is eligible and it has no D151 winner, despite an existing timeout teacher row.','Metadata and stored result/checker artifacts only; never opened held-out input files. val.jsonl opened only for exclusion case/instance IDs.'])
summary['unfinished_count'] = sum(e.get('completed') is not True for e in rejected)
summary['completed_failed_count'] = sum(e.get('completed') is True for e in rejected)
summary['unfinished_cases'] = [e['case'] for e in rejected if e.get('completed') is not True]
summary['post_snapshot_finish_utc_min'] = min(e['finished_utc'] for e in post_snapshot)
summary['post_snapshot_finish_utc_max'] = max(e['finished_utc'] for e in post_snapshot)
write('summary.json',summary)
write('eligible-small.json',eligible)
write('gold-passed-held-out-excluded.json',excluded)
write('rejected-or-unfinished.json',rejected)
write('missing-d151-winner.json',missing_winner)
write('no-existing-row.json',no_row)
write('post-maps-snapshot.json',post_snapshot)
write('d151-winners.json',winners)
print(json.dumps(summary,indent=2))
