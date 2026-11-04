import json,glob,sys
for f in sorted(glob.glob(sys.argv[1]+'/*.json')):
    r=json.load(open(f))
    if 'instance_id' not in r: continue
    print(r['instance_id'][-34:],r['n_boxes'],r['status'],r['lifts'],r.get('parts'),round(r['wall_s']),r.get('failure_reason'))
    for t in r['method_metadata'].get('trials',[]):
        print('   ',{k:(round(v,3) if isinstance(v,float) else v) for k,v in t.items() if k in('attempt','gm_reference','limit','gm_actual','exact_ratio','chains_over','lifts','outcome','rules','dive_s','illegal_columns','worst','ratio_dive','repair_excess','repair_rounds','exchange_excess','exchange')})
