"""Produce paired rule margins and gate counts from this experiment's score rows."""
from pathlib import Path
import argparse,csv,hashlib,json,statistics
HERE=Path(__file__).resolve().parent
ARMS=('dive','iterated','walk')
KEYS=('securing_ratio','trim_m','sight_m','sight_rad','draft_m','gm_m','second_check_area_mrad','second_check_angle_rad','second_check_gz_m','heel_rad','bending_ratio','displacement_kg')

def main(directory):
    directory=Path(directory)
    rows=[json.loads(p.read_text()) for p in sorted(directory.glob('*.json')) if p.name!='summary.json']
    rows=[r for r in rows if 'instance_id' in r]
    flat=[]
    for row in rows:
        variants=row.get('method_metadata',{}).get('variants',{})
        for arm in ARMS:
            v=variants.get(arm,{})
            flat.append(dict(instance_id=row['instance_id'],arm=arm,complete=v.get('complete',False),
                             passed=v.get('passed',False),lifts=v.get('lifts'),solve_s=v.get('solve_s'),
                             **{k:(v.get('margins') or {}).get(k) for k in KEYS}))
    with (directory/'margins.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(flat[0]) if flat else ['instance_id'])
        writer.writeheader();writer.writerows(flat)
    def fmt(x): return 'NA' if x is None else f'{x:.4g}'
    lines=['# Same-root per-rule margins','',
           'Signed legal-positive margins, minima across full-checker voyage conditions. '+
           'Securing and bending are 1 − worst utilization; sight/trim/draft/GM metres; '+
           'angles radians; second-check area metre-radians. NA means no checked complete stow or unavailable margin.',
           '', 'D = inherited dive + greedy balance; I = iterated + KK; W = projected walk + KK.', '']
    fields=[('securing_ratio','Securing'),('trim_m','Trim m'),('sight_m','Sight m'),('sight_rad','Sight rad'),
            ('draft_m','Draft m'),('gm_m','GM m'),('second_check_area_mrad','2nd GM area'),('heel_rad','Heel rad'),('bending_ratio','Bending')]
    for arm in ('iterated','walk'):
        lines += [f'## D → {arm}', '', '| Map | Complete D/after | '+ ' | '.join(label for k,label in fields)+' |',
                  '|---|---|'+ '|'.join('---:' for _ in fields)+'|']
        for row in rows:
            variants=row.get('method_metadata',{}).get('variants',{})
            a,b=variants.get('dive',{}),variants.get(arm,{})
            ma,mb=a.get('margins') or {},b.get('margins') or {}
            cells=[f"{fmt(ma.get(k))} → {fmt(mb.get(k))}" for k,label in fields]
            lines.append('| '+row['instance_id']+' | '+f"{int(a.get('complete',False))}/{int(b.get('complete',False))}"+' | '+' | '.join(cells)+' |')
        lines.append('')
    aggregate={}
    for arm in ARMS:
        values=[r.get('method_metadata',{}).get('variants',{}).get(arm,{}) for r in rows]
        aggregate[arm]=dict(complete=sum(bool(v.get('complete')) for v in values),
                           passed=sum(bool(v.get('passed')) for v in values),
                           mean_solve_s=statistics.mean(v.get('solve_s',0) for v in values) if values else None,
                           mean_wall_s=statistics.mean(v.get('wall_s',0) for v in values) if values else None,
                           witness_fallbacks=sum(bool(v.get('rounding',{}).get('completion',{}).get('fallback_witness')) for v in values))
    paired=[r for r in rows if all(r.get('method_metadata',{}).get('variants',{}).get(a,{}).get('complete') for a in ARMS)]
    drift_summary={}
    for arm in ARMS:
        values=[r['method_metadata']['variants'][arm].get('drift',{}) for r in paired]
        drift_summary[arm]={key:statistics.mean(abs(v[key]) for v in values if key in v)
                            for key in ('lcg_tm','tcg_tm','vertical_tm','cargo_midship_bending_nm')
                            if any(key in v for v in values)}
        bay=[max((abs(value) for key,value in v.items() if key.startswith('bay_mass_t:')),default=0.) for v in values]
        if bay: drift_summary[arm]['mean_max_bay_mass_drift_t']=statistics.mean(bay)
    data=dict(maps=len(rows),paired_complete_maps=len(paired),arms=aggregate,
              paired_mean_absolute_drift=drift_summary,
              portfolio_complete=sum(any(v.get('complete') for v in r.get('method_metadata',{}).get('variants',{}).values()) for r in rows),
              score_passes=sum(bool(r.get('passed')) for r in rows),
              source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob('*.py'))})
    lines += ['## All failing rule IDs','',
              'Physics margins above retain their native units. E9 clearance and E6 hold-preference failures have no scalar margin in the saved checker PartReport; they are reported categorically here, never assigned a fabricated numeric margin.',
              '', '| Map | Arm | Failing rule IDs |', '|---|---|---|']
    for row in rows:
        for arm in ARMS:
            v=row.get('method_metadata',{}).get('variants',{}).get(arm,{})
            lines.append('| '+row['instance_id']+' | '+arm+' | '+(', '.join(v.get('rules',[])) if v.get('complete') else 'No complete checked stow')+' |')
    lines += ['', '## Gates','', '```json',json.dumps(data,indent=2),'```','']
    (directory/'MARGINS.md').write_text('\n'.join(lines))
    (directory/'comparison.json').write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');args=p.parse_args();main(args.directory)
