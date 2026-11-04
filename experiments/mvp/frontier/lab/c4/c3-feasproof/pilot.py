from pathlib import Path
import json,time,os,importlib.util,math
from stowage.io import load_instance
from stowage.model import MovePlan
import audit,joint_model
from area_bound import AreaOracle,build_area_envelope,build_knot_area_envelope
from native_union import NativeGeometryUnion,NativeLiquidUnion
HERE=Path(__file__).resolve().parent
r=next(c for c in audit.positive_controls() if c['instance_id']=='random.medium.185100000.0.2')
i=load_instance(audit.ROOT/r['input']);p=MovePlan.model_validate(r['plan'])
model=joint_model.build_joint_model(i)
projection=joint_model.project_plan(model,i,p)
obs=next(c for c in json.loads((HERE/'controls.json').read_text())['controls'] if c['instance_id']==i.id)
phase=os.environ.get('PHASE','arrival')
context,ship=audit.phase_states(i,p)[phase]
a=obs['phases'][phase];v=projection['phases'][phase]
oracle=AreaOracle(context)
mass=(a['metrics']['displacement_kg']-1e-6,a['metrics']['displacement_kg']+1e-6)
kg=(a['kg_physical_m']-1e-10,a['kg_physical_m']+1e-10)
lcg=(v['lcg_m']-1e-8,v['lcg_m']+1e-8)
tanks=dict(ship.tank_masses_kg)
hull=NativeGeometryUnion(oracle,max_cells=16)
liquid=NativeLiquidUnion(oracle,mass,tanks,max_cells=16)
t=time.monotonic()
print('pilot pid',os.getpid(),flush=True)
if os.environ.get('FULL'):
    builder=build_knot_area_envelope if os.environ.get("KNOT") else build_area_envelope
    extra={"heel_intervals":[(0.,math.nextafter(math.pi/2,math.inf))]} if os.environ.get("COARSE") else {}
    result=builder(context,mass_kg=mass,kg_m=kg,lcg_m=lcg,tank_masses_kg=tanks,oracle=oracle,
      integral_provider=hull,correction_lower_provider=liquid,max_pose_boxes=int(os.environ.get('BOXES','16')),
      max_depth=12,offset_contraction_steps=int(os.environ.get('CONTRACT','0')),**extra)
    (HERE/'pilot_area.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='slab_bounds'}),flush=True)
else:
    phi=(math.radians(30),math.radians(30))
    alpha=(-.002,-.002)
    result=oracle.classify_pose(phi,alpha,(6.3,6.3),mass_kg=mass,kg_m=kg,lcg_m=lcg,tank_masses_kg=tanks,integral_provider=hull,correction_lower_provider=liquid)
    print(json.dumps(result),flush=True)
print('elapsed',time.monotonic()-t,'pid',os.getpid(),'observed_area',a['measured_positive_area_mrad'],flush=True)
