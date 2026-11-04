from pathlib import Path
import json,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
s=json.loads((HERE/'summary.json').read_text());p=json.loads((HERE/'packing-summary.json').read_text());pack={r['map_id']:{x['mode']:x for x in r['maps']} for r in p['maps']};rows=[]
for r in s['maps']:
 if r['map_id'] not in pack:continue
 q=pack[r['map_id']]['shared'];d=json.loads((ROOT/r['input']).read_text());cargo=[b for b in d['containers'] if b['id'] in d['load_list']];hatches={h['id']:h for h in r['hatches']};podorder=sorted({b['pod'] for b in cargo},key=lambda pod:min(c['index'] for c in d['rotation']['calls'] if c['locode']==pod and c['index']>d['call_index']));regions=[]
 for i,pod in enumerate(podorder):
  hs=[h for j in q['shared_station_runs'][i] for h in q['stations'][j]['covers']];bs=[b for b in cargo if b['pod']==pod];physical=sum(1 if b['length_ft']==20 else 2 for b in bs);hc=sum(hatches[h]['hold_cells'] for h in hs);cap=sum(hatches[h]['cells'] for h in hs);hphysical=sum(1 if b['length_ft']==20 else 2 for b in bs if b['weight_class']=='H');mass=sum(b['gross_mass_kg'] for b in bs);mc=sum(hatches[h]['optimistic_mass_cap_kg'] for h in hs);regions.append({'pod':pod,'allowed_hatches':hs,'physical_demand_cells':physical,'allowed_cell_capacity':cap,'individual_region_cell_fill':physical/cap,'heavy_physical_cell_demand':hphysical,'allowed_hold_cells':hc,'heavy_cells_per_hold_cell':hphysical/hc,'mass_kg':mass,'optimistic_mass_capacity_kg':mc,'individual_region_mass_fill':mass/mc,'note':'Shared station capacities are available to both PODs; individual ratios cannot be summed. Joint fractional LP enforces competition.'})
 allocation=[]
 for h in q.get('hatches',[]):
  cap=hatches[h['id']];allocation.append({**h,'capacity_cells':cap['cells'],'hold_capacity_cells':cap['hold_cells'],'optimistic_mass_capacity_kg':cap['optimistic_mass_cap_kg'],'fractional_witness_cell_fill':h['cells']/cap['cells'],'fractional_witness_mass_fill':h['mass_kg']/cap['optimistic_mass_cap_kg'],'note':'One feasible optimal LP allocation, not a necessary per-hatch lower bound.'})
 rows.append({'map_id':r['map_id'],'shared_regions':regions,'shared_lp_allocation':allocation,'shared_minimax_stack_utilisation':q.get('minimax_stack_utilisation'),'lp_status':q['status']})
out={'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'maps':rows,'all12':len(rows)==12};(HERE/'hatch-tightness.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
