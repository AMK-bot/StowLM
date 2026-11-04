import json,glob,sys
for f in sorted(glob.glob(sys.argv[1]+'/*.json')):
    r=json.load(open(f))
    if 'instance_id' not in r or r['passed']: continue
    print(r['instance_id'][-30:],r['status'],round(r['wall_s']),round(r['propose_s']),round(r['finish_s']),'ceil',r['method_metadata'].get('gm_ceiling'))
    for t in r['method_metadata']['trials']:
        print('  ',t.get('outcome'),t.get('db_scale'),'ref',round(t.get('gm_reference',0),2),'gm',round(t.get('gm_actual',0),2),round(t.get('exact_ratio',0),3),t.get('lifts'),'fb' if t.get('fallback') else '','res' if t.get('resumed') else '',t.get('dive_s'),{k.split('.')[-1][:12]:round(v,3) for k,v in (t.get('worst') or {}).items()})
