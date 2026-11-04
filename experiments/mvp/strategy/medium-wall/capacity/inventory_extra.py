from pathlib import Path
import json,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
m=json.loads((ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text());rows=[]
for j in m['maps']:
 assert j['split']=='train';assert sha(ROOT/j['input'])==j['sha256'];d=json.loads((ROOT/j['input']).read_text());cargo=[b for b in d['containers'] if b['id'] in d['load_list']];stacks={s['id']:s for s in d['vessel']['stacks']};heavy=[b for b in cargo if b['weight_class']=='H'];fixed=d['vessel']['fixed_loads'];tank={t['id']:t for t in d['vessel']['tanks']};hold=sum(len(s['cell_ids']) for s in stacks.values() if not s['on_deck']);massfixed=sum(f['mass_kg'] for f in fixed);rows.append({'map_id':j['map_id'],'nominal_teu':sum(b['length_ft']/20 for b in cargo),'physical_cell_demand':sum(1 if b['length_ft']==20 else 2 for b in cargo),'hold_cells':hold,'deck_cells':len(d['vessel']['cells'])-hold,'heavy_boxes':len(heavy),'heavy_cell_demand':sum(1 if b['length_ft']==20 else 2 for b in heavy),'fixed_background_mass_kg':massfixed,'background_tcg_m':sum(f['mass_kg']*f['y_m'] for f in fixed)/massfixed,'initial_adjustable_ballast_kg':sum(v for k,v in d['onboard']['tank_masses_kg'].items() if tank[k]['adjustable'])})
(HERE/'inventory-extra.json').write_text(json.dumps(rows,indent=2)+'\n')
