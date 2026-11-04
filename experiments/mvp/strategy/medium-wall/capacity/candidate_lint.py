"""Describe omitted structural relations; no checker replacement or rule claim."""
from pathlib import Path
import json,sys,collections,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
def parts(s):
 b,row,t=int(s[:3]),int(s[3:5]),int(s[5:]);return [s] if b%2 else [f'{k:03}{row:02}{t:02}' for k in (b-1,b+1)]
def lint(path):
 r=json.loads(path.read_text())
 if not r.get('witness'):return
 d=json.loads((ROOT/r['input']).read_text());boxes={b['id']:b for b in d['containers']};cells={c['id']:c for c in d['vessel']['cells']};stacks={s['id']:s for s in d['vessel']['stacks']};by_stack=collections.defaultdict(list);kg=[];short=[];height=[];ym=zm=xm=0.;total=0.
 for cid,slot in r['witness'].items():
  b=boxes[cid];ps=parts(slot);total+=b['gross_mass_kg'];ym+=b['gross_mass_kg']*sum(cells[c]['tcg_m'] for c in ps)/len(ps);xm+=b['gross_mass_kg']*sum(cells[c]['lcg_m'] for c in ps)/len(ps)
  for c in ps:by_stack[cells[c]['stack_id']].append((int(c[-2:]),cid))
 for sid,rows in by_stack.items():
  rows.sort()
  for (tier,a),(tier2,b) in zip(rows,rows[1:]):
   if boxes[b]['gross_mass_kg']>boxes[a]['gross_mass_kg']+1e-7:kg.append({'stack':sid,'below':a,'above':b,'below_kg':boxes[a]['gross_mass_kg'],'above_kg':boxes[b]['gross_mass_kg']})
   if boxes[b]['length_ft']==20 and boxes[a]['length_ft']>20:short.append({'stack':sid,'below':a,'above':b})
 for cid,slot in r['witness'].items():
  ps=parts(slot)
  if len(ps)!=2:continue
  bases=[]
  for c in ps:
   sid=cells[c]['stack_id'];t=int(c[-2:]);top=stacks[sid]['base_z_m']+sum(boxes[b]['height_m']+(boxes[b]['oog']['over_height_m'] if boxes[b]['oog'] else 0) for tier,b in by_stack[sid] if tier<t);bases.append(top)
  if abs(bases[0]-bases[1])>1e-6:height.append({'box':cid,'slot':slot,'support_roof_m':bases,'difference_m':abs(bases[0]-bases[1])})
 out={'map_id':r['map_id'],'candidate_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'diagnostic_only':True,'actual_mass_inversions':kg,'short_above_long_adjacencies':short,'long_support_height_mismatches':height,'cargo_tcg_m':ym/total,'cargo_lcg_m':xm/total,'note':'These are measured geometric/weight relations omitted by CP. Only native engine/checker determine which are active violations under pinned rules.'};p=path.with_name('construct-cp-lint.json');p.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(r['map_id'],len(kg),len(short),len(height),ym/total)
if __name__=='__main__':
 for arg in sys.argv[1:]:lint(Path(arg))
