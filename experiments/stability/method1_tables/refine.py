"""Refine shallow double-bottom grids, reusing identical existing nodal planes.

Only new angular nodes call the same exact polygon-polynomial sampler. Numerical
arrays/provenance are written by the provider's ordinary atomic writer; previous
certificates then fail their SHA binding until the complete holdouts are rerun.
"""
import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from stowage.checker import stability_liquid_tables as t
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry


def run(name, output, ids=('DB-M-P','DB-M-S')):
    params=load_ship_params(name)
    result={'vessel':name,'tanks':[],'complete':False}
    started=perf_counter()
    for raw in params['tanks']:
        if raw['id'] not in ids:continue
        solid=TankGeometry.from_params(raw,ship_params=params)
        old=t.lookup(solid,require_qualified=False)
        if old is None:raise ValueError('coarse physical grid required first')
        heels=np.unique(np.r_[old.heels,np.radians([-12.5,-7.5,-2.5,2.5,7.5,12.5])])
        trims=np.unique(np.r_[old.trims,[-.03,-.015,-.01,-.005,.005,.01,.015,.03]])
        moments=np.empty((len(heels),len(trims),len(old.fills),3))
        existing_h={float(v):i for i,v in enumerate(old.heels)}
        existing_t={float(v):i for i,v in enumerate(old.trims)}
        built=reused=0;tank_start=perf_counter()
        for i,h in enumerate(heels):
            for j,trim in enumerate(trims):
                if float(h) in existing_h and float(trim) in existing_t:
                    moments[i,j]=old.moments[existing_h[float(h)],existing_t[float(trim)]];reused+=1
                else:
                    moments[i,j]=t._angle_moments(solid,h,trim,old.fills);built+=1
        new=t.LiquidTable(old.fills,heels,trims,moments,old.capacity,old.empty_centroid,old.prismatic)
        t._validate(new)
        t._write(t._raw_path(solid),new,solid);t._write(t._path(solid),new,solid)
        t._lookup.cache_clear()
        result['tanks'].append(dict(id=raw['id'],built_planes=built,reused_planes=reused,
            shape=[len(old.fills),len(heels),len(trims)],seconds=perf_counter()-tank_start,
            path=str(t._path(solid))))
        output.write_text(json.dumps(result,indent=2)+'\n')
    result.update(complete=True,seconds=perf_counter()-started)
    output.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--vessel',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.vessel,args.output)
