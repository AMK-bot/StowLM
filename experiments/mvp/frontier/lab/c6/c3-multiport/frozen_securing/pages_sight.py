"""Optional native-range roof restriction; separate from frozen cohort Model.

At supplied waterplanes every retained cargo envelope is below the native
finite-range blind cone. A changed equilibrium and fixed obstructions still
require the full unchanged visibility and voyage checker.
"""
import math
from collections import defaultdict

from frozen_securing.pages_balanced import Model as BalancedModel, Page, Pattern


def roof_cap(*, x_min, x_max, y_min, y_max, eye, normal, along, across,
             aft_draft_m, bow_range_m, roof_margin_m=0.):
    """Highest absolute roof below all four native range-cone corner limits.

    Coordinates and orthonormal waterplane vectors follow visibility.py.
    Enforcing the convex cone inequality at every vertex is a sufficient cargo
    restriction in this supplied waterplane; it intentionally covers all azimuths.
    """
    dot = lambda a, b: sum(x*y for x,y in zip(a,b))
    height = dot(normal,eye)-normal[2]*aft_draft_m
    if height <= 0 or bow_range_m <= 0 or normal[2] <= 0:
        raise ValueError('invalid sight eye/waterplane/range')
    # Derivative of R*n_z*z minus H*radial(z) is positive under this bound.
    if bow_range_m*normal[2] <= height*math.hypot(along[2],across[2]):
        raise ValueError('sight waterplane too steep for monotone roof cap')
    cap = math.inf
    for x in (x_min,x_max):
        for y in (y_min,y_max):
            def excess(z):
                p=(x-eye[0],y-eye[1],z-eye[2])
                u,v=dot(along,p),dot(across,p)
                h=dot(normal,p)+height
                return height*math.hypot(u,v)+bow_range_m*(h-height)
            lo,hi=-1000.,1000.
            if excess(lo)>0 or excess(hi)<0:
                raise ValueError('sight roof root lies outside search bracket')
            # Retain the feasible endpoint, never the violating one.
            for _ in range(44):
                mid=(lo+hi)/2
                if excess(mid)<=0:
                    lo=mid
                else:
                    hi=mid
            cap=min(cap,lo)
    return cap-max(0.,roof_margin_m)


class Model(BalancedModel):
    def __init__(self, problem, *, sight_draft_m=None, sight_conditions=(),
                 sight_reference_ship=None, sight_roof_margin_m=.15, **kwargs):
        self.sight_roof_margin_m=float(sight_roof_margin_m)
        self.sight_frames=[]
        self.sight_cache={}
        super().__init__(problem,**kwargs)
        conditions=list(sight_conditions)
        if sight_draft_m is not None:
            conditions.append(dict(draught_aft_m=float(sight_draft_m),
                                   draught_fwd_m=float(sight_draft_m),list_rad=0.))
        if sight_reference_ship is not None:
            from stowage.checker.visibility import _loading_condition
            c=_loading_condition(problem.instance,sight_reference_ship)
            conditions.append(dict(draught_aft_m=c.t_aft_m,draught_fwd_m=c.t_fwd_m,
                                   list_rad=c.heel_rad))
        if not conditions:
            return
        from stowage.checker.visibility import _geometry, CONNING_EYE_SETBACK_M
        from pathlib import PurePosixPath
        vessel=problem.instance.vessel
        bridge,hull=vessel.bridge,vessel.hull
        if bridge is None or hull is None:
            raise ValueError('bridge/hull required for sight restriction')
        name=PurePosixPath(hull.hull_offsets_ref.split('#')[0]).stem
        geometry,_=_geometry(name)
        eye=(bridge.x_m-CONNING_EYE_SETBACK_M,0.,bridge.eye_z_m)
        for c in conditions:
            aft=float(c['draught_aft_m']);fwd=float(c['draught_fwd_m'])
            heel=float(c.get('list_rad',0.))
            n=tuple(float(v) for v in geometry.waterplane_normal(aft,fwd,heel))
            raw=(1.-n[0]*n[0],-n[1]*n[0],-n[2]*n[0])
            norm=math.sqrt(sum(v*v for v in raw))
            along=tuple(v/norm for v in raw)
            across=(n[1]*along[2]-n[2]*along[1],n[2]*along[0]-n[0]*along[2],n[0]*along[1]-n[1]*along[0])
            bow_z=aft+(fwd-aft)*bridge.bow_x_m/hull.lpp_m
            bow=sum(along[i]*(v-eye[i]) for i,v in enumerate((bridge.bow_x_m,0.,bow_z)))
            self.sight_frames.append(dict(eye=eye,normal=n,along=along,across=across,
                                          aft_draft_m=aft,bow_range_m=bow+min(2*hull.loa_m,500.)))
        # Necessary-for-this-restriction pricing mask: even the shortest bodies
        # below a candidate cannot put its roof above the page's permitted line.
        # The exact whole-column legal() test handles actual previous HC/OOG.
        out=self.static.copy()
        minimum=min(float(self.p.boxes[self.T.box_ids[b]].height_m) for b in self.boxes)
        for page in self.pages:
            if not page.deck:
                continue
            base=max(float(self.T.stack_base_z[j]) for j in page.stacks)
            for tier,slot in enumerate(page.slots):
                for b in self.boxes:
                    if out[b,slot] and base+tier*minimum+float(self.T.height[b])>self.sight_limit(page,tier,b)+1e-8:
                        out[b,slot]=False
        self.static=out
        refined=defaultdict(list)
        for key,indices in self.types.items():
            for j in indices:
                sig=self.static[self.boxes,:][:,self.pages[j].slots].tobytes()
                refined[key+(sig,)].append(j)
        self.types=refined
        self.type_of={j:key for key,indices in refined.items() for j in indices}
        self.pricing_cache.clear()

    def sight_limit(self,page,tier,box):
        if not self.sight_frames or not page.deck:
            return math.inf
        b=self.p.boxes[self.T.box_ids[box]]
        x,y=self._slot_xy(page,tier)
        o=b.oog
        fore=o.over_length_fore_m if o else 0.;aft=o.over_length_aft_m if o else 0.
        port=o.over_width_port_m if o else 0.;star=o.over_width_starboard_m if o else 0.
        length={20:6.058,40:12.192,45:13.716}[b.length_ft]
        bounds=(x-length/2-aft,x+length/2+fore,y-1.219-star,y+1.219+port)
        if bounds not in self.sight_cache:
            self.sight_cache[bounds]=min(roof_cap(x_min=bounds[0],x_max=bounds[1],
                y_min=bounds[2],y_max=bounds[3],roof_margin_m=self.sight_roof_margin_m,
                **frame) for frame in self.sight_frames)
        return self.sight_cache[bounds]

    def legal(self,page,boxes,*,any_port=False):
        if not super().legal(page,boxes,any_port=any_port):
            return False
        if not page.deck or not self.sight_frames:
            return True
        roof=max(float(self.T.stack_base_z[j]) for j in page.stacks)
        for tier,b in enumerate(boxes):
            roof+=float(self.T.height[b])
            if roof>self.sight_limit(page,tier,b)+1e-8:
                return False
        return True
