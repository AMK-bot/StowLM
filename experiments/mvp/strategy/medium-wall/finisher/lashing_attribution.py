"""Attribute existing checker securing violations by actual allowed input IDs."""
from collections import Counter
import run

out=[];cohort=run.read(run.ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json')
for task in run.read(run.HERE/'tasks.json'):
    job=next(j for j in cohort['maps'] if j['map_id']==task['map_id'])
    instance_path=run.ROOT/job['input'];instance=run.read(instance_path)
    load_ids=set(instance['load_list']);instance_ids={b['id'] for b in instance['containers']}
    fixed_ids={f'fixed-load-{i}-box-{j}' for i,load in enumerate(instance['vessel']['fixed_loads'])
        for j,_ in enumerate(load.get('cargo_boxes') or [])}
    def category(cid):
        if cid in load_ids:return 'loaded_this_call'
        if cid in instance_ids:return 'other_instance_cargo'
        if cid in fixed_ids:return 'fixed_background'
        return 'unknown'
    report_path=run.ROOT/'experiments/mvp/strategy/blockrule/runs/initial-medium-train/maps'/task['map_id']/'reports/default-raw.json'
    report=run.read(report_path);parts={};total=Counter();unique=set();violations=0
    for name,part in report['parts'].items():
        if not name.startswith('condition:') or not name.endswith(':lashing'):continue
        securing=[v for v in part['violations'] if v['rule_id']=='iacs.member_irs.container_securing']
        ids={cid for v in securing for cid in v['containers']};refs=Counter(category(cid) for v in securing for cid in v['containers'])
        parts[name]=dict(violation_count=len(securing),container_reference_counts=dict(refs),
            unique_container_counts=dict(Counter(category(cid) for cid in ids)),unique_container_ids=sorted(ids))
        total.update(refs);unique.update(ids);violations+=len(securing)
    out.append(dict(map_id=task['map_id'],input_sha256=run.sha(instance_path),report_sha256=run.sha(report_path),
        violation_count=violations,container_reference_counts=dict(total),
        unique_container_counts=dict(Counter(category(cid) for cid in unique)),parts=parts,
        meaning='Attribution of these existing checker failures at retained ballast, not a universal securing bound or infeasibility certificate.'))
run.save(run.HERE/'lashing-attribution.json',out)
for row in out:print(row['map_id'],row['violation_count'],row['container_reference_counts'],row['unique_container_counts'])
