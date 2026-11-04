"""Export compact tables from complete, hash-pinned numerical results."""
import csv,json
from pathlib import Path
HERE=Path(__file__).resolve().parent;OUT=HERE/'followup'
curves=[];areas=[];sensitivity=[]
for name in ['feeder_500','feeder_800','feeder_1700']:
 d=json.loads((OUT/f'{name}-C1.json').read_text());b=d['rows'][0]
 areas.append(dict(ship=name,condition='C1',mass_t=b['mass_kg']/1000,kg_m=b['kg_m'],gm_m=b['gm_m'],
  deck_edge_deg=b['deck_edge_deg'],flooding_deg=b['flooding_deg'],
  area_0_30_mrad=b['metrics']['area_0_30_mrad'],area_0_min40_f_mrad=b['metrics']['area_0_40_mrad'],
  area_30_min40_f_mrad=b['metrics']['area_30_40_mrad'],area_0_f_mrad=b['metrics']['area_0_flood_mrad'],
  b_total_area_min_mrad=b['required_b_areas_mrad']['area_0_flood_mrad'],b_total_ratio=b['b_total_area_ratio']))
 for row in d['rows']:
  for x in row['sealed_curve']:curves.append(dict(ship=name,condition='C1',variant=row['variant'],**x))
  sensitivity.append(dict(ship=name,condition='C1',variant=row['variant'],
   delta_area_0_f_mrad=row['metrics']['area_0_flood_mrad']-b['metrics']['area_0_flood_mrad'],
   delta_area_percent=100*(row['metrics']['area_0_flood_mrad']/b['metrics']['area_0_flood_mrad']-1),
   delta_gz30_m=row['sealed_curve'][6]['gz_m']-b['sealed_curve'][6]['gz_m'],
   delta_gz40_m=row['sealed_curve'][8]['gz_m']-b['sealed_curve'][8]['gz_m'],
   phi_f_deg=row['flooding_deg'],b_total_ratio=row['b_total_area_ratio']))
for name,rows in [('feeder-areas.csv',areas),('feeder-gz-to50.csv',curves),('feeder-sensitivities.csv',sensitivity)]:
 with (OUT/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 print(name)
reference=json.loads((HERE/'iges-upper19-results.json').read_text());rows=[]
for r in reference['rows']:
 rows.append(dict(heel_deg=r['heel_deg'],reference_kn_m=r['reference_kn_m'],
  native16_kn_m=r['native16']['kn_m'],vertical19_kn_m=r['vertical19']['kn_m'],
  iges_upper19_kn_m=r['iges_upper']['kn_m'],reference_gz_m=r['reference_gz_m'],
  native16_gz_m=r['native16']['gz_m'],vertical19_gz_m=r['vertical19']['gz_m'],
  iges_upper19_gz_m=r['iges_upper']['gz_m'],iges_kn_error_m=r['error_kn_m'],iges_gz_error_m=r['error_gz_m']))
with (OUT/'reference-like-for-like.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print('reference-like-for-like.csv')
