"""Audit saved TRAIN artifacts without re-solving."""
from pathlib import Path
import json,hashlib,os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
import numpy as np
from scipy.sparse import csr_matrix
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4];RUNTIME=HERE.parent/'search/runtime'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def parts(slot):
 b,r,t=int(slot[:3]),int(slot[3:5]),int(slot[5:]);return [slot] if b%2 else [f'{k:03}{r:02}{t:02}' for k in (b-1,b+1)]
m=json.loads((ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text());assert m['split']=='train';binding=json.loads((HERE/'binding.json').read_text());assert binding['manifest_sha256']==sha(ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json');assert binding['runtime_snapshot_sha256']==sha(RUNTIME/'snapshot.json');assert binding['script_sha256']==sha(HERE/'analyze.py');snapshot=json.loads((RUNTIME/'snapshot.json').read_text());assert all(sha(RUNTIME/p)==h for p,h in snapshot['files'].items());out={'input_maps':0,'integer_geometry_witnesses':0,'fractional_lp_witnesses':0,'strict_mask_certificates':0,'cp_witnesses':0,'cp_pending':0,'runtime_snapshot_files':len(snapshot['files']),'runtime_snapshot_identity':snapshot['snapshot_sha256'],'max_lp_ineq_violation':0.,'max_lp_assignment_residual':0.,'files':{}}
for j in m['maps']:
 assert j['split']=='train' and '/maps/train/medium/' in j['input'];assert sha(ROOT/j['input'])==j['sha256'];assert sha(ROOT/j['audit'])==j['audit_sha256'];out['input_maps']+=1;d=json.loads((ROOT/j['input']).read_text());cell={c['id']:c for c in d['vessel']['cells']};box={b['id']:b for b in d['containers']};folder=HERE/'maps'/j['map_id'];g=json.loads((folder/'geometry.json').read_text());w=g['witness'];assert set(w)==set(d['load_list']);occupied=[]
 for cid,s in w.items():
  ps=parts(s);b=box[cid];assert (b['length_ft']==20)==(len(ps)==1);assert all(cell[c][f"accepts_{b['length_ft']}"] for c in ps);assert not b['reefer'] or any(cell[c]['reefer_plug'] for c in ps);occupied+=ps
 assert len(occupied)==len(set(occupied));oc=set(occupied)
 for s in d['vessel']['stacks']:
  missing=False
  for c in s['cell_ids']:
   if c not in oc:missing=True
   else:assert not missing
 out['integer_geometry_witnesses']+=1
 for f in folder.glob('*-packing.json'):
  r=json.loads(f.read_text());out['files'][str(f.relative_to(HERE))]=sha(f)
  if r['status']==0:
   npz=folder/(r['mode']+'-packing-witness.npz');a=np.load(npz);A=csr_matrix((a['A_ub_data'],a['A_ub_indices'],a['A_ub_indptr']),shape=tuple(a['A_ub_shape']));E=csr_matrix((a['A_eq_data'],a['A_eq_indices'],a['A_eq_indptr']),shape=tuple(a['A_eq_shape']));x=a['x'];ineq=max(0.,float(np.max(A@x-a['b_ub'])));eq=float(np.max(np.abs(E@x-a['b_eq'])));assert ineq<1e-5 and eq<1e-7;assert min(x)>=-1e-7 and max(x[:-1])<=1+1e-7;assert abs(x[-1]-r['minimax_stack_utilisation'])<1e-7;out['fractional_lp_witnesses']+=1;out['max_lp_ineq_violation']=max(out['max_lp_ineq_violation'],ineq);out['max_lp_assignment_residual']=max(out['max_lp_assignment_residual'],eq)
  elif r.get('certificate'):
   assert r['mode']=='strict';assert any(n>0 for n in r['certificate']['deficit_cells'].values());out['strict_mask_certificates']+=1
 cp=folder/'construct-cp.json'
 if cp.exists():
  r=json.loads(cp.read_text());assert r['input_sha256']==j['sha256'];assert r['script_sha256']==sha(HERE/'construct_cp.py')
  if r.get('witness'):assert len(r['witness'])==len(d['load_list']);assert r['independent_witness_audit']=='pass';out['cp_witnesses']+=1
 elif j['seed'] in (185100000,185100010):out['cp_pending']+=1
out['static_all12_verified']=out['input_maps']==out['integer_geometry_witnesses']==12;out['packing_complete']=json.loads((HERE/'packing-summary.json').read_text()).get('completed',False);out['status']='pass';(HERE/'verification.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='files'},indent=2))
