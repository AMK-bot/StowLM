"""Audit probe: no-cargo ballast sets, trim/GM vs limits (departure consumables 98%)."""
import json, math
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.tanks import TankGeometry
out={}
for n in (500,1000,1700):
    p=load_ship_params(f'feeder_{n}');h=p['hull'];L,B,T=h['lpp_m'],h['beam_m'],h['design_draught_m']
    g=HullGeometry.from_params(p,nx=96,nz=128);lim=p['operating_limits']
    tanks=[(t,TankGeometry.from_params(t)) for t in p['tanks']]
    def cond(sel):
        m=h['lightship_mass_kg'];mx=m*h['lightship_lcg_m'];mz=m*h['lightship_vcg_m'];fsm=0.
        for t,s in tanks:
            f=.98 if t['kind']!='ballast' else (1. if any(t['id'].startswith(k) for k in sel) else 0.)
            if f<=0:continue
            v=s.capacity_m3*f;sp=s.sounding(v);w=v*t['content_density_kg_m3'];m+=w;mx+=w*sp.lcg_m;mz+=w*sp.vcg_m;fsm+=sp.fsm_m4*t['content_density_kg_m3']
        fc=g.equilibrium(m,mx/m,0.,mz/m);sub=fc.submerged;gm=sub.vcb_m+sub.bm_t_m-mz/m-fsm/m
        return dict(ballast_t=round((m-h['lightship_mass_kg'])/1e3,0),gm=round(gm,2),trim=round(fc.t_aft_m-fc.t_fwd_m,2),t_fwd=round(fc.t_fwd_m,2),t_aft=round(fc.t_aft_m,2),
                    trim_ok=abs(fc.t_aft_m-fc.t_fwd_m)<=lim['max_trim_m'],fwd_ok=fc.t_fwd_m>=lim['min_draught_fwd_m'],aft_prop_immersion=round(fc.t_aft_m/T,2))
    sets={'all':['DB','WING','HEEL','FPT','APT'],'no_APT':['DB','WING','HEEL','FPT'],'fwd_set':['DB-M','DB-F','WING-F','HEEL','FPT'],'DB+FPT':['DB','FPT']}
    out[n]={k:cond(v) for k,v in sets.items()};out[n]['limits']=dict(max_trim=lim['max_trim_m'],min_fwd=lim['min_draught_fwd_m'])
    print(n,json.dumps(out[n]))
json.dump(out,open('experiments/physics-review/convergence/audit/probe_ballast.json','w'),indent=1)
