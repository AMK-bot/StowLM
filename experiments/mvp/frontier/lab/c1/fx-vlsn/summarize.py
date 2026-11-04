"""Collect retained evidence; candidate objectives are kept separate from passes."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def main():
    out = dict(candidate_ablation={},checked_runs={},source_sha256={})
    grouped=defaultdict(list)
    records=[]
    for path in sorted((HERE/'results'/'ablation8').glob('*.json')):
        row=json.loads(path.read_text())
        if row.get('missing_start'):
            continue
        records.append(row)
        for arm in row['arms']:
            grouped[arm['neighborhood']+'/'+arm['scorer']].append(arm)
    out['candidate_ablation'].update(maps_with_starts=len(records),
        stored_mean_lifts=statistics.mean(r['stored_lifts'] for r in records) if records else None)
    for key,arms in grouped.items():
        out['candidate_ablation'][key]=dict(mean_candidate_lifts=statistics.mean(a['best'] for a in arms),
            wall_s=sum(a['wall_s'] for a in arms),iterations=sum(a['iterations'] for a in arms),
            evaluations=sum(a.get('evaluations',a['iterations']) for a in arms),
            masks=sum(a.get('masks',0) for a in arms),
            rejected_masks=sum(a.get('mask_rejected',0) for a in arms))
    for folder in ('quick12','candidate_check','ablation_checked','frontier60_checked','lazy12_checked','warm_fusion_checked','from_starts_admitted'):
        summary=HERE/'results'/folder/'summary.json'
        if summary.exists():
            out['checked_runs'][folder]=json.loads(summary.read_text())
    fusion=HERE/'results'/'fusion15.json'
    if fusion.exists():
        out['fusion']=json.loads(fusion.read_text())
    out['fresh_start_comparison']=[]
    for path in sorted((HERE/'results'/'from_starts60').glob('*.json')):
        row=json.loads(path.read_text())
        out['fresh_start_comparison'].append(dict(id=row['id'],
            construction_s=row['construction']['wall_s'],
            arms={key:dict(wall_s=arm['wall_s'],
                            best=arm['candidates'][0]['objective'] if arm['candidates'] else None,
                            stats=arm['stats']) for key,arm in row['arms'].items()}))
    def checked(folder):
        return {r['instance_id']:r for p in (HERE/'results'/folder).glob('*.json')
                if p.name!='summary.json' for r in [json.loads(p.read_text())]
                if r.get('instance_id')}
    union=checked('ablation_checked')
    reference=checked('frontier60_checked')
    paired=sorted(k for k in union.keys() & reference.keys()
                  if union[k]['passed'] and reference[k]['passed'])
    if paired:
        out['checked_union_vs_frontier60']=dict(paired=len(paired),
            union_mean=statistics.mean(union[k]['lifts'] for k in paired),
            frontier60_mean=statistics.mean(reference[k]['lifts'] for k in paired),
            wins=sum(union[k]['lifts']<reference[k]['lifts'] for k in paired),
            ties=sum(union[k]['lifts']==reference[k]['lifts'] for k in paired),
            losses=sum(union[k]['lifts']>reference[k]['lifts'] for k in paired),
            union_actual_search_s=sum(sum(a['wall_s'] for a in r['arms'])+
                                      r['recombination']['elapsed_s'] for r in records),
            union_admission_s=sum(r['wall_s'] for r in union.values()),
            frontier60_total_s=sum(r['wall_s'] for r in reference.values()),
            note='Warm-start search only; same inherited-ballast full-check admission. Union search time is saved online experiment time, not the zero-budget artifact reader.')
    live=checked('lazy12_checked')
    live_pairs=sorted(k for k in live.keys() & reference.keys()
                      if live[k]['passed'] and reference[k]['passed'])
    if live_pairs:
        out['live_lazy_vs_frontier60']=dict(paired=len(live_pairs),
            lazy_mean=statistics.mean(live[k]['lifts'] for k in live_pairs),
            frontier60_mean=statistics.mean(reference[k]['lifts'] for k in live_pairs),
            wins=sum(live[k]['lifts']<reference[k]['lifts'] for k in live_pairs),
            ties=sum(live[k]['lifts']==reference[k]['lifts'] for k in live_pairs),
            losses=sum(live[k]['lifts']>reference[k]['lifts'] for k in live_pairs),
            lazy_total_s=sum(r['wall_s'] for r in live.values()),
            frontier60_total_s=sum(r['wall_s'] for r in reference.values()),
            lazy_propose_s=sum(r['propose_s'] for r in live.values()),
            frontier60_propose_s=sum(r['propose_s'] for r in reference.values()),
            lazy_passes=sum(r['passed'] for r in live.values()),
            frontier60_passes=sum(r['passed'] for r in reference.values()),
            note='Identical stored ordered warm starts and inherited-ballast full-check admission; live method reads no saved search candidate.')
    out['source_sha256']={path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(HERE.glob('*.py'))}
    out['notes']='Candidate ablations are replay/mask results; only checked_runs establish passes. Helper source hashes are at report generation; timer-only instrumentation was added after the ablation started.'
    (HERE/'results'/'REPORT.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out['candidate_ablation'],indent=2))


if __name__=='__main__':
    main()
