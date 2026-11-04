"""Full-stack box/slot LP adapter with immovable arrival pairs.

Page extraction follows C4 pages_base.build_pages. No live lab module is loaded.
All integer indices are the native feasibility engine's original indices.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from math import fsum, hypot, inf, sqrt
import numpy as np
from stowage.conventions import cells_of_slot, parse_slot


@dataclass(frozen=True)
class Page:
    kind: str
    stacks: tuple
    slots: tuple
    limit_kg: float
    hmax: float
    deck: bool
    cover: object


def build_pages(T):
    twenty, pairs = {}, defaultdict(dict)
    for s in range(len(T.slot_ids)):
        if T.slot_n[s] == 1:
            twenty[int(T.slot_stacks[s,0]), int(T.slot_k[s,0])] = s
        elif T.slot_k[s,0] == T.slot_k[s,1]:
            pairs[int(T.slot_stacks[s,0]),int(T.slot_stacks[s,1])][int(T.slot_k[s,0])] = s
    pages = []
    for j in range(len(T.stack_ids)):
        slots = []
        while (j,len(slots)) in twenty:
            slots.append(twenty[j,len(slots)])
        if slots:
            pages.append(Page('20',(j,),tuple(slots),float(T.stack_lim_20[j]),float(T.stack_hmax[j]),bool(T.stack_deck[j]),T.stack_cover[j]))
    for (a,b), found in sorted(pairs.items()):
        slots=[]
        while len(slots) in found:
            slots.append(found[len(slots)])
        if slots:
            pages.append(Page('long',(a,b),tuple(slots),float(min(T.stack_lim_40[a],T.stack_lim_40[b])),
                              float(min(T.stack_hmax[a],T.stack_hmax[b])),bool(T.stack_deck[a]),T.stack_cover[a]))
    return pages


def roof_cap(bounds, frame, margin):
    """Copied C4 sight cone restriction at a supplied native waterplane."""
    eye, normal, along, across = (frame[k] for k in ('eye','normal','along','across'))
    dot=lambda a,b:fsum(x*y for x,y in zip(a,b))
    height=dot(normal,eye)-normal[2]*frame['aft_draft_m']
    distance=frame['bow_range_m']
    if height<=0 or normal[2]<=0 or distance*normal[2]<=height*hypot(along[2],across[2]):
        raise ValueError('invalid sight waterplane')
    cap=inf
    for x in bounds[:2]:
        for y in bounds[2:]:
            def excess(z):
                p=(x-eye[0],y-eye[1],z-eye[2])
                return height*hypot(dot(along,p),dot(across,p))+distance*dot(normal,p)
            lo,hi=-1000.,1000.
            for _ in range(44):
                mid=(lo+hi)/2
                if excess(mid)<=0:lo=mid
                else:hi=mid
            cap=min(cap,lo)
    return cap-max(0.,margin)


class Model:
    def __init__(self, problem, branch=None, *, sight_conditions=(), sight_reference_ship=None,
                 sight_draft_m=None, sight_roof_margin_m=.15, **kwargs):
        self.p,self.T,self.branch=problem,problem.engine.T,branch
        T=self.T
        self.arrival={p.container_id:p.slot for p in problem.instance.onboard.placements}
        self.fixed_ids=set(self.arrival)
        names=list(dict.fromkeys(list(problem.instance.load_list)+list(self.arrival)))
        self.boxes=[T.box_index[b] for b in names]
        self.fixed_pairs={T.box_index[b]:int(T.slot_index[q]) for b,q in self.arrival.items()}
        self.free_boxes=[b for b in self.boxes if b not in self.fixed_pairs]
        self.pages=build_pages(T)
        self.stacks=sorted({s for page in self.pages for s in page.stacks})
        self.pod={b:problem.pod(T.box_ids[b]) for b in self.boxes}
        self.ports=sorted(set(self.pod.values()))
        self.heavy={b:T.e1_threshold is not None and T.mass[b]>=T.e1_threshold for b in self.boxes}
        self.order=sorted(self.boxes,key=lambda b:(bool(T.closes_stack[b]),not self.heavy[b],-int(T.rank[b]),-self.pod[b],-float(T.mass[b]),b))
        self.sight_roof_margin_m=float(sight_roof_margin_m)
        self.sight_frames=[];self.sight_cache={}
        conditions=list(sight_conditions)
        if sight_reference_ship is not None:
            from stowage.checker.visibility import _loading_condition
            c=_loading_condition(problem.instance,sight_reference_ship)
            conditions.append(dict(draught_aft_m=c.t_aft_m,draught_fwd_m=c.t_fwd_m,list_rad=c.heel_rad))
        if sight_draft_m is not None:
            conditions.append(dict(draught_aft_m=sight_draft_m,draught_fwd_m=sight_draft_m,list_rad=0.))
        if conditions:self._sight_frames(conditions)
        self.static=T.static.copy()
        occupied={int(c) for q in self.fixed_pairs.values() for c in T.slot_cells[q] if c>=0}
        blocked=[s for s in range(len(T.slot_ids)) if occupied.intersection(int(c) for c in T.slot_cells[s] if c>=0)]
        for b in self.free_boxes:self.static[b,blocked]=False
        # Kept cargo is given occupancy, even if current unary screening differs.
        for b,q in self.fixed_pairs.items():
            self.static[b,:]=False;self.static[b,q]=True
        self.heat_clearance=self._heat_clearance()
        self.heat_slots_cache={}
        # Heat has true-support-height dependence. A slot survives only if some
        # admissible lower-height sum is heat-safe; exact legal() checks the
        # chosen sum. Never restore an all-denied mask.
        heights=sorted({float(problem.boxes[T.box_ids[b]].height_m) for b in self.boxes})
        sums={0.}
        possible={0:(0.,)}
        max_tiers=max((len(p.slots) for p in self.pages),default=0)
        for tier in range(1,max_tiers):
            sums={round(z+h,6) for z in sums for h in heights}
            possible[tier]=tuple(sums)
        for page in self.pages:
            base=max(float(T.stack_base_z[j]) for j in page.stacks)
            for tier,q in enumerate(page.slots):
                for b in self.free_boxes:
                    if not self.static[b,q]:continue
                    if T.heat[b] and not any(self.heat_ok(page,tier,b,base+z) for z in possible[tier]):
                        self.static[b,q]=False
                    if self.sight_frames and page.deck:
                        minimum=min(heights) if heights else 0.
                        if base+tier*minimum+float(T.height[b])>self.sight_limit(page,tier,b)+1e-8:
                            self.static[b,q]=False

    def _heat_clearance(self):
        from stowage.checker.rules_special import _clearance_parameter
        raw=_clearance_parameter(self.p.instance,'expert.e8.away_from_heat','minimum_clearance','heat_clearance_m')
        return float(raw) if isinstance(raw,(int,float)) and raw>=0 else None

    def heat_ok(self,page,tier,b,floor):
        if not self.T.heat[b]:return True
        if self.heat_clearance is None:return False
        box=self.p.boxes[self.T.box_ids[b]]
        heat_ids={h.tank_id if h.tank_id is not None else h.id for h in self.p.instance.vessel.heat_sources if h.kind=='heated_tank'}
        if any(t.heated and t.id not in heat_ids for t in self.p.instance.vessel.tanks):return False
        x,y=self._slot_xy(page,tier)
        o=box.oog
        length={20:6.058,40:12.192,45:13.716}[box.length_ft]
        env=((x-length/2-(o.over_length_aft_m if o else 0),x+length/2+(o.over_length_fore_m if o else 0)),
             (y-1.219-(o.over_width_starboard_m if o else 0),y+1.219+(o.over_width_port_m if o else 0)),
             (floor,floor+box.height_m+(o.over_height_m if o else 0)))
        for heat in self.p.instance.vessel.heat_sources:
            hb=((heat.x_min_m,heat.x_max_m),(heat.y_min_m,heat.y_max_m),(heat.z_min_m,heat.z_max_m))
            overlap=all(min(a[1],c[1])-max(a[0],c[0])>1e-9 for a,c in zip(env,hb))
            distance=sqrt(sum(max(a[0]-c[1],c[0]-a[1],0.)**2 for a,c in zip(env,hb)))
            if overlap or distance+1e-9<self.heat_clearance:return False
        return True

    def _slot_xy(self,page,tier):
        cells=[self.p.cells[self.T.cell_ids[int(c)]] for c in self.T.slot_cells[page.slots[tier]] if c>=0]
        return fsum(c.lcg_m for c in cells)/len(cells),fsum(c.tcg_m for c in cells)/len(cells)

    def legal(self,page,boxes,*,any_port=False):
        T=self.T
        if len(boxes)>len(page.slots):return False
        # A trial prefix must contain each fixed tier it reaches, in place.
        for k,b in enumerate(boxes):
            q=page.slots[k]
            if b in self.fixed_pairs and self.fixed_pairs[b]!=q:return False
            owners=[a for a,s in self.fixed_pairs.items() if s==q]
            if owners and b!=owners[0]:return False
            if not self.static[b,q]:return False
        if any(bool(T.closes_stack[b]) for b in boxes[:-1]):return False
        if sum(bool(T.is_dg[b]) for b in boxes)>1:return False
        if sum(float(T.share[b]) for b in boxes)>page.limit_kg+1e-6:return False
        if sum(float(T.height[b]) for b in boxes)>page.hmax+1e-9:return False
        roof=max(float(T.stack_base_z[j]) for j in page.stacks)
        ranks=[];heavy=True
        for k,b in enumerate(boxes):
            if ranks and int(T.rank[b])>min(ranks)+T.e2_tol:return False
            if self.heavy[b] and not heavy:return False
            ranks.append(int(T.rank[b]));heavy=heavy and self.heavy[b]
            if not self.heat_ok(page,k,b,roof):return False
            roof+=float(T.height[b])
            if page.deck and self.sight_frames and roof>self.sight_limit(page,k,b)+1e-8:return False
        return True

    def _sight_frames(self,conditions):
        from pathlib import PurePosixPath
        from stowage.checker.visibility import _geometry,CONNING_EYE_SETBACK_M
        v=self.p.instance.vessel;bridge,hull=v.bridge,v.hull
        if bridge is None or hull is None:raise ValueError('bridge/hull required for sight restriction')
        name=PurePosixPath(hull.hull_offsets_ref.split('#')[0]).stem
        geometry,_=_geometry(name)
        eye=(bridge.x_m-CONNING_EYE_SETBACK_M,0.,bridge.eye_z_m)
        for c in conditions:
            aft,fwd,heel=float(c['draught_aft_m']),float(c['draught_fwd_m']),float(c.get('list_rad',0.))
            n=tuple(float(v) for v in geometry.waterplane_normal(aft,fwd,heel))
            raw=(1-n[0]*n[0],-n[1]*n[0],-n[2]*n[0]);norm=sqrt(sum(v*v for v in raw))
            along=tuple(v/norm for v in raw)
            across=(n[1]*along[2]-n[2]*along[1],n[2]*along[0]-n[0]*along[2],n[0]*along[1]-n[1]*along[0])
            bow_z=aft+(fwd-aft)*bridge.bow_x_m/hull.lpp_m
            bow=fsum(along[i]*(a-eye[i]) for i,a in enumerate((bridge.bow_x_m,0.,bow_z)))
            self.sight_frames.append(dict(eye=eye,normal=n,along=along,across=across,aft_draft_m=aft,
                                          bow_range_m=bow+min(2*hull.loa_m,500.)))

    def sight_limit(self,page,tier,b):
        if not page.deck or not self.sight_frames:return inf
        box=self.p.boxes[self.T.box_ids[b]];x,y=self._slot_xy(page,tier);o=box.oog
        length={20:6.058,40:12.192,45:13.716}[box.length_ft]
        bounds=(x-length/2-(o.over_length_aft_m if o else 0),x+length/2+(o.over_length_fore_m if o else 0),
                y-1.219-(o.over_width_starboard_m if o else 0),y+1.219+(o.over_width_port_m if o else 0))
        if bounds not in self.sight_cache:
            self.sight_cache[bounds]=min(roof_cap(bounds,f,self.sight_roof_margin_m) for f in self.sight_frames)
        return self.sight_cache[bounds]

    def clear_oog(self,assignment):
        # The recorded cohort has no OOG. Moving fixed cargo in an optional
        # generic repair would violate this adapter's keep decision.
        return dict(assignment)
