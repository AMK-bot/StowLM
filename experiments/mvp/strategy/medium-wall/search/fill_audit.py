#!/usr/bin/env python3
"""Read-only TRAIN artifact arithmetic; no solver/checker/generator imports or calls."""
import json,hashlib,collections,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5]
HERE=Path(__file__).resolve().parent
B=ROOT/'experiments/mvp/strategy/blockrule'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def cells(slot):
 bay=int(slot[:3]);return [slot] if bay%2 else [f'{bay-1:03d}'+slot[3:],f'{bay+1:03d}'+slot[3:]]
def rel(p):return str(p.relative_to(ROOT))
manifest=B/'cohort-small-train.json';cohort=read(manifest);assert cohort['split']=='train'
runpath=B/'runs/final-small-train/run.json';run=read(runpath);assert run['split']=='small-train' or run['split']=='train',run['split']
rows=[]
for job in cohort['maps']:
 assert job['split']=='train' and '/corpus/train/small/' in job['input']
 inp=ROOT/job['input'];assert sha(inp)==job['sha256'];d=read(inp)
 auditpath=inp.with_name(inp.stem+'.audit.json');a=read(auditpath);assert a['config']['split']=='train' and a['instance_sha256']==sha(inp)
 sp=B/'runs/final-small-train/maps'/job['map_id']/'summary.json';s=read(sp);assert s['complete'] and s['job']['sha256']==sha(inp)
 r=s['selected']['raw']['default'];cargo={x['id']:x for x in d['containers'] if x['id'] in d['load_list']};assert len(cargo)==len(d['load_list'])
 assert not d['onboard']['placements'] and not d['discharge_list']
 mass=sum(x['gross_mass_kg'] for x in cargo.values());nominal=sum(x['length_ft']/20 for x in cargo.values());physical=sum(1 if x['length_ft']==20 else 2 for x in cargo.values());den=len(d['vessel']['cells']);u=a['booking_utilisation']
 assert abs(mass-u['cargo_mass_kg'])<1e-7 and nominal==a['realised_teu'] and den==a['capacity_teu']==d['vessel']['nominal_teu']
 row={k:job[k] for k in ['map_id','seed','ship','family','input','sha256','split','release_epoch','source_binding_status']}
 row.update(loads=len(cargo),length_counts=dict(collections.Counter(x['length_ft'] for x in cargo.values())),decision_cells=den,nominal_teu=nominal,physical_cell_demand=physical,nominal_fill=nominal/den,physical_fill=physical/den,cargo_mass_kg=mass,generator_mass_budget_kg=u['cargo_mass_budget_kg'],mass_budget_fraction=mass/u['cargo_mass_budget_kg'],requested_mass_cap_kg=u['booking_mass_cap_kg'],requested_mass_cap_fraction=a['booking_mass_fraction'],requested_booking_slack=a['booking_slack'],mass_cap_utilisation=mass/u['booking_mass_cap_kg'],onboard_movable_count=0,passed=bool(r['passed']),checker_passed=r.get('checker_passed'),summary_path=rel(sp),summary_sha256=sha(sp),audit_path=rel(auditpath),audit_sha256=sha(auditpath),instance_versions=a['instance_versions'],stage=d['stage'])
 if r['passed']:
  assert r['checker_attempted'] and r['checker_passed'] and r['finalize_status']=='ok' and not r['layout_violations']
  reportpath=ROOT/r['report_path'];report=read(reportpath);assert report['passed'] and not report['violations'] and not report['shut_out']
  fp=ROOT/r['finished_path'];f=read(fp);assert f['planner']=='baseline.twostep' and f['status']=='ok' and not f['repaired']
  placements={}
  for x in f['plan']['actions']:
   assert x['op']=='P';placements[x['box']]=x['slot']
  assert set(placements)==set(cargo)
  occupied=[c for slot in placements.values() for c in cells(slot)];assert len(occupied)==physical and len(set(occupied))==physical and set(occupied)<=set(c['id'] for c in d['vessel']['cells'])
  lineage=r['inheritance'];assert lineage['physical_sources_verified']
  original=ROOT/lineage['original_report_path'];assert sha(original)==lineage['original_report_sha256'] and read(original)['passed']
  row.update(checker_report=rel(reportpath),checker_report_sha256=sha(reportpath),finished_path=rel(fp),finished_sha256=sha(fp),planner_version=f['planner_version'],original_sources_sha256=lineage['original_sources_sha256'],original_report_sha256=lineage['original_report_sha256'],original_run_sha256=lineage['original_run_sha256'],plan_occupied_cells=len(occupied))
 rows.append(row)
