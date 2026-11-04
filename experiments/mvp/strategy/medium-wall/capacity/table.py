from pathlib import Path
import json,csv
HERE=Path(__file__).resolve().parent;s=json.loads((HERE/'summary.json').read_text());fields=['map_id','seed','ship','family','loads','nominal_teu','physical_cell_demand','physical_cell_capacity','nominal_teu_fraction','physical_cell_fraction','mass_kg','producer_mass_fraction','h','m','l','n20','n40','n45','hold_cells','heavy_cell_demand','pod_mix','fixed_background_mass_kg','movable_onboard_placements','background_box_count','background_nominal_teu','background_occupied_physical_cells','full_geometric_cell_capacity','full_modeled_box_count','full_modeled_nominal_teu','full_modeled_nominal_teu_utilisation','full_modeled_physical_cells','full_modeled_physical_cell_utilisation']
with (HERE/'map-table.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
 for r in s['maps']:
  row={k:r[k] for k in fields if k in r};row.update(producer_mass_fraction=r['generator_mass_fraction'],h=r['classes'].get('H',0),m=r['classes'].get('M',0),l=r['classes'].get('L',0),n20=r['lengths'].get('20',0),n40=r['lengths'].get('40',0),n45=r['lengths'].get('45',0),pod_mix='; '.join(f"{p['pod']}:{p['boxes']}box/{p['nominal_teu']:g}nominalTEU/{p['physical_cell_demand']}cells/{p['mass_kg']/1000:.1f}t HML{p['classes'].get('H',0)},{p['classes'].get('M',0)},{p['classes'].get('L',0)}" for p in r['pod']));w.writerow(row)
