"""Clarify physical occupancy vs nominal TEU and tank/background accounting."""
from pathlib import Path
import json,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
summary=json.loads((HERE/'summary.json').read_text());extra={r['map_id']:r for r in json.loads((HERE/'inventory-extra.json').read_text())}
background={r['map_id']:r for r in json.loads((HERE/'background-inventory.json').read_text())}
for row in summary['maps']:
 x=extra[row['map_id']];p=ROOT/row['input'];assert sha(p)==row['sha256'];d=json.loads(p.read_text());cargo=[b for b in d['containers'] if b['id'] in d['load_list']];nominal=sum(b['length_ft']/20 for b in cargo);physical=sum(1 if b['length_ft']==20 else 2 for b in cargo);assert nominal==x['nominal_teu'];assert physical==x['physical_cell_demand']
 row.update(x);row.update(nominal_teu_fraction=nominal/row['capacity_teu'],physical_cell_fraction=physical/row['capacity_teu'],physical_cell_capacity=row['capacity_teu'],movable_onboard_placements=len(d['onboard']['placements']),all_initial_tank_masses_kg=row['initial_ballast_kg'],legacy_field_notes={'teu':'physical_cell_demand; forty-five-foot consumes2cells','teu_fraction':'physical_cell_fraction','initial_ballast_kg':'all_initial_tank_masses_kg including consumables; use initial_adjustable_ballast_kg for ballast only'})
 row.update(background[row['map_id']])
 row['reefer_plug_cells']=sum(c['reefer_plug'] for c in d['vessel']['cells'])
 audit=json.loads((ROOT/(row['input'].removesuffix('.json')+'.audit.json')).read_text())
 row['full_export_capacity_teu']=audit['booking_reference']['full_capacity_teu']
 row['decision_region_fraction_of_full_capacity']=row['capacity_teu']/row['full_export_capacity_teu']
 stack={s['id']:s for s in d['vessel']['stacks']}
 for h in row['hatches']:
  h['reefer_plug_cells']=sum(c['reefer_plug'] for c in d['vessel']['cells'] if stack[c['stack_id']]['hatch_cover_id']==h['id'])
 for pod in row['pod']:
  bs=[b for b in cargo if b['pod']==pod['pod']]
  pod.update(nominal_teu=sum(b['length_ft']/20 for b in bs),physical_cell_demand=sum(1 if b['length_ft']==20 else 2 for b in bs))
 (HERE/'maps'/row['map_id']/'summary.json').write_text(json.dumps(row,indent=2,sort_keys=True)+'\n')
summary['augmentation']={'script_sha256':sha(__file__),'explicit_capacity_metrics':True,'original_generation_binding_preserved':True};(HERE/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
