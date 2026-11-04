"""Exact P/S reflection of independent bay/row groups; preserves local CP structure."""
from pathlib import Path
import json,itertools,hashlib,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def parts(s):
 b,r,t=int(s[:3]),int(s[3:5]),int(s[5:]);return [s] if b%2 else [f'{k:03}{r:02}{t:02}' for k in (b-1,b+1)]
def mirror(s):
 b,r,t=int(s[:3]),int(s[3:5]),int(s[5:]);assert r!=0;return f'{b:03}{r+1 if r%2 else r-1:02}{t:02}'
def balance(path):
 r=json.loads(path.read_text());assert r.get('witness');d=json.loads((ROOT/r['input']).read_text());assert sha(ROOT/r['input'])==r['input_sha256'];cells={c['id']:c for c in d['vessel']['cells']};stacks={s['id']:s for s in d['vessel']['stacks']};boxes={b['id']:b for b in d['containers']};groups={}
 def moment(cid,slot):return boxes[cid]['gross_mass_kg']*sum(cells[c]['tcg_m'] for c in parts(slot))/len(parts(slot))
 def key(slot):
  b,row=int(slot[:3]),int(slot[3:5]);return ((b if b%2==0 else b+1 if b%4==1 else b-1),min(row,row+1 if row%2 else row-1))
 for cid,slot in r['witness'].items():
  ps=parts(slot);ms=mirror(slot);mp=parts(ms)
  assert all(c in cells for c in mp)
  for c,mc in zip(ps,mp):
   a,z=cells[c],cells[mc];assert abs(a['tcg_m']+z['tcg_m'])<1e-8;assert a['lcg_m']==z['lcg_m'];assert a['vcg_base_m']==z['vcg_base_m'];assert all(a[k]==z[k] for k in ('accepts_20','accepts_40','accepts_45','reefer_plug'))
   sa,sz=stacks[a['stack_id']],stacks[z['stack_id']];assert all(sa[k]==sz[k] for k in ('max_mass_kg','max_mass_20_kg','max_mass_40_kg','max_height_m','dg_classes_allowed'))
  groups.setdefault(key(slot),[]).append(cid)
 keys=sorted(groups);assert len(keys)<=16;contributions=[sum(moment(cid,r['witness'][cid]) for cid in groups[k]) for k in keys]
 bits=min(itertools.product((0,1),repeat=len(keys)),key=lambda bits:(abs(sum((-1 if bit else 1)*m for bit,m in zip(bits,contributions))),sum(bits),bits));chosen={cid:(mirror(s) if bits[keys.index(key(s))] else s) for cid,s in r['witness'].items()};cargo=sum(boxes[c]['gross_mass_kg'] for c in chosen);before=sum(moment(c,s) for c,s in r['witness'].items());after=sum(moment(c,s) for c,s in chosen.items());assert len({c for s in chosen.values() for c in parts(s)})==sum(len(parts(s)) for s in chosen.values())
 out={'map_id':r['map_id'],'input':r['input'],'input_sha256':r['input_sha256'],'witness':chosen,'source_candidate':str(path.relative_to(ROOT)),'source_candidate_sha256':sha(path),'script_sha256':sha(__file__),'proposal':'whole bay-pair/row-pair P/S reflections selected by exhaustive minimum cargo transverse moment','groups':len(keys),'reflected_groups':[list(k) for k,b in zip(keys,bits) if b],'cargo_tcg_before_m':before/cargo,'cargo_tcg_after_m':after/cargo,'cargo_moment_before_kgm':before,'cargo_moment_after_kgm':after,'local_symmetry_audit':'pass; cell fit/height/LCG and corresponding stack caps match; complete row-group reflection preserves local stack mass/order/support','physics_or_native_certificate':False};p=path.with_name('construct-cp-balanced.json');p.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');return out
if __name__=='__main__':
 for arg in sys.argv[1:]:
  r=balance(Path(arg));print(r['map_id'],r['cargo_tcg_before_m'],r['cargo_tcg_after_m'])
