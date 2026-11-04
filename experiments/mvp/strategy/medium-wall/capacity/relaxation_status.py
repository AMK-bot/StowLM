"""Separate LP optimization status from feasibility proved by narrower witnesses."""
from pathlib import Path
import json,hashlib
HERE=Path(__file__).resolve().parent
p=json.loads((HERE/'packing-summary.json').read_text());out=[]
for row in p['maps']:
 modes={r['mode']:r for r in row['maps']};mass=modes['mass'];sources=[r for r in row['maps'] if r['mode'] in ('mass','strict','shared') and r['status']==0];best=min(sources,key=lambda r:r['minimax_stack_utilisation']) if sources else None
 out.append({'map_id':row['map_id'],'unrestricted_static_mass_optimisation_status':mass['status'],'unrestricted_static_mass_exact_optimum':mass.get('minimax_stack_utilisation'),'unrestricted_static_mass_relaxation_feasible':best is not None,'unrestricted_static_mass_primal_upper_bound':best.get('minimax_stack_utilisation') if best else None,'feasibility_witness_mode':best['mode'] if best else None,'argument':'Strict/shared POD permissions only remove box-slot columns from the unrestricted static-domain mass model; a feasible allocation in either remains feasible unrestricted. This preserves feasibility only, not equality of optima.','geometry_mass_optimisation_status':modes['geometry']['status'],'shared_minimax_stack_utilisation':modes['shared'].get('minimax_stack_utilisation')})
value={'maps':out,'complete':len(out)==12,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'all_relaxed_static_mass_feasible':all(r['unrestricted_static_mass_relaxation_feasible'] for r in out)};(HERE/'relaxation-status.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