groups=[]
for ship in sorted(set(r['ship'] for r in rows)):
 allr=[r for r in rows if r['ship']==ship];passed=[r for r in allr if r['passed']]
 g=dict(ship=ship,passed=len(passed),requested=len(allr),decision_cells=sorted(set(r['decision_cells'] for r in allr)))
 for k in ['loads','nominal_teu','physical_cell_demand','nominal_fill','physical_fill','cargo_mass_kg','mass_budget_fraction','mass_cap_utilisation']:
  g[k+'_range']=[min(r[k] for r in passed),max(r[k] for r in passed)]
 groups.append(g)
oldspec=ROOT/'experiments/mvp/solve47/twostep/comparison-v43-mass55-medium4-scoped-v4/specs.json';o=read(oldspec)[0]
# Historical metadata only: no additional split-unassigned instance reads.
historical=dict(seed=o['seed'],split_status='UNCONFIRMED DEVELOPMENT; not admitted as TRAIN',loads_from_report=198,nominal_teu=o['manifest_teu'],decision_capacity_teu=844,nominal_fill=o['realised_teu_fraction'],mass_kg=o['manifest_mass_kg'],generator_mass_budget_kg=o['mass_budget_kg'],mass_budget_fraction=o['realised_mass_fraction'],spec_sha256=sha(oldspec),source_instance_sha256=o['source_instance_sha256'],physical_fill='not audited under strict TRAIN scope',epoch_bundle_sha256='6fc8ac4604243fa41b8b12e75416a3528e00e8580f546a9acf9788b188facd78',full_checker_certificate_previously_inspected=True)
cap=read(ROOT/'experiments/mvp/strategy/medium-wall/capacity/summary.json');current=[{k:r[k] for k in ['map_id','loads','teu','capacity_teu','teu_fraction','mass_kg','generator_mass_fraction']} for r in cap['maps']];assert len(current)==12
extra={r['map_id']:r for r in read(ROOT/'experiments/mvp/strategy/medium-wall/capacity/inventory-extra.json')}
for r in current:
 e=extra[r['map_id']];assert r['teu']==e['physical_cell_demand'];r.update(nominal_teu=e['nominal_teu'],nominal_fill=e['nominal_teu']/r['capacity_teu'],physical_cell_demand=e['physical_cell_demand'],physical_fill=r['teu_fraction'])
out=dict(format='TRAIN-artifact-fill-audit-v1',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),method='stdlib JSON arithmetic only; no new solving, physics, checker, generation, training or numerical worker',manifest_sha256=sha(manifest),run_sha256=sha(runpath),rows=rows,groups=groups,historical_medium_metadata=historical,current_medium=current,excluded='tiny24 retention explicitly validation; no tiny corpus/val instance or plan/checker opened',passed_checks=dict(train_instances=30,matching_instance_audit_hashes=30,full_checker_reports_verified=sum(r['passed'] for r in rows),plan_cell_occupancy_checks=sum(r['passed'] for r in rows),source_lineage_hashes_verified=sum(r['passed'] for r in rows)))
(HERE/'fill-evidence.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps({'groups':groups,'checks':out['passed_checks'],'historical':historical,'current_medium_fill_range':[min(r['teu_fraction'] for r in current),max(r['teu_fraction'] for r in current)]},indent=2))
