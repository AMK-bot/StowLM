"""Audit probe: whole-ship C3 export background (D29 basis) at 0.97 design displacement, no ballast. GM, trim, roll period."""
import json, math
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_conditions import _c3_placements
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.tanks import TankGeometry
out={}
for n in (500,1000,1700):
    p=load_ship_params(f'feeder_{n}');h=p['hull'];L,B,T=h['lpp_m'],h['beam_m'],h['design_draught_m']
    g=HullGeometry.from_params(p,nx=96,nz=128);disp=g.submerged(T,T,0.).displacement_kg
    v=load_vessel(f'feeder_{n}',with_hull=False)
    tanks=[(t,TankGeometry.from_params(t)) for t in p['tanks'] if not t.get('adjustable')]
    cons=sum(.97*t['capacity_m3']*t['content_density_kg_m3'] for t,_ in tanks)
    budget=.97*disp-h['lightship_mass_kg']-cons
    placed=_c3_placements(v,41,budget,lpp_m=L)
    cells={c['id']:c for c in p['cells']};stk={s['id']:s for s in p['stacks']}
    m=h['lightship_mass_kg'];mx=m*h['lightship_lcg_m'];mz=m*h['lightship_vcg_m'];fsm=0.
    for t,s in tanks:
        vol=.97*s.capacity_m3;sp=s.sounding(vol);w=vol*t['content_density_kg_m3'];m+=w;mx+=w*sp.lcg_m;mz+=w*sp.vcg_m;fsm+=sp.fsm_m4*t['content_density_kg_m3']
    cargo=sum(x for _,x in placed);deck=sum(x for c,x in placed if stk[cells[c]['stack_id']]['on_deck'])
    for cid,x in placed:
        c=cells[cid];m+=x;mx+=x*c['lcg_m'];mz+=x*(c['vcg_base_m']+2.591/2)
    fc=g.equilibrium(m,mx/m,0.,mz/m);sub=fc.submerged;gm=sub.vcb_m+sub.bm_t_m-mz/m-fsm/m;d=(fc.t_aft_m+fc.t_fwd_m)/2
    c=.373+.023*B/d-.043*L/100;Tr=2*c*B/math.sqrt(gm) if gm>0 else None
    out[n]=dict(boxes=len(placed),cells=len(p['cells']),cargo_t=cargo/1e3,deck_share_mass=deck/cargo,KG=mz/m,GM=gm,trim=fc.t_aft_m-fc.t_fwd_m,mean_T=d,roll_T_s=Tr)
    print(n,json.dumps(out[n]),flush=True)
json.dump(out,open('experiments/physics-review/convergence/audit/probe_c3.json','w'),indent=1)
