"""Union direct polygon/liquid envelopes with native cubic table ranges."""
from __future__ import annotations

import math
from pathlib import Path
import hashlib

from area_bound import angle_intervals,pose_axes,_add,_sub,_mul,_div
from table_bound import table_integral_bounds,tensor_cubic_bounds


def union(a,b):
    return min(a[0],b[0]),max(a[1],b[1])


def expand(a,reserve=1.e-9):
    pad=reserve*max(1.,abs(a[0]),abs(a[1]))
    return math.nextafter(a[0]-pad,-math.inf),math.nextafter(a[1]+pad,math.inf)


class NativeGeometryUnion:
    def __init__(self,oracle,*,max_cells=128):
        from stowage.checker.stability_tables import cached_geometry
        self.oracle=oracle
        self.table=cached_geometry(oracle.geometry,oracle.params,oracle.rho)
        self.max_cells=max_cells
        self.calls=self.table_calls=0
        self.identity=None if self.table is None else dict(path=str(self.table.cache_path),
            sha256=hashlib.sha256(Path(self.table.cache_path).read_bytes()).hexdigest())

    def __call__(self,phi,alpha,offset,direct):
        self.calls+=1
        answer=dict(direct,native_union=True,checker_outward=False,
            arithmetic_limitation="direct fallback floating polygon kernel lacks a uniform error enclosure near vertical trim")
        if self.table is None:
            answer['native_table']='no qualified native hull table'
            return answer
        table=self.table
        # A pose outside the table's trim/heel domain necessarily uses direct
        # geometry. Intersect alpha before transforming scaled immersion.
        tlo,thi=table.bounds['trim_m']
        alo,ahi=-math.atan((thi+1.e-10)/self.oracle.L),-math.atan((tlo-1.e-10)/self.oracle.L)
        a=(max(alpha[0],math.nextafter(alo,-math.inf)),
           min(alpha[1],math.nextafter(ahi,math.inf)))
        p=(max(phi[0],0.),min(phi[1],table.bounds['heel_rad'][1]+1.e-10))
        if a[0]>a[1] or p[0]>p[1]:
            return answer
        sa,ca=angle_intervals(a)
        _,cp=angle_intervals(p,nonnegative=True)
        q=_div(_add(offset,_mul((self.oracle.L/2,)*2,_mul(sa,cp))),ca)
        trim=_mul((-self.oracle.L,)*2,_div(sa,ca))
        result=table_integral_bounds(table,expand(q,1.e-12),expand(trim,1.e-12),p,max_cells=self.max_cells)
        if result['bounds'] is not None:
            self.table_calls+=1
            for key,value in result['integrals'].items():
                answer[key]=union(answer[key],value)
            answer['native_table_range_status']=result['status']
        return answer


