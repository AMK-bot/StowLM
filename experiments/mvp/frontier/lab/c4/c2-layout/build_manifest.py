"""Freeze all approved TRAIN ladder maps and extract saved C3 diagnostic evidence.

No solvers or checkers run. Input bytes are opened only after catalog.ensure_safe.
Only outputs inside this script's directory are written; no caches are generated.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = ROOT / 'experiments/mvp/frontier/lab'
sys.path.insert(0, str(LAB))
from catalog import ensure_safe, load_set


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def write_json(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


CODES = {
    'expert.e9.over_width_length': 'E9',
    'iacs.member_irs.container_securing': 'S',
    'iscode.loading.draft': 'D',
    'iscode.loading.trim': 'T',
    'solas.v22.visibility': 'V',
    'iscode.a2_3.weather': 'W',
    'iscode.b2_3.containership_second_check': 'A',
}


def codes(rules):
    return ','.join(CODES.get(r, r) for r in rules) or 'none'


def build():
    rows = []
    metadata_sources = []
    for path in sorted((ROOT / 'experiments/mvp/scale/runs/v6/maps').glob('*/summary.json')):
        summary = json.loads(path.read_text())
        job = summary.get('job', {})
        if job.get('split') != 'train' or job.get('stage') not in ('f60', 'f70', 'f80'):
            continue
        approved = ensure_safe(job['input'])
        input_path = ROOT / approved
        digest = sha(input_path)
        assert digest == job['input_sha256'], approved
        data = json.loads(input_path.read_text())
        rows.append(dict(input=approved, input_sha256=digest,
                         instance_id=data['id'], rung=job['stage'], ship=job['ship'],
                         seed=job['seed'], mass_level=job['mass_level'], n_boxes=job['n_loads'],
                         provenance='TRAIN', selection='All TRAIN V6 metadata jobs at f60/f70/f80, regardless of outcome.',
                         metadata=relative(path), metadata_sha256=sha(path),
                         v6_passed=summary.get('checker_passed') is True,
                         v6_comparator_scope='Pass constructor only; not a lift benchmark.'))
        metadata_sources.append(dict(path=relative(path), sha256=sha(path)))
    for approved in load_set('medium12'):
        input_path = ROOT / approved
        data = json.loads(input_path.read_text())
        rows.append(dict(input=approved, input_sha256=sha(input_path),
                         instance_id=data['id'], rung='medium12', provenance='TRAIN',
                         selection='All maps in the frozen lab medium12 development list.',
                         metadata='experiments/mvp/frontier/lab/sets/medium12.txt',
                         n_boxes=len(data['load_list'])))
    for rung in ('f60', 'f70', 'f80', 'medium12'):
        chosen = [r for r in rows if r['rung'] == rung]
        assert len(chosen) == 12, (rung, len(chosen))
        assert len({r['input'] for r in chosen}) == len(chosen)
        (HERE / f'{rung}.txt').write_text(''.join(r['input']+'\n' for r in chosen))
    assert len({r['input'] for r in rows}) == len(rows) == 48
    (HERE / 'ladder.txt').write_text(''.join(r['input']+'\n' for r in rows if r['rung'] != 'medium12'))
    write_json('MANIFEST.json', rows)
    write_json('cohort_evidence.json', dict(schema_version=1, source_metadata=metadata_sources,
        catalog_source=dict(path=relative(LAB/'catalog.py'), sha256=sha(LAB/'catalog.py')),
        medium12_list=dict(path=relative(LAB/'sets/medium12.txt'), sha256=sha(LAB/'sets/medium12.txt')),
        selected_before_new_results=True, sealed_data_opened=False,
        counts={rung:sum(r['rung']==rung for r in rows) for rung in ('f60','f70','f80','medium12')}))
    failures = []
    c3 = LAB/'c3/c2-layout'
    for rung, run in (('f60','f60-1'),('f70','f70-lp')):
        run_rows=[]
        for row in rows:
            if row['rung'] != rung:
                continue
            path=c3/'results'/run/f"{row['instance_id']}.json"
            saved=json.loads(path.read_text())
            assert saved['input']==row['input'] and saved['input_sha256']==row['input_sha256']
            run_rows.append(saved)
            if saved.get('passed'):
                continue
            trials=saved.get('method_metadata',{}).get('trials',[])
            checked=[dict(index=i, **{k:v for k,v in t.items() if k in
                    ('kind','rules','lifts','full_check','replay_legal','heel','bow_down','seconds','settings','conflicts')})
                     for i,t in enumerate(trials) if 'rules' in t and t.get('full_check') is False]
            cold=next((t for t in checked if t['kind']=='cold_reference'),None)
            # Closest checked candidate = fewest failing rule IDs, then lifts, then trial order.
            # This ranks diagnostic rule counts, not physical distance to feasibility.
            closest=min(checked, key=lambda t:(len(t['rules']),t.get('lifts',float('inf')),t['index']), default=None)
            uncompleted=[dict(index=i, **{k:v for k,v in t.items() if k in
                  ('kind','status','reason','lp_uncovered','left','columns','closed_hold')})
                  for i,t in enumerate(trials) if 'rules' not in t]
            failures.append(dict(instance_id=row['instance_id'],input=row['input'],rung=rung,
                result=relative(path),result_sha256=sha(path),status=saved.get('status'),
                failure_reason=saved.get('failure_reason'),
                complete_replay_observed=saved.get('method_metadata',{}).get('replay_legal') is True,
                returned_candidate_count=len(saved.get('tries',[])),
                cold_reference=cold,closest_checked=closest,all_checked_trials=checked,
                incomplete_or_screened_trials=uncompleted,
                interpretation='Search returned no candidates; checked trial rules describe those stows only. Restricted page-pool uncovered mass or search timeout is not a physical infeasibility certificate.'))
        assert len(run_rows)==12
    assert len(failures)==13
    write_json('failures_c3.json', dict(schema_version=1, codes=CODES, failures=failures,
         closest_candidate_definition='Fewest distinct failing rule IDs, then lifts, then trial order; not a physical feasibility metric.',
         evidence='Immutable score row embedded method_metadata.trials; mutable C3 trace files not used.'))
    lines=['# Cycle 3: all 13 failed ladder maps','',
       'The complete fixed cohorts contain 12 maps per rung. C3 f60 passed 8/12; f70 passed 3/12. All 24 had a complete replay-legal trial. Each failed score row has `status=no_candidates`: the method discarded all failed trial stows before returning to the runner. No failed map is proved infeasible.','',
       'Codes: **S** securing (`iacs.member_irs.container_securing`); **D** draft; **T** trim; **V** visibility; **E9** over-width/length OOG clearance; **W** weather; **A** containership second check. A saved rule ID does not identify which second-check metric failed: do not silently equate A with area-to-flood.','',
       '“Closest” means fewest distinct failing rule IDs, then lowest lifts, then trial order. It is only a diagnostic ranking. Full rule IDs, all checked trials, restricted-pool failures and result SHA-256 hashes are in [failures_c3.json](failures_c3.json).','',
       '| Map (D192 prefix omitted) | Cold trial rules / lifts | Closest checked rules / lifts | Search outcome |',
       '|---|---|---|---|']
    for f in failures:
        cold=f['cold_reference'];best=f['closest_checked']
        fam=f['incomplete_or_screened_trials']
        nlp=sum(t.get('lp_uncovered',0)>1e-6 for t in fam)
        ncap=sum(t.get('status')=='page cell capacity screen' for t in fam)
        ntime=sum(t.get('reason')=='time' for t in fam)
        outcome=f'no candidates; {nlp} restricted LP uncovered'
        if ncap: outcome+=f'; {ncap} cell screens'
        if ntime: outcome+=f'; {ntime} timed rounds'
        def fmt(t):
            return f"{codes(t['rules'])} / {t.get('lifts','?'):g}" if t else 'not recorded'
        lines.append(f"| {f['instance_id'].removeprefix('D192.medium-')} | {fmt(cold)} | {fmt(best)} | {outcome} |")
    lines+=['','## What this means for Cycle 4','',
      'The six f60/f70 feeder_800 high-mass cold trials all fail securing, trim and visibility; five also fail draft and four E9. Their later restricted masters fail to cover all boxes, so the recorded results do not establish whether moment-aware pricing can produce a legal complete alternative.',
      '',
      'All three feeder_500 f70 low-mass cases reach checked candidates failing securing alone. Their cold stows additionally fail second-check/visibility (and one weather); scoring only scalar GM cannot diagnose the second check. The feeder_800 f60 low seed192200100 reaches a 103-lift candidate failing E9 alone: geometry repair remains a distinct construction issue.',
      '',
      'The three feeder_800 f70 low-mass cases retain only cold checked stows, failing securing and visibility, with E9/trim on seeds100 and101. Later closure searches are incomplete or screened; none proves infeasibility.',
      '',
      'C3 Benders establishes the actual binding second-check metric on its accepted D185 witness as `containership_area_0_flood_mrad`, arrival area margin 0.000158135 m·rad, while all securing rules pass. This transfers the correct diagnostic/search target, not a universal binding claim for every D192 map. See [BINDINGS.md](../../c3/c2-benders/BINDINGS.md).',
      '',
      'V6 is a constructor built to pass, not a lift benchmark. C3’s four warm primary maps were chosen from V6-passing maps and are preselected as passable; the whole 12-map ladder cohorts above avoid that filter. Medium lift totals use the fixed-stow, no-later-loads D15 projection.',
      '',
      'Reproduce with `PYTHONDONTWRITEBYTECODE=1 python3 experiments/mvp/frontier/lab/c4/c2-layout/build_manifest.py`. No solver or checker is run; source rows supply historical diagnoses.']
    (HERE/'FAILURES-C3.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(counts={rung:sum(r['rung']==rung for r in rows) for rung in ('f60','f70','f80','medium12')},c3_failures=len(failures),v6_passes={rung:sum(r.get('v6_passed',False) for r in rows if r['rung']==rung) for rung in ('f60','f70','f80')})))


if __name__=='__main__':
    build()
