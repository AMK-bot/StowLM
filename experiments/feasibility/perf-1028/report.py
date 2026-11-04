"""Summarize persisted measurements; never substitutes missing C2-on samples."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent

def summary(reference, optimized):
    before = json.loads(reference.read_text())
    after = json.loads(optimized.read_text())
    rows=[]
    for key, old in before['blocks'].items():
        new=after['blocks'].get(key)
        if not new or not old.get('states') or not new.get('states'):
            continue
        assert len(old['states']) == len(new['states'])
        for a,b in zip(old['states'],new['states']):
            assert a['depth']==b['depth'] and a['mask_sha256']==b['mask_sha256']
        times=[np.array([s['index_ms'] for s in block['states']]) for block in (old,new)]
        # These are base PLACE steps, not a prediction of additional restows.
        steps=old['boxes']
        stats=[(float(np.median(t)),float(np.percentile(t,95))) for t in times]
        rows.append(dict(key=key,states=len(times[0]),base_place_steps=steps,
                         reference_ms=stats[0],optimized_ms=stats[1],
                         reference_base_plan_s=[steps*x/1000 for x in stats[0]],
                         optimized_base_plan_s=[steps*x/1000 for x in stats[1]]))
    return rows

def main():
    p=argparse.ArgumentParser()
    p.add_argument('reference',type=Path)
    p.add_argument('optimized',type=Path)
    p.add_argument('--output',type=Path,default=HERE/'summary.json')
    args=p.parse_args()
    result=summary(args.reference,args.optimized)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    for row in result:
        before='/'.join(f'{x:.3f}' for x in row['reference_ms'])
        after='/'.join(f'{x:.3f}' for x in row['optimized_ms'])
        print(f"{row['key']}: {before} -> {after} ms; {row['states']} exact masks")

if __name__=='__main__':
    main()