class NativeLiquidUnion:
    """Enclose actual native liquid CG and signed transfer corrections.

    Whole-fill consumable corrections are nonnegative by the source's max(0).
    For individual ballast correction use moment ranges, not a claimed
    nonnegative transfer. Exact tank masses use the same upright sounding as
    the checker; variable masses retain physical reference-CG boxes.
    """
    def __init__(self,oracle,mass_kg,tank_masses_kg,*,max_cells=64):
        from stowage.vessel.tanks import TankGeometry
        from stowage.checker.stability_liquid_tables import lookup
        from stowage.checker.stability_sounding import sounding
        from stowage.checker.stability_loading import CONSUMABLE_KINDS
        self.oracle=oracle
        self.mass=mass_kg
        self.max_cells=max_cells
        raw={t['id']:t for t in oracle.params.get('tanks',[])}
        self.tanks=[]
        self.calls=0
        supplied=tank_masses_kg or {}
        for t in oracle.instance.vessel.tanks:
            value=supplied.get(t.id,(0.,t.capacity_m3*t.content_density_kg_m3))
            masses=(float(value),float(value)) if isinstance(value,(int,float)) else tuple(map(float,value))
            if masses[1]<=0:
                continue
            solid=TankGeometry.from_params(raw[t.id],ship_params=oracle.params)
            table=lookup(solid)
            g=raw[t.id].get('geometry',raw[t.id])
            number=lambda x:float(x.get('value') if isinstance(x,dict) else x)
            bbox=[(number(g[f'{c}_min_m']),number(g[f'{c}_max_m'])) for c in 'xyz']
            if masses[0]==masses[1]:
                point=sounding(solid,min(masses[0]/t.content_density_kg_m3,solid.capacity_m3))
                reference=[expand((getattr(point,c+'_m'),)*2,1.e-12) for c in ('lcg','tcg','vcg')]
            else:
                reference=bbox
            self.tanks.append(dict(id=t.id,masses=masses,density=t.content_density_kg_m3,
                capacity=solid.capacity_m3*t.content_density_kg_m3,solid=solid,table=table,
                reference=reference,bbox=bbox,variable=t.kind in CONSUMABLE_KINDS,
                shape=getattr(solid,'shape',None)))

    def __call__(self,phi,alpha,tank_masses_kg,direct_lower):
        self.calls+=1
        axes=pose_axes(phi,alpha)
        callback=[(0.,0.)]*3
        negative=0.
        ca,sa=axes['cos_alpha'],axes['sin_alpha']
        # Native tank trim uses atan((ta-tf)/L)=-alpha exactly, while the
        # same independent source floating reserve covers conversion.
        trim=expand((-alpha[1],-alpha[0]),1.e-12)
        for tank in self.tanks:
            mlo,mhi=tank['masses']
            fraction=(max(0.,mlo/tank['capacity']),min(1.,mhi/tank['capacity']))
            spans=[hi-lo for lo,hi in tank['bbox']]
            direct_delta=[(-mhi*s/self.mass[0],mhi*s/self.mass[0]) for s in spans]
            direct_delta[2]=(0.,direct_delta[2][1])
            delta=direct_delta
            table=tank['table']
            # The production prismatic table explicitly refuses nonzero trim.
            query_trim=trim
            if table is not None and table.prismatic:
                query_trim=(max(trim[0],-1.e-14),min(trim[1],1.e-14))
            if table is not None and query_trim[0]<=query_trim[1]:
                box=(phi,query_trim,fraction)
                result=tensor_cubic_bounds((table.heels,table.trims,table.fills),table.moments,
                                           box,max_cells=self.max_cells,edge_tolerance=1.e-13,clip_edges=False)
                if result['bounds'] is not None:
                    native=[]
                    moments=result['bounds']
                    if mlo<=tank['capacity']<=mhi:
                        moments=[union(v,expand((float(full),)*2,1.e-12)) for v,full in zip(moments,table.moments[0,0,-1])]
                    for moment,reference in zip(moments,tank['reference']):
                        # Native centroid=moment/volume, then multiplied by
                        # actual liquid mass=rho*volume. Extra reserve encloses
                        # both source operations and their cancellation.
                        moved=_mul(tuple(moment),(tank['density'],)*2)
                        original=_mul((mlo,mhi),reference)
                        native.append(expand(_div(_sub(moved,original),self.mass),1.e-10))
                    delta=[union(a,b) for a,b in zip(direct_delta,native)]
            callback=[_add(a,b) for a,b in zip(callback,delta)]
            # Dry/pressed/non-correction regimes are permitted. Ignoring the
            # small-tank exemption enlarges the adverse correction set.
            if tank['variable'] and tank['shape']=='hull_sections':
                continue
            projected=(0.,0.)
            for d,b in zip(delta,axes['across']):
                projected=_add(projected,_mul(d,b))
            if tank['variable']:
                # Other variable shapes use whole-fill FSM or correction-volume
                # providers. Retain the whole-fill physical lower bound; the
                # unchecked native arithmetic still keeps checker_outward false.
                negative=min(negative,float(direct_lower))
            else:
                negative+=min(0.,projected[0])
        return dict(lower_m=min(float(direct_lower),math.nextafter(negative,-math.inf)),
                    callback_delta_x_m=callback[0],callback_delta_z_m=callback[2],
                    callback_enclosure_outward=True,callback_physical_enclosed=False,
                    checker_outward=False,
                    scope='union native cubic moment ranges and direct physical liquid bounds')
