"""Angular-integration convergence for the near-boundary arrival sensitivity."""
import json,math,time
from pathlib import Path
import numpy as np
import feeder_envelope as f
from historical_fixture import read_historical_fixture,require_fixture_hash


def main():
 start=time.perf_counter()
 i,identity=read_historical_fixture(f.ROOT,f.Instance)
 extra=[identity['source_path'],str(Path(__file__).resolve().relative_to(f.ROOT)),
        str((Path(__file__).resolve().parent/'historical_fixture.py').relative_to(f.ROOT))]
 before=f.source_hashes('feeder_500',extra_paths=extra)
 require_fixture_hash(before,identity)
 p=f.load_ship_params('feeder_500')
 l=f.derive_arrival_liquid_state(i.onboard,i.voyage,vessel=i.vessel)
 ship=i.onboard.model_copy(update={'tank_masses_kg':dict(l.tank_masses_kg)})
 g=f.geometry_variant(f.geometry_variant(f.StabilityGeometry(p),p,'sealed_cover_0p6'),p,'forecastle_2m')
 original=f.build_stability_curve;got=[]
 def capture(*args,**kwargs):
  result=original(*args,**kwargs);got.append(result);return result
 f.build_stability_curve=capture
 try:
  row,_,_=f.evaluate(i,ship,p,g)
 finally:
  f.build_stability_curve=original
 curve=got[-1];end=math.radians(row['total_area_end_deg'])
 rows=[]
 for step in (2.,1.,.5):
  x=np.unique(np.r_[curve.angles[curve.angles<=end],np.arange(0,end,math.radians(step)),end])
  y=np.array([curve.at_angle(float(a))[0] for a in x])
  area=float(np.trapezoid(y,x));req=row['required_b_areas_mrad']['area_0_flood_mrad']
  rows.append(dict(step_deg=step,points=len(x),area_mrad=area,required_mrad=req,ratio=area/req,deficit_mrad=req-area))
  print(rows[-1],flush=True)
 after=f.source_hashes('feeder_500',extra_paths=extra)
 require_fixture_hash(after,identity)
 if before!=after:
  raise RuntimeError('Source changed during historical convergence solve')
 r=dict(case='historical500arrival sealedcover0.6+forecastle2m',rows=rows,**identity,
  source_hashes_before=before,source_hashes_after=after,source_stable=True,
  script_sha256=before[str(Path(__file__).resolve().relative_to(f.ROOT))],elapsed_s=time.perf_counter()-start)
 (f.OUT/'historical-arrival-area-convergence.json').write_text(json.dumps(r,indent=2)+'\n')
 print('elapsed_s',r['elapsed_s'])


if __name__=='__main__':main()
